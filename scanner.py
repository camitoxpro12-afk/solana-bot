"""
Escaner de nuevos tokens en Solana.
Fuentes: pump.fun API + DexScreener token profiles
"""

import asyncio
import time
from typing import List, Dict, Optional, Set, Callable, Awaitable
import httpx

import config
from database import was_token_seen, mark_token_seen, add_log, is_blacklisted, get_favorites
from analyzer import analyze_token
from news import get_token_sentiment_boost


_known_addresses: Set[str] = set()
_last_analyzed: Dict[str, float] = {}   # addr -> timestamp (para re-evaluar trending/top)

# No analizar SOL ni stablecoins como si fueran memecoins
_SKIP_MINTS = {
    config.SOL_MINT,
    config.WSOL_MINT,
    config.USDC_MINT,
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _gecko_metrics(attrs: Dict) -> Dict:
    price_change = attrs.get("price_change_percentage") or {}
    volume = attrs.get("volume_usd") or {}
    txns = attrs.get("transactions") or {}
    h1 = txns.get("h1") or {}
    h24 = txns.get("h24") or {}
    buys_1h = _num(h1.get("buys"))
    sells_1h = _num(h1.get("sells"))
    txns_1h = int(buys_1h + sells_1h)
    return {
        "liquidity_usd": _num(attrs.get("reserve_in_usd")),
        "volume_1h": _num(volume.get("h1")),
        "volume_24h": _num(volume.get("h24")),
        "change_1h": _num(price_change.get("h1")),
        "change_6h": _num(price_change.get("h6")),
        "change_24h": _num(price_change.get("h24")),
        "buys_1h": int(buys_1h),
        "sells_1h": int(sells_1h),
        "txns_1h": txns_1h,
        "buy_ratio_1h": (buys_1h / txns_1h) if txns_1h > 0 else 0.0,
        "txns_24h": int(_num(h24.get("buys")) + _num(h24.get("sells"))),
    }


def _passes_market_prefilter(token: Dict) -> tuple[bool, str]:
    """Filtro barato antes de RugCheck/IA: solo aplica cuando ya tenemos metricas."""
    if token.get("category") == "favorite":
        return True, ""
    m = token.get("metrics") or {}
    if not m:
        return True, ""
    if m.get("liquidity_usd", 0) < config.SCAN_MIN_LIQUIDITY_USD:
        return False, f"liquidez ${m.get('liquidity_usd', 0):,.0f}"
    if m.get("volume_1h", 0) < config.SCAN_MIN_VOLUME_1H_USD:
        return False, f"volumen 1h ${m.get('volume_1h', 0):,.0f}"
    if m.get("txns_1h", 0) < config.SCAN_MIN_TXNS_1H:
        return False, f"pocas transacciones 1h ({m.get('txns_1h', 0)})"
    if m.get("change_24h", 0) < config.SCAN_MIN_24H_CHANGE_PCT:
        return False, f"24h {m.get('change_24h', 0):+.0f}%"
    if m.get("change_6h", 0) < config.SCAN_MIN_6H_CHANGE_PCT:
        return False, f"6h {m.get('change_6h', 0):+.0f}%"
    if m.get("change_1h", 0) > config.SCAN_MAX_1H_CHANGE_PCT:
        return False, f"1h +{m.get('change_1h', 0):.0f}% (pump demasiado vertical)"
    if m.get("buy_ratio_1h", 0) < config.SCAN_MIN_BUY_RATIO_1H:
        return False, f"presion compradora baja ({m.get('buy_ratio_1h', 0)*100:.0f}%)"
    return True, ""


def _candidate_quality(token: Dict) -> float:
    """Ordena primero las monedas con mercado real y crecimiento sostenido."""
    m = token.get("metrics") or {}
    if not m:
        return 0.0
    return (
        min(m.get("liquidity_usd", 0), 500_000) / 10_000
        + min(m.get("volume_1h", 0), 500_000) / 20_000
        + min(m.get("txns_1h", 0), 2000) / 40
        + max(-50, min(m.get("change_24h", 0), 300)) / 5
        + max(-50, min(m.get("change_6h", 0), 150)) / 6
    )


async def _fetch_pumpfun_new() -> List[Dict]:
    """Latest tokens from pump.fun sorted by creation time"""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                f"{config.PUMPFUN_API}/coins",
                params={"offset": 0, "limit": 50, "sort": "created_timestamp", "order": "DESC"},
                headers={"User-Agent": "Mozilla/5.0"}
            )
            if r.status_code == 200:
                data = r.json()
                return [
                    {
                        "address": c.get("mint", ""),
                        "name": c.get("name", ""),
                        "symbol": c.get("symbol", ""),
                        "source": "pump.fun",
                        "created_at_ms": c.get("created_timestamp", 0),
                    }
                    for c in data
                    if c.get("mint")
                ]
    except Exception as e:
        add_log("WARNING", f"Error fetch pump.fun: {e}")
    return []


