"""
Motor de analisis de tokens en multiples capas.

Puntuacion maxima: 100 pts
  Seguridad (40):  rugcheck(20) + mint_authority(10) + freeze_authority(10)
  Mercado (35):    liquidez(15) + distribucion_holders(10) + volumen_mc(10)
  Momentum (15):   tendencia_precio(8) + tendencia_volumen(7)
  Social (10):     noticias(5) + actividad_social(5)
"""

import asyncio
import time
from typing import Optional, Dict, Any
import httpx

import config
from models import TokenAnalysis, TokenScores
from database import get_weights


async def _get(client: httpx.AsyncClient, url: str, **kwargs) -> Optional[Dict]:
    try:
        r = await client.get(url, timeout=8, **kwargs)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


async def fetch_dexscreener(token_address: str) -> Optional[Dict]:
    async with httpx.AsyncClient() as client:
        data = await _get(client, f"{config.DEXSCREENER_URL}/latest/dex/tokens/{token_address}")
    if not data:
        return None
    pairs = data.get("pairs") or []
    # Pick the Solana pair with highest liquidity
    sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
    if not sol_pairs:
        return None
    return max(sol_pairs, key=lambda p: p.get("liquidity", {}).get("usd", 0) or 0)


async def fetch_rugcheck(token_address: str) -> Optional[Dict]:
    async with httpx.AsyncClient() as client:
        return await _get(client, f"{config.RUGCHECK_URL}/tokens/{token_address}/report")


async def fetch_pumpfun_token(token_address: str) -> Optional[Dict]:
    async with httpx.AsyncClient() as client:
        return await _get(client, f"{config.PUMPFUN_API}/coins/{token_address}")


def _score_rugcheck(report: Optional[Dict]) -> tuple[float, list]:
    """Returns (score 0-20, list_of_risks)"""
    if not report:
        return 5.0, ["No se pudo obtener reporte de rugcheck"]

    risk_normalized = report.get("score_normalised", 100)
    risks = report.get("risks", [])
    risk_names = [r.get("name", "") for r in risks]

    # Convert rugcheck risk score (0=safe, 100=risky) to our score (0=bad, 20=good)
    score = max(0.0, 20.0 - (risk_normalized / 100 * 20.0))
    return round(score, 1), risk_names


def _score_mint_freeze(report: Optional[Dict], pair: Optional[Dict]) -> tuple[float, float]:
    """Returns (mint_score 0-10, freeze_score 0-10)"""
    mint_score = 5.0
    freeze_score = 5.0

    if report:
        risks = [r.get("name", "").lower() for r in report.get("risks", [])]
        # Mint authority check
        if any("mint" in r and "enabled" in r for r in risks):
            mint_score = 0.0
        elif any("mint" in r and ("disabled" in r or "revoked" in r) for r in risks):
            mint_score = 10.0
        else:
            mint_authority = report.get("mintAuthority")
            mint_score = 10.0 if (mint_authority is None or mint_authority == "null") else 2.0

        # Freeze authority check
        if any("freeze" in r and "enabled" in r for r in risks):
            freeze_score = 0.0
        elif any("freeze" in r and ("disabled" in r or "revoked" in r) for r in risks):
            freeze_score = 10.0
        else:
            freeze_auth = report.get("freezeAuthority")
            freeze_score = 10.0 if (freeze_auth is None or freeze_auth == "null") else 3.0

    return round(mint_score, 1), round(freeze_score, 1)


def _score_liquidity(liquidity_usd: float) -> float:
    """0-15 pts based on liquidity"""
    if liquidity_usd >= 50_000:
        return 15.0
    if liquidity_usd >= 20_000:
        return 12.0
    if liquidity_usd >= 10_000:
        return 9.0
    if liquidity_usd >= 5_000:
        return 6.0
    if liquidity_usd >= 2_000:
        return 3.0
    return 0.0


def _score_holder_distribution(report: Optional[Dict]) -> tuple[float, float, int]:
    """Returns (score 0-10, top10_pct, holder_count)"""
    if not report:
        return 3.0, 50.0, 0

    top_holders = report.get("topHolders", [])
    # Exclude LP pools and de-duplicate addresses; RugCheck can occasionally
    # repeat holder rows, which otherwise produces impossible totals >100%.
    seen = set()
    non_lp = []
    for h in top_holders:
        if h.get("isLP", False) or h.get("isDex", False):
            continue
        ident = h.get("address") or h.get("owner") or h.get("wallet") or h.get("account")
        if ident and ident in seen:
            continue
        if ident:
            seen.add(ident)
        non_lp.append(h)
    top10_pct = min(100.0, sum((h.get("pct", 0) or 0) for h in non_lp[:10]))

    holder_count = len(top_holders)  # rugcheck doesn't give exact count

    if top10_pct <= 15:
        score = 10.0
    elif top10_pct <= 25:
        score = 8.0
    elif top10_pct <= 35:
        score = 5.0
    elif top10_pct <= 45:
        score = 2.0
    else:
        score = 0.0

    return round(score, 1), round(top10_pct, 1), holder_count


