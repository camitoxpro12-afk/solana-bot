"""
Escaner de nuevos tokens en Solana.
Fuentes: pump.fun API + DexScreener token profiles
"""

import asyncio
import time
from typing import List, Dict, Optional, Set, Callable, Awaitable
import httpx

import config
from database import was_token_seen, mark_token_seen, add_log
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
                    rel = (pool.get("relationships", {}) or {}).get("base_token", {}).get("data", {}) or {}
                    tid = (rel.get("id") or "").replace("solana_", "")
                    if not tid or tid in _SKIP_MINTS:
                        continue
                    name = (pool.get("attributes", {}).get("name") or "").split("/")[0].strip()
                    out.append({
                        "address": tid, "name": name, "symbol": name,
                        "source": "geckoterminal", "category": category, "created_at_ms": 0,
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

    new_count = 0
    for token in candidates:
        addr = token["address"]
        category = token.get("category", "new")

        # Nuevas: analizar una sola vez. Trending/top: re-evaluar con cooldown.
        if category == "new":
            if addr in _known_addresses or was_token_seen(addr):
                continue
            _known_addresses.add(addr)
        else:
            if (time.time() - _last_analyzed.get(addr, 0)) < config.RESCAN_TRENDING_MINUTES * 60:
                continue
            _last_analyzed[addr] = time.time()

        new_count += 1

        cat_tag = {"trending": "TREND", "top": "TOP", "new": "NEW"}.get(category, category)
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

    if new_count > 0:
        add_log("INFO", f"Scan completo: {new_count} nuevos tokens analizados")


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