async def _fetch_dexscreener_new() -> List[Dict]:
    """Latest token profiles added to DexScreener (Solana only)"""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                f"{config.DEXSCREENER_URL}/token-profiles/latest/v1",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            if r.status_code == 200:
                data = r.json() if isinstance(r.json(), list) else []
                return [
                    {
                        "address": t.get("tokenAddress", ""),
                        "name": t.get("description", "")[:30] or "Unknown",
                        "symbol": "",
                        "source": "dexscreener",
                        "created_at_ms": 0,
                    }
                    for t in data
                    if t.get("chainId") == "solana" and t.get("tokenAddress")
                ]
    except Exception as e:
        add_log("WARNING", f"Error fetch DexScreener profiles: {e}")
    return []


async def _fetch_geckoterminal(path: str, category: str) -> List[Dict]:
    """Tokens en tendencia / top de Solana desde GeckoTerminal (gratis, sin key)."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{config.GECKOTERMINAL_URL}/networks/solana/{path}",
                headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
            )
            if r.status_code == 200:
                data = r.json().get("data", []) or []
                out = []
                for pool in data[:30]:
                    attrs = pool.get("attributes", {}) or {}
                    rel = (pool.get("relationships", {}) or {}).get("base_token", {}).get("data", {}) or {}
                    tid = (rel.get("id") or "").replace("solana_", "")
                    if not tid or tid in _SKIP_MINTS:
                        continue
                    name = (attrs.get("name") or "").split("/")[0].strip()
                    out.append({
                        "address": tid, "name": name, "symbol": name,
                        "source": "geckoterminal", "category": category, "created_at_ms": 0,
                        "metrics": _gecko_metrics(attrs),
                    })
                return out
    except Exception as e:
        add_log("WARNING", f"Error fetch GeckoTerminal {path}: {e}")
    return []


async def _fetch_trending() -> List[Dict]:
    return await _fetch_geckoterminal("trending_pools", "trending")


async def _fetch_top() -> List[Dict]:
    return await _fetch_geckoterminal("pools?sort=h24_volume_usd_desc", "top")


async def _fetch_pumpfun_top() -> List[Dict]:
    """Tokens de pump.fun mas grandes por market cap (menos spam que los nuevos)."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                f"{config.PUMPFUN_API}/coins",
                params={"offset": 0, "limit": 30, "sort": "market_cap", "order": "DESC", "includeNsfw": "false"},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if r.status_code == 200:
                return [
                    {"address": c.get("mint", ""), "name": c.get("name", ""),
                     "symbol": c.get("symbol", ""), "source": "pump.fun",
                     "category": "top", "created_at_ms": c.get("created_timestamp", 0)}
                    for c in r.json() if c.get("mint")
                ]
    except Exception as e:
        add_log("WARNING", f"Error fetch pump.fun top: {e}")
    return []