def _score_volume_mc(pair: Optional[Dict]) -> float:
    """0-10 pts: high volume relative to market cap = real activity"""
    if not pair:
        return 1.0
    volume_1h = pair.get("volume", {}).get("h1", 0) or 0
    mc = pair.get("marketCap") or pair.get("fdv") or 1
    if mc == 0:
        return 1.0
    ratio = volume_1h / mc
    if ratio >= 0.5:
        return 10.0
    if ratio >= 0.2:
        return 8.0
    if ratio >= 0.1:
        return 6.0
    if ratio >= 0.05:
        return 3.0
    return 1.0


def _score_price_trend(pair: Optional[Dict]) -> float:
    """0-8 pts: positive price momentum"""
    if not pair:
        return 2.0
    change_1h = pair.get("priceChange", {}).get("h1", 0) or 0
    change_6h = pair.get("priceChange", {}).get("h6", 0) or 0

    score = 2.0  # Neutral base
    if change_1h > 0:
        score += min(3.0, change_1h / 20)  # Up to +3 for 1h gain
    else:
        score += max(-2.0, change_1h / 20)

    if change_6h > 0:
        score += min(3.0, change_6h / 50)  # Up to +3 for 6h gain
    else:
        score += max(-2.0, change_6h / 50)

    return round(max(0.0, min(8.0, score)), 1)


def _score_volume_trend(pair: Optional[Dict]) -> float:
    """0-7 pts: volume growing = interest building"""
    if not pair:
        return 2.0
    txns = pair.get("txns", {})
    buys_1h = txns.get("h1", {}).get("buys", 0) or 0
    sells_1h = txns.get("h1", {}).get("sells", 0) or 0
    total_1h = buys_1h + sells_1h

    if total_1h == 0:
        return 0.0

    buy_ratio = buys_1h / total_1h
    if buy_ratio >= 0.7:
        score = 7.0
    elif buy_ratio >= 0.6:
        score = 5.5
    elif buy_ratio >= 0.5:
        score = 4.0
    elif buy_ratio >= 0.4:
        score = 2.5
    else:
        score = 1.0

    # Bonus for high transaction count (activity)
    if total_1h > 200:
        score = min(7.0, score + 1.0)
    elif total_1h > 100:
        score = min(7.0, score + 0.5)

    return round(score, 1)


def _analyze_dev(report: Optional[Dict]) -> tuple[list, float]:
    """
    Analisis del creador / bundle. Detecta concentracion peligrosa de un solo
    holder e insiders/snipers. Devuelve (banderas, pct_del_mayor_holder).
    """
    flags = []
    if not report:
        return flags, 0.0

    holders = report.get("topHolders", []) or []
    non_lp = [h for h in holders if not h.get("isLP", False) and not h.get("isDex", False)]
    top1_pct = max((h.get("pct", 0) or 0 for h in non_lp), default=0.0)

    if top1_pct > config.MAX_CREATOR_PCT:
        flags.append(f"Un solo holder controla {top1_pct:.1f}% del supply")

    # Redes de insiders (rugcheck a veces las reporta)
    insiders = report.get("insiderNetworks") or []
    if isinstance(insiders, list) and insiders:
        flags.append(f"{len(insiders)} red(es) de insiders detectada(s)")

    # Riesgos que mencionan creador/insider/sniper/bundle
    for r in report.get("risks", []):
        nm = (r.get("name", "") or "").lower()
        if any(k in nm for k in ["insider", "sniper", "bundle", "creator", "hub"]):
            flags.append(r.get("name", ""))

    return flags, round(top1_pct, 1)


def _score_social(pumpfun_data: Optional[Dict]) -> float:
    """0-5 pts from pump.fun activity"""
    if not pumpfun_data:
        return 1.0
    reply_count = pumpfun_data.get("reply_count", 0) or 0
    if reply_count >= 100:
        return 5.0
    if reply_count >= 50:
        return 4.0
    if reply_count >= 20:
        return 3.0
    if reply_count >= 5:
        return 2.0
    return 1.0


