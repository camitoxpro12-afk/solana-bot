"""
Analisis del mercado de SOL (la moneda nativa de Solana).

A diferencia de las memecoins, SOL es un activo solido (no va a hacer rug, tiene
liquidez enorme y un proyecto real). Por eso no necesita los filtros anti-scam;
lo que interesa es leer el MERCADO: tendencia, momentum, sobrecompra/sobreventa
y sentimiento, para responder "¿es buen momento para tener SOL?".

Fuentes (gratis, sin API key):
  - CoinGecko: precio e historico
  - alternative.me: indice Fear & Greed del mercado cripto

Indicadores calculados: SMA7/SMA30 (tendencia), RSI14 (sobrecompra/sobreventa),
cambios 24h/7d/30d. Opcionalmente, Claude da una lectura razonada del mercado.
"""

import time
from typing import Optional, Dict, List
import httpx

import config

_cache = {"data": None, "ts": 0.0}
_llm_cache = {"read": None, "ts": 0.0}
CACHE_TTL = 120        # indicadores: 2 min
LLM_CACHE_TTL = 1800   # lectura de Claude: 30 min (controla coste)


async def _get_json(client: httpx.AsyncClient, url: str, **kwargs) -> Optional[dict]:
    try:
        r = await client.get(url, timeout=10, **kwargs)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def _sma(vals: List[float], n: int) -> Optional[float]:
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def _rsi(vals: List[float], period: int = 14) -> Optional[float]:
    if len(vals) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(-period, 0):
        diff = vals[i] - vals[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 1)


def _to_daily_closes(prices: List[list]) -> List[float]:
    """Convierte [[ts_ms, precio], ...] en cierres diarios (ultimo precio de cada dia)."""
    by_day = {}
    for ts, p in prices:
        by_day[int(ts // 86400000)] = p
    return [by_day[d] for d in sorted(by_day)]


async def _fetch_raw():
    async with httpx.AsyncClient() as client:
        price = await _get_json(
            client, f"{config.COINGECKO_URL}/simple/price",
            params={"ids": "solana", "vs_currencies": "usd,eur", "include_24hr_change": "true"},
        )
        chart = await _get_json(
            client, f"{config.COINGECKO_URL}/coins/solana/market_chart",
            params={"vs_currency": "usd", "days": "60"},
        )
        fng = await _get_json(client, "https://api.alternative.me/fng/", params={"limit": "1"})
    return price, chart, fng


def _build(price, chart, fng) -> Dict:
    sol = (price or {}).get("solana", {})
    price_usd = float(sol.get("usd", 0) or 0)
    price_eur = float(sol.get("eur", 0) or 0)
    change_24h = float(sol.get("usd_24h_change", 0) or 0)

    closes = _to_daily_closes((chart or {}).get("prices", [])) if chart else []
    sma7 = _sma(closes, 7)
    sma30 = _sma(closes, 30)
    rsi = _rsi(closes, 14)
    change_7d = ((closes[-1] - closes[-8]) / closes[-8] * 100) if len(closes) >= 8 else 0.0
    change_30d = ((closes[-1] - closes[-31]) / closes[-31] * 100) if len(closes) >= 31 else (
        ((closes[-1] - closes[0]) / closes[0] * 100) if len(closes) >= 2 else 0.0)

    fng_val, fng_label = None, ""
    if fng and isinstance(fng.get("data"), list) and fng["data"]:
        try:
            fng_val = int(fng["data"][0].get("value"))
            fng_label = fng["data"][0].get("value_classification", "")
        except (ValueError, TypeError):
            pass

    # Señal: combina tendencia + momentum + RSI en una puntuacion 0-100
    score = 50.0
    notes = []
    if sma7 and sma30:
        if price_usd > sma7:
            score += 12; notes.append("precio sobre la media de 7 dias (corto plazo alcista)")
        else:
            score -= 12; notes.append("precio bajo la media de 7 dias (corto plazo debil)")
        if sma7 > sma30:
            score += 10; notes.append("tendencia de fondo alcista (SMA7 > SMA30)")
        else:
            score -= 10; notes.append("tendencia de fondo bajista (SMA7 < SMA30)")
    if change_7d > 0:
        score += 8
    else:
        score -= 8
    if rsi is not None:
        if rsi > 75:
            score -= 10; notes.append(f"RSI {rsi} = sobrecompra (riesgo de correccion)")
        elif rsi < 30:
            score += 8; notes.append(f"RSI {rsi} = sobreventa (posible rebote)")
        else:
            notes.append(f"RSI {rsi} = zona neutral")
    score = max(0.0, min(100.0, score))
    signal = "bullish" if score >= 60 else ("bearish" if score <= 40 else "neutral")

    label_es = {"bullish": "Alcista", "neutral": "Neutral", "bearish": "Bajista"}[signal]
    reasoning = f"Mercado {label_es.lower()} (puntuacion {score:.0f}/100). " + "; ".join(notes[:3]) + "."

    return {
        "price_usd": round(price_usd, 2),
        "price_eur": round(price_eur, 2),
        "change_24h": round(change_24h, 2),
        "change_7d": round(change_7d, 2),
        "change_30d": round(change_30d, 2),
        "sma7": round(sma7, 2) if sma7 else None,
        "sma30": round(sma30, 2) if sma30 else None,
        "above_sma7": (price_usd > sma7) if sma7 else None,
        "above_sma30": (price_usd > sma30) if sma30 else None,
        "rsi14": rsi,
        "fear_greed": fng_val,
        "fear_greed_label": fng_label,
        "signal": signal,
        "signal_label": label_es,
        "signal_score": round(score),
        "reasoning": reasoning,
        "updated_ts": time.strftime("%H:%M:%S"),
    }


async def get_sol_market(with_llm: bool = True) -> Dict:
    """Devuelve los indicadores del mercado de SOL (cacheados 2 min) + lectura
    opcional de Claude (cacheada 30 min)."""
    now = time.time()
    if _cache["data"] is None or (now - _cache["ts"]) > CACHE_TTL:
        try:
            price, chart, fng = await _fetch_raw()
            _cache["data"] = _build(price, chart, fng)
            _cache["ts"] = now
        except Exception:
            if _cache["data"] is None:
                return {"error": "No se pudo obtener el mercado de SOL"}

    data = dict(_cache["data"])

    # Lectura razonada de Claude (opcional, cache largo para controlar coste)
    if with_llm:
        try:
            import llm_analyst
            if llm_analyst.is_enabled() and (
                _llm_cache["read"] is None or (now - _llm_cache["ts"]) > LLM_CACHE_TTL
            ):
                read = await llm_analyst.sol_market_read(data)
                if read:
                    _llm_cache["read"] = read
                    _llm_cache["ts"] = now
        except Exception:
            pass
    data["llm_read"] = _llm_cache["read"]
    return data