async def scan_new_tokens(
    on_token_found: Callable[[Dict], Awaitable[None]],
    on_buy_signal: Callable[[object], Awaitable[None]],
):
    """
    Main scan loop iteration.
    - Fetches new tokens from pump.fun and DexScreener
    - Filters already seen tokens
    - Analyzes each new token
    - Calls on_buy_signal if verdict is 'buy'
    """
    # Construye la lista de fuentes segun la configuracion
    fetchers = []
    if config.SCAN_TRENDING:
        fetchers.append(_fetch_trending())
    if config.SCAN_TOP:
        fetchers.append(_fetch_top())
    if config.SCAN_PUMPFUN_TOP:
        fetchers.append(_fetch_pumpfun_top())
    if config.SCAN_NEW:
        fetchers.append(_fetch_pumpfun_new())
        fetchers.append(_fetch_dexscreener_new())

    results = await asyncio.gather(*fetchers, return_exceptions=True) if fetchers else []

    # Combina y deduplica por direccion (prioridad por orden: trending > top > new)
    seen_this_scan: Set[str] = set()
    candidates = []
    for res in results:
        if not isinstance(res, list):
            continue
        for token in res:
            addr = token.get("address", "")
            if addr and addr not in seen_this_scan and addr not in _SKIP_MINTS:
                seen_this_scan.add(addr)
                candidates.append(token)

    # FAVORITAS: monedas que ya ganaron. Se re-analizan SIEMPRE (aunque no esten en
    # tendencia ahora) para volver a entrar en su proxima bajada (re-trade).
    for fav in get_favorites(limit=20):
        addr = fav.get("address", "")
        if addr and addr not in seen_this_scan and addr not in _SKIP_MINTS and not is_blacklisted(addr):
            seen_this_scan.add(addr)
            candidates.append({
                "address": addr,
                "name": fav.get("name") or fav.get("symbol") or "",
                "symbol": fav.get("symbol", ""),
                "source": "favorito", "category": "favorite", "created_at_ms": 0,
            })

    kept = []
    prefiltered = 0
    for token in candidates:
        ok, reason = _passes_market_prefilter(token)
        if ok:
            kept.append(token)
        else:
            prefiltered += 1
    if prefiltered:
        add_log("INFO", f"Prefiltro calidad: {prefiltered} tokens sin mercado fuerte omitidos antes del analisis")
    candidates = sorted(kept, key=_candidate_quality, reverse=True)

    new_count = 0
    for token in candidates:
        addr = token["address"]
        category = token.get("category", "new")

        # LISTA NEGRA: monedas que ya hicieron rug pull. NUNCA se vuelven a comprar.
        if is_blacklisted(addr):
            continue

        # Nuevas: analizar una sola vez. Trending/top/favoritas: re-evaluar con cooldown.
        if category == "new":
            if addr in _known_addresses or was_token_seen(addr):
                continue
            _known_addresses.add(addr)
        else:
            rescan_min = (config.FAVORITE_RESCAN_MINUTES if category == "favorite"
                          else config.RESCAN_TRENDING_MINUTES)
            if (time.time() - _last_analyzed.get(addr, 0)) < rescan_min * 60:
                continue
            _last_analyzed[addr] = time.time()

        new_count += 1

        cat_tag = {"trending": "TREND", "top": "TOP", "new": "NEW", "favorite": "FAV"}.get(category, category)
        add_log("INFO", f"Analizando [{cat_tag}]: {token.get('name', addr[:8])} ({addr[:8]}...)")

        # Notify dashboard that we're analyzing this token
        await on_token_found({
            "address": addr,
            "name": token.get("name", ""),
            "symbol": token.get("symbol", ""),
            "source": token.get("source", ""),
            "category": category,
            "status": "analyzing",
        })

        # Run full analysis
        try:
            news_sentiment = get_token_sentiment_boost(
                token.get("name", ""), token.get("symbol", "")
            )
            analysis = await analyze_token(addr, news_sentiment, category=category)
        except Exception as e:
            add_log("ERROR", f"Error analizando {addr[:8]}: {e}")
            continue

        if not analysis:
            add_log("INFO", f"Sin datos de mercado: {addr[:8]}... - omitiendo")
            continue

        scores = analysis.scores
        total_score = scores.total if scores else 0
        verdict = analysis.verdict

        # Persist to DB
        mark_token_seen(
            addr, analysis.name, analysis.symbol,
            total_score, verdict,
            {
                "liquidity": analysis.liquidity_usd,
                "age_minutes": analysis.age_minutes,
                "price_usd": analysis.price_usd,
                "risks": analysis.rugcheck_risks[:3],
                "reason": analysis.reason,
                "image_url": analysis.image_url,
                "category": category,
            }
        )

        # Notify dashboard with full analysis
        await on_token_found({
            "address": addr,
            "name": analysis.name,
            "symbol": analysis.symbol,
            "source": token.get("source", ""),
            "score": round(total_score, 1),
            "verdict": verdict,
            "reason": analysis.reason,
            "liquidity_usd": round(analysis.liquidity_usd),
            "price_usd": analysis.price_usd,
            "price_change_1h": analysis.price_change_1h,
            "image_url": analysis.image_url,
            "category": category,
            "scores": scores.model_dump() if scores else {},
            "status": "analyzed",
        })

        if verdict == "scam":
            add_log("WARNING", f"SCAM detectado: {analysis.name} ({addr[:8]}...) | {analysis.reason}")
        elif verdict == "buy":
            add_log("INFO", f"SENAL DE COMPRA: {analysis.name} {analysis.symbol} | Score: {total_score:.1f} | {analysis.reason}")
            await on_buy_signal(analysis)
        else:
            add_log("INFO", f"Omitido: {analysis.name} | Score: {total_score:.1f} | {analysis.reason}")

        # Small delay between analyses to avoid rate limiting
        await asyncio.sleep(1.5)

    # Latido: se registra SIEMPRE para que se vea que el escaner sigue vivo
    add_log("INFO", f"🔍 Escaneo completo: {new_count} monedas nuevas analizadas | "
                    f"{len(_last_analyzed)} en seguimiento (re-evaluo cada {config.RESCAN_TRENDING_MINUTES} min)")


async def scanner_loop(
    on_token_found: Callable,
    on_buy_signal: Callable,
    stop_event: asyncio.Event,
):
    """Main scanner loop, runs until stop_event is set"""
    add_log("INFO", "Scanner iniciado - buscando nuevos tokens en Solana...")
    while not stop_event.is_set():
        try:
            await scan_new_tokens(on_token_found, on_buy_signal)
        except Exception as e:
            add_log("ERROR", f"Error en scanner loop: {e}")
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=config.SCAN_INTERVAL
            )
        except asyncio.TimeoutError:
            pass
