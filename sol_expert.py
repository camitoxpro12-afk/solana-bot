"""
Analista Experto de Solana.

Reune TODOS los datos publicos disponibles de SOL (precio, tecnicos, market cap,
volumen, distancia al ATH, TVL de DeFi, yields/rendimientos, noticias, miedo/codicia)
y se los pasa a la IA para que produzca una conclusion experta: comprar/mantener/
vender/esperar, horizonte temporal, razones y riesgos.

Honesto: nadie predice el futuro. Es un analisis probabilistico, no una promesa.
Fuentes (gratis, sin key): CoinGecko, DefiLlama (TVL + yields), alternative.me, RSS.
"""

import time
from typing import Optional, Dict
import httpx

import config

_cache = {"data": None, "ts": 0.0}
_computing = False
TTL = 1800  # 30 min (el analisis de IA es relativamente caro/lento)


async def _get(client: httpx.AsyncClient, url: str, timeout: int = 15, **kw):
    try:
        r = await client.get(url, timeout=timeout, **kw)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def _fmt_b(v):
    try:
        return f"{float(v) / 1e9:.2f}B"
    except (TypeError, ValueError):
        return "?"


def _fmt_m(v):
    try:
        return f"{float(v) / 1e6:.0f}M"
    except (TypeError, ValueError):
        return "?"


async def _gather_extra() -> Dict:
    out = {}
    async with httpx.AsyncClient() as client:
        cg = await _get(
            client, f"{config.COINGECKO_URL}/coins/solana",
            params={"localization": "false", "tickers": "false", "market_data": "true",
                    "community_data": "false", "developer_data": "false"},
        )
        if cg:
            md = cg.get("market_data", {}) or {}
            out["rank"] = cg.get("market_cap_rank")
            out["mcap"] = (md.get("market_cap") or {}).get("usd")
            out["volume"] = (md.get("total_volume") or {}).get("usd")
            out["ath_change_pct"] = (md.get("ath_change_percentage") or {}).get("usd")
            out["chg_30d"] = md.get("price_change_percentage_30d")
            out["chg_1y"] = md.get("price_change_percentage_1y")

        chains = await _get(client, "https://api.llama.fi/v2/chains")
        if isinstance(chains, list):
            sol = next((c for c in chains if c.get("name") == "Solana"), None)
            if sol:
                out["tvl"] = sol.get("tvl")

        try:
            yld = await _get(client, "https://yields.llama.fi/pools", timeout=30)
            pools = [p for p in (yld or {}).get("data", [])
                     if p.get("chain") == "Solana" and (p.get("tvlUsd") or 0) > 1_000_000]
            pools.sort(key=lambda p: p.get("tvlUsd", 0), reverse=True)
            out["yields"] = [
                {"project": p.get("project"), "symbol": p.get("symbol"),
                 "apy": round(p.get("apy") or 0, 1), "tvl": int(p.get("tvlUsd") or 0)}
                for p in pools[:6]
            ]
        except Exception:
            out["yields"] = []
    return out


def _build_dossier(market: Dict, extra: Dict, headlines: list) -> str:
    lines = ["=== DOSSIER COMPLETO DE SOLANA (SOL) ==="]
    lines.append(f"Precio: ${market.get('price_usd')} (rank #{extra.get('rank')} por capitalizacion)")
    lines.append(f"Cambio: 24h {market.get('change_24h')}% | 7d {market.get('change_7d')}% | 30d {extra.get('chg_30d')}% | 1 año {extra.get('chg_1y')}%")
    lines.append(f"Market cap: ${_fmt_b(extra.get('mcap'))} | Volumen 24h: ${_fmt_b(extra.get('volume'))}")
    lines.append(f"Distancia al maximo historico (ATH): {extra.get('ath_change_pct')}% (negativo = por debajo del ATH)")
    lines.append(f"Medias moviles: SMA7 ${market.get('sma7')}, SMA30 ${market.get('sma30')} | RSI(14): {market.get('rsi14')}")
    lines.append(f"Tendencia tecnica (0-100): {market.get('signal_score')} ({market.get('signal_label')})")
    lines.append(f"Indice miedo/codicia: {market.get('fear_greed')} ({market.get('fear_greed_label')})")
    lines.append(f"TVL DeFi en Solana: ${_fmt_b(extra.get('tvl'))}")
    if extra.get("yields"):
        lines.append("Yields/rendimientos top en Solana (APY actual):")
        for y in extra["yields"]:
            lines.append(f"  - {y['project']} {y['symbol']}: {y['apy']}% APY (TVL ${_fmt_m(y['tvl'])})")
    if headlines:
        lines.append("Noticias recientes:")
        for h in headlines[:8]:
            lines.append(f"  - {h}")
    lines.append("\nDa tu analisis experto de SOL con recomendacion y horizonte.")
    return "\n".join(lines)


async def get_sol_expert(log_fn=None) -> Dict:
    """Devuelve el analisis experto (cacheado 30 min). Evita computos concurrentes."""
    global _computing
    now = time.time()
    if _cache["data"] and (now - _cache["ts"]) < TTL:
        return _cache["data"]
    if _computing:
        return _cache["data"] or {"report": None, "computing": True}

    _computing = True
    try:
        import sol_market
        import news
        import llm_analyst
        market = await sol_market.get_sol_market(with_llm=False)
        extra = await _gather_extra()
        headlines = [n.get("title", "") for n in news.get_recent_news()[:8]]
        dossier = _build_dossier(market, extra, headlines)
        report = await llm_analyst.sol_expert_analysis(dossier, log_fn)
        result = {
            "report": report,
            "tvl": extra.get("tvl"),
            "mcap": extra.get("mcap"),
            "rank": extra.get("rank"),
            "ath_change_pct": extra.get("ath_change_pct"),
            "chg_1y": extra.get("chg_1y"),
            "yields": extra.get("yields", []),
            "updated_ts": time.strftime("%H:%M:%S"),
        }
        if report:  # solo cachear si la IA respondio
            _cache["data"] = result
            _cache["ts"] = now
        return result
    finally:
        _computing = False