async def analyze_token(address: str, news_sentiment: float = 2.5, category: str = "new") -> Optional[TokenAnalysis]:
    """
    Full multi-layer analysis. Returns TokenAnalysis with verdict.
    news_sentiment: 0-5, provided by news module.
    category: 'new' | 'trending' | 'top' - las trending/top son mas flexibles
             (sin filtro de antiguedad, umbral mas bajo) pero igual de estrictas en seguridad.
    """
    # Fetch all data in parallel
    pair, rugcheck, pumpfun = await asyncio.gather(
        fetch_dexscreener(address),
        fetch_rugcheck(address),
        fetch_pumpfun_token(address),
        return_exceptions=True
    )
    # Handle exceptions from gather
    pair = pair if not isinstance(pair, Exception) else None
    rugcheck = rugcheck if not isinstance(rugcheck, Exception) else None
    pumpfun = pumpfun if not isinstance(pumpfun, Exception) else None

    if not pair:
        return None  # No market data = can't trade

    # Extract basic info
    base = pair.get("baseToken", {})
    name = base.get("name", "Unknown")
    symbol = base.get("symbol", "?")
    price_usd = float(pair.get("priceUsd") or 0)
    price_sol = float(pair.get("priceNative") or 0)
    liquidity_usd = pair.get("liquidity", {}).get("usd") or 0
    market_cap = pair.get("marketCap") or pair.get("fdv") or 0
    volume_1h = pair.get("volume", {}).get("h1") or 0
    volume_24h = pair.get("volume", {}).get("h24") or 0
    price_change_1h = pair.get("priceChange", {}).get("h1") or 0
    price_change_24h = pair.get("priceChange", {}).get("h24") or 0

    # Token age
    created_at_ms = pair.get("pairCreatedAt") or 0
    age_minutes = (time.time() * 1000 - created_at_ms) / 60000 if created_at_ms else 9999

    # Imagen/logo del token (DexScreener -> pump.fun como respaldo)
    info = pair.get("info") or {}
    image_url = info.get("imageUrl") or ""
    if not image_url and isinstance(pumpfun, dict):
        image_url = pumpfun.get("image_uri") or ""

    # Apply learned weight multipliers
    weights = get_weights()

    # Compute individual scores
    rug_score, risks = _score_rugcheck(rugcheck)
    mint_score, freeze_score = _score_mint_freeze(rugcheck, pair)
    dist_score, top10_pct, holder_count = _score_holder_distribution(rugcheck)

    # Analisis dev/bundle: anade banderas a la lista de riesgos (visibles + las ve el LLM)
    if config.ENABLE_DEV_ANALYSIS:
        dev_flags, _creator_top1 = _analyze_dev(rugcheck)
        risks = list(risks) + dev_flags
    if (
        not config.ENABLE_TRADING
        and config.PAPER_EXPLORATION_MODE
        and category in ("trending", "top", "favorite")
        and top10_pct > config.MAX_TOP10_PCT
    ):
        risks = list(risks) + [f"Exploracion paper: top10 {top10_pct:.0f}% supera limite live {config.MAX_TOP10_PCT:.0f}%"]
    liq_score = _score_liquidity(float(liquidity_usd))
    vol_mc_score = _score_volume_mc(pair)
    price_trend_score = _score_price_trend(pair)
    vol_trend_score = _score_volume_trend(pair)
    social_score = _score_social(pumpfun)

    # Apply learning weights
    def w(factor: str, base_score: float, max_pts: float) -> float:
        weight = weights.get(factor, 1.0)
        return round(min(max_pts, base_score * weight), 1)

    scores = TokenScores(
        rugcheck=w("rugcheck", rug_score, 20),
        mint_authority=w("mint_authority", mint_score, 10),
        freeze_authority=w("freeze_authority", freeze_score, 10),
        liquidity=w("liquidity", liq_score, 15),
        holder_distribution=w("holder_distribution", dist_score, 10),
        volume_mc_ratio=w("volume_mc_ratio", vol_mc_score, 10),
        price_trend=w("price_trend", price_trend_score, 8),
        volume_trend=w("volume_trend", vol_trend_score, 7),
        news_sentiment=w("news_sentiment", news_sentiment, 5),
        social_score=w("social_score", social_score, 5),
    )
    total = scores.compute_total()

    # Determine verdict
    verdict, reason = _determine_verdict(
        total, float(liquidity_usd), age_minutes, mint_score, freeze_score, risks, category,
        float(price_change_1h), float(price_change_24h), top10_pct
    )

    return TokenAnalysis(
        address=address,
        name=name,
        symbol=symbol,
        price_usd=price_usd,
        price_sol=price_sol,
        liquidity_usd=float(liquidity_usd),
        market_cap=float(market_cap),
        volume_1h=float(volume_1h),
        volume_24h=float(volume_24h),
        price_change_1h=float(price_change_1h),
        price_change_24h=float(price_change_24h),
        holders=holder_count,
        top10_pct=top10_pct,
        mint_authority=(mint_score < 5),
        freeze_authority=(freeze_score < 5),
        age_minutes=age_minutes,
        rugcheck_risks=risks,
        image_url=image_url,
        category=category,
        scores=scores,
        verdict=verdict,
        reason=reason,
    )


def _determine_verdict(
    total_score: float, liquidity: float, age_minutes: float,
    mint_score: float, freeze_score: float, risks: list, category: str = "new",
    price_change_1h: float = 0.0, price_change_24h: float = 0.0, top10_pct: float = 0.0
) -> tuple[str, str]:
    # Descalificadores de SEGURIDAD (siempre estrictos, sin importar la categoria/estrategia)
    if mint_score == 0.0:
        return "scam", "Mint authority activo - pueden crear tokens infinitos"
    if freeze_score == 0.0:
        return "scam", "Freeze authority activo - pueden congelar tu wallet"

    paper_exploration = (
        not config.ENABLE_TRADING
        and config.PAPER_EXPLORATION_MODE
        and category in ("trending", "top", "favorite")
    )
    min_liquidity = config.MIN_LIQUIDITY_USD
    max_top10 = config.MAX_TOP10_PCT
    if paper_exploration:
        min_liquidity = min(min_liquidity, config.PAPER_EXPLORATION_MIN_LIQUIDITY_USD)
        max_top10 = max(max_top10, config.PAPER_EXPLORATION_MAX_TOP10_PCT)

    if liquidity < min_liquidity:
        return "skip", f"Liquidez insuficiente: ${liquidity:,.0f}"
    # ANTI-RUG: si el top 10 de wallets posee demasiado supply, pueden tirar el precio
    # a cero de golpe (rug pull). Es la bandera roja #1 segun los datos reales.
    if top10_pct > max_top10:
        return "scam", f"Holders muy concentrados: top 10 posee {top10_pct:.0f}% (max {max_top10:.0f}%) - riesgo de rug pull"

    # Filtros de antiguedad SOLO para tokens nuevos (las trending/top son establecidas)
    if category == "new":
        if age_minutes < config.MIN_TOKEN_AGE_MINUTES:
            return "skip", f"Token demasiado nuevo: {age_minutes:.0f} min"
        if age_minutes > config.MAX_TOKEN_AGE_HOURS * 60:
            return "skip", f"Token demasiado antiguo: {age_minutes/60:.1f}h"

    # Riesgos criticos (siempre)
    critical_keywords = ["honeypot", "blacklist", "hidden owner", "proxy", "malicious"]
    for risk in risks:
        if any(kw in risk.lower() for kw in critical_keywords):
            return "scam", f"Riesgo critico detectado: {risk}"

    # === ESTRATEGIA "DIP": comprar la bajada de una moneda en tendencia ===
    if config.STRATEGY == "dip":
        if total_score < 40:  # filtro minimo de calidad/seguridad
            return "skip", f"Calidad baja: {total_score:.0f}/100"
        if price_change_24h < config.DIP_MIN_24H_RISE:
            return "skip", f"No esta en tendencia (24h {price_change_24h:+.0f}%, se busca >= +{config.DIP_MIN_24H_RISE:.0f}%)"
        if price_change_1h > config.DIP_MAX_1H:
            return "skip", f"Esperando el retroceso (1h {price_change_1h:+.0f}%, se compra cuando baje <= {config.DIP_MAX_1H:.0f}%)"
        if price_change_1h < config.DIP_MIN_1H:
            return "skip", f"DUMP en curso, no retroceso (1h {price_change_1h:+.0f}% < {config.DIP_MIN_1H:.0f}%) - no compro cuchillos cayendo"
        note = ""
        if paper_exploration and top10_pct > config.MAX_TOP10_PCT:
            note = f" | exploracion paper: top10 {top10_pct:.0f}%"
        return "buy", f"DIP: subio +{price_change_24h:.0f}% en 24h y ahora retrocede {price_change_1h:+.0f}% en 1h - comprando la bajada{note}"

    # === ESTRATEGIA "MOMENTUM" (anterior): comprar cuando sube ===
    min_score = config.MIN_SCORE if category == "new" else config.MIN_SCORE_TRENDING
    cat_label = {"trending": "tendencia", "top": "top volumen", "new": "nueva"}.get(category, category)
    if total_score >= min_score:
        return "buy", f"Puntuacion {total_score:.1f}/100 ({cat_label}) - por encima del umbral {min_score:.0f}"
    else:
        return "skip", f"Puntuacion baja: {total_score:.1f}/100 (minimo {min_score:.0f} para {cat_label})"
