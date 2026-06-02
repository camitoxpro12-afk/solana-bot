"""
Capa de razonamiento con Claude (Anthropic API).

Actua como un SEGUNDO FILTRO: solo se invoca sobre tokens que YA pasaron el
analisis algoritmico (veredicto 'buy'). Asi se minimiza el coste - el LLM solo
razona sobre un punado de candidatos al dia, no sobre cada token escaneado.

Dos modos:
  - Estructurado (rapido/barato): output_config fuerza JSON. Con thinking en
    modelos potentes (Opus/Sonnet) para razonar mas a fondo.
  - Con busqueda web: Claude puede buscar en internet (reputacion del token,
    rugs recientes, sentimiento) antes de decidir. Mas lento y con coste de
    busqueda (~$0.01/busqueda), pero mas informado.

Modelo configurable via LLM_MODEL en .env. Devuelve veredicto razonado.
"""

import asyncio
import json
import re
import time
from typing import Optional, Dict, Any

import httpx
import config

# Control de ritmo + POOL de claves de Gemini (rota entre varias para repartir la carga)
_gemini_lock = asyncio.Lock()
_gemini_last = [0.0]
_gemini_key_idx = [0]
GEMINI_MIN_INTERVAL = 6.0  # segundos minimos entre llamadas POR CLAVE


def _gemini_keys() -> list:
    """Lista de claves de Gemini (GEMINI_API_KEY puede traer varias separadas por comas)."""
    return [k.strip() for k in (config.GEMINI_API_KEY or "").split(",") if k.strip()]


def _next_gemini_key() -> Optional[str]:
    """Devuelve la siguiente clave en rotacion (round-robin) para repartir la carga."""
    keys = _gemini_keys()
    if not keys:
        return None
    k = keys[_gemini_key_idx[0] % len(keys)]
    _gemini_key_idx[0] += 1
    return k


async def _throttle_gemini():
    """Espacia las llamadas a Gemini. Con N claves, el ritmo global es N veces mas rapido."""
    async with _gemini_lock:
        interval = GEMINI_MIN_INTERVAL / max(1, len(_gemini_keys()))
        wait = interval - (time.monotonic() - _gemini_last[0])
        if wait > 0:
            await asyncio.sleep(wait)
        _gemini_last[0] = time.monotonic()

try:
    from anthropic import AsyncAnthropic
    import anthropic as _anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


# Actitud segun el nivel de riesgo elegido por el usuario
_STANCE = {
    "conservador": "Tu sesgo es la PRUDENCIA. Ante la duda, veta. Prefieres perderte oportunidades antes que arriesgar capital.",
    "balanceado": "Buscas EQUILIBRIO entre riesgo y oportunidad. Ningun token es perfecto y la volatilidad es NORMAL en memecoins; no exijas perfeccion. Aprueba tokens con fundamentos solidos y riesgo ACEPTABLE aunque tengan imperfecciones menores. Veta solo ante senales rojas reales.",
    "agresivo": "Aceptas mas riesgo a cambio de mayor recompensa. Aprueba tokens con potencial aunque sean imperfectos, siempre que no haya senales rojas criticas. Solo vetas ante peligro claro (scam, honeypot, rug evidente).",
}


def _build_system_prompt() -> str:
    """System prompt del analista, con la actitud segun config.RISK_MODE."""
    stance = _STANCE.get(config.RISK_MODE, _STANCE["balanceado"])
    return f"""Eres un analista experto en memecoins de Solana. Revisas tokens que YA pasaron un filtro algoritmico y decides si CONFIRMAR la compra (decision="buy") o VETARLA (decision="veto").

{stance}

=== VETA (decision="veto") solo ante SENALES ROJAS REALES ===
- Honeypot o imposibilidad de vender; impuestos de venta abusivos.
- Mint o freeze authority todavia activos.
- Concentracion peligrosa: top 10 wallets con mas del 40% del supply.
- Pump & dump evidente: vela vertical (+cientos% en 1h) sin volumen sostenido.
- Fuerte presion vendedora pese a "subir", o datos incoherentes que ocultan el riesgo.

=== APRUEBA (decision="buy") cuando el balance riesgo/recompensa es razonable ===
- Seguridad ok (mint/freeze revocados, sin riesgos criticos).
- Liquidez suficiente y proporcional al market cap.
- Distribucion de holders sana o aceptable (top 10 por debajo de ~35%).
- Volumen real y momentum positivo (no tiene por que ser perfecto).
Un token NO necesita ser perfecto: basta con que el riesgo sea aceptable y haya potencial. Rechazar absolutamente todo no es util.

=== PRINCIPIOS ===
1. Coherencia: desconfia de metricas que se contradicen (precio disparado pero liquidez minima = trampa).
2. Calibra la confianza al riesgo REAL, no al miedo. La volatilidad normal de una memecoin no es motivo de veto.
3. No te dejes llevar por el hype extremo, pero tampoco rechaces todo por defecto.

=== FORMATO DE RESPUESTA ===
Termina SIEMPRE con un JSON entre etiquetas <verdict></verdict>:
<verdict>{{"decision": "buy" o "veto", "confidence": entero 0-100, "key_risks": ["riesgo1"], "reasoning": "explicacion breve en espanol"}}</verdict>
Se conciso. No inventes datos que no te dieron."""

WEBSEARCH_SUFFIX = """

=== BUSQUEDA WEB ===
Tienes acceso a busqueda web. Usala con criterio (maximo unas pocas busquedas) para investigar:
- Reputacion del token o su creador, rugs o scams recientes asociados.
- Menciones en redes (X/Twitter), sentimiento de la comunidad.
- Si el contrato o el nombre aparece en listas de estafas conocidas.
Si la busqueda no aporta nada util, decide con los datos que ya tienes. No dejes que la busqueda retrase en exceso la decision."""


VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["buy", "veto"]},
        "confidence": {"type": "integer"},
        "key_risks": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "string"},
    },
    "required": ["decision", "confidence", "key_risks", "reasoning"],
    "additionalProperties": False,
}

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 3}

_client: Optional[object] = None


def _claude_available() -> bool:
    return config.LLM_USE_CLAUDE and ANTHROPIC_AVAILABLE and bool(config.ANTHROPIC_API_KEY)


def _gemini_available() -> bool:
    return config.LLM_USE_GEMINI and bool(_gemini_keys())


def is_enabled() -> bool:
    """Activo si el filtro LLM esta on y hay AL MENOS un proveedor disponible."""
    return config.ENABLE_LLM_REVIEW and (_claude_available() or _any_free_available())


def provider_label() -> str:
    parts = []
    if _claude_available():
        parts.append(f"Claude ({config.LLM_MODEL})")
    if _gemini_available():
        parts.append("Gemini")
    if _groq_available():
        parts.append("Groq")
    if _cerebras_available():
        parts.append("Cerebras")
    if _openrouter_available():
        parts.append("OpenRouter")
    if not parts:
        return "ninguno (solo algoritmo)"
    if len(parts) > 1:
        return " + ".join(parts) + " (rotacion automatica, nunca para)"
    return parts[0]


def role_label() -> str:
    """Resumen de la asignacion de modelos por tarea."""
    return (
        f"entrada={config.LLM_ENTRY_PROVIDER}, "
        f"salida={config.LLM_EXIT_PROVIDER}, "
        f"sol={config.LLM_SOL_PROVIDER}"
    )


def _supports_thinking(model: str) -> bool:
    """Opus 4.x y Sonnet 4.6 soportan adaptive thinking; Haiku no."""
    m = model.lower()
    return ("opus-4" in m) or ("sonnet-4-6" in m)


def _effort_for(model: str) -> Optional[str]:
    """Nivel de esfuerzo valido para el modelo (Haiku no soporta effort)."""
    m = model.lower()
    effort = (config.LLM_EFFORT or "medium").lower()
    if "haiku" in m:
        return None  # Haiku no soporta el parametro effort
    if "sonnet-4-6" in m and effort in ("max", "xhigh"):
        return "high"  # 'max' es solo para Opus
    return effort


def _get_client() -> object:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def _build_user_message(analysis) -> str:
    s = analysis.scores
    scores_txt = ""
    if s:
        scores_txt = (
            f"  - Seguridad RugCheck: {s.rugcheck}/20\n"
            f"  - Mint authority (10=revocado): {s.mint_authority}/10\n"
            f"  - Freeze authority (10=desactivado): {s.freeze_authority}/10\n"
            f"  - Liquidez: {s.liquidity}/15\n"
            f"  - Distribucion holders: {s.holder_distribution}/10\n"
            f"  - Volumen/MarketCap: {s.volume_mc_ratio}/10\n"
            f"  - Tendencia precio: {s.price_trend}/8\n"
            f"  - Tendencia volumen: {s.volume_trend}/7\n"
            f"  - Noticias: {s.news_sentiment}/5\n"
            f"  - Social: {s.social_score}/5\n"
            f"  - TOTAL: {s.total}/100\n"
        )
    risks_txt = ", ".join(analysis.rugcheck_risks[:6]) if analysis.rugcheck_risks else "ninguno reportado"
    return f"""Revisa este token de Solana que paso el filtro algoritmico:

DATOS DEL TOKEN:
- Nombre: {analysis.name} ({analysis.symbol})
- Direccion (mint): {analysis.address}
- Precio: ${analysis.price_usd:.8f}
- Market Cap: ${analysis.market_cap:,.0f}
- Liquidez: ${analysis.liquidity_usd:,.0f}
- Volumen 1h: ${analysis.volume_1h:,.0f}
- Volumen 24h: ${analysis.volume_24h:,.0f}
- Cambio precio 1h: {analysis.price_change_1h:+.1f}%
- Cambio precio 24h: {analysis.price_change_24h:+.1f}%
- Concentracion top 10 holders: {analysis.top10_pct:.1f}%
- Mint authority activo (peligroso): {analysis.mint_authority}
- Freeze authority activo (peligroso): {analysis.freeze_authority}
- Edad del token: {analysis.age_minutes:.0f} minutos
- Riesgos RugCheck: {risks_txt}

PUNTUACIONES ALGORITMICAS:
{scores_txt}
Decide: comprar o vetar. Recuerda tu sesgo de prudencia."""


def _extract_verdict_json(text: str) -> Optional[Dict[str, Any]]:
    """Extrae el JSON del veredicto del texto (preferentemente entre <verdict></verdict>)."""
    candidates = []
    m = re.search(r"<verdict>\s*(\{.*?\})\s*</verdict>", text, re.DOTALL)
    if m:
        candidates.append(m.group(1))
    # Fallback: cualquier objeto plano que contenga "decision"
    for mm in re.finditer(r"\{[^{}]*\"decision\"[^{}]*\}", text, re.DOTALL):
        candidates.append(mm.group(0))
    for c in reversed(candidates):
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            continue
    return None


def _sanitize(data: Dict[str, Any]) -> Dict[str, Any]:
    data["decision"] = "buy" if data.get("decision") == "buy" else "veto"
    try:
        data["confidence"] = max(0, min(100, int(data.get("confidence", 0))))
    except (ValueError, TypeError):
        data["confidence"] = 0
    if not isinstance(data.get("key_risks"), list):
        data["key_risks"] = []
    data["key_risks"] = [str(r) for r in data["key_risks"]][:8]
    data["reasoning"] = str(data.get("reasoning", ""))[:500]
    return data


def _accumulate_cost(totals: dict, usage):
    totals["in"] += getattr(usage, "input_tokens", 0) or 0
    totals["out"] += getattr(usage, "output_tokens", 0) or 0
    totals["cr"] += getattr(usage, "cache_read_input_tokens", 0) or 0
    totals["cw"] += getattr(usage, "cache_creation_input_tokens", 0) or 0
    stu = getattr(usage, "server_tool_use", None)
    if stu is not None:
        totals["searches"] += getattr(stu, "web_search_requests", 0) or 0


# Precios por 1M tokens (input, output)
_PRICES = {
    "haiku": (1.0, 5.0),
    "sonnet": (3.0, 15.0),
    "opus": (5.0, 25.0),
}


def _log_cost(totals: dict, model: str, log_fn):
    try:
        key = "opus" if "opus" in model else ("sonnet" if "sonnet" in model else "haiku")
        pin, pout = _PRICES[key]
        cost = (totals["in"] * pin + totals["out"] * pout +
                totals["cr"] * pin * 0.1 + totals["cw"] * pin * 1.25) / 1_000_000
        cost += totals["searches"] * 0.01  # ~$10/1000 busquedas
        extra = ""
        if totals["searches"]:
            extra += f" | {totals['searches']} busquedas web"
        if totals["cr"] or totals["cw"]:
            extra += f" | cache r{totals['cr']}/w{totals['cw']}"
        log_fn("INFO", f"LLM ({model}) coste: ${cost:.5f} | in:{totals['in']} out:{totals['out']}{extra}")
    except Exception:
        pass


async def _review_structured(client, user_msg: str, model: str, log_fn) -> Optional[Dict]:
    """Salida estructurada (output_config). Con thinking/effort en modelos potentes."""
    output_config = {"format": {"type": "json_schema", "schema": VERDICT_SCHEMA}}
    effort = _effort_for(model)
    if effort:
        output_config["effort"] = effort
    kwargs = dict(
        model=model,
        system=[{"type": "text", "text": _build_system_prompt(), "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_msg}],
        output_config=output_config,
    )
    if _supports_thinking(model) and config.ENABLE_LLM_THINKING:
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["max_tokens"] = 3500   # deja espacio al razonamiento + JSON
    else:
        kwargs["max_tokens"] = 800

    resp = await client.messages.create(**kwargs)
    totals = {"in": 0, "out": 0, "cr": 0, "cw": 0, "searches": 0}
    _accumulate_cost(totals, resp.usage)
    _log_cost(totals, model, log_fn)

    text = next((b.text for b in resp.content if b.type == "text"), None)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return _extract_verdict_json(text)


async def _review_websearch(client, user_msg: str, model: str, log_fn) -> Optional[Dict]:
    """Con busqueda web. Sin output_config (incompatible con citas); extrae <verdict>."""
    messages = [{"role": "user", "content": user_msg}]
    totals = {"in": 0, "out": 0, "cr": 0, "cw": 0, "searches": 0}
    final_text = ""

    for _ in range(6):  # limite de continuaciones (pause_turn por busquedas server-side)
        resp = await client.messages.create(
            model=model,
            max_tokens=2500,
            system=[{"type": "text", "text": _build_system_prompt() + WEBSEARCH_SUFFIX,
                     "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            tools=[WEB_SEARCH_TOOL],
        )
        _accumulate_cost(totals, resp.usage)
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        final_text = " ".join(b.text for b in resp.content if b.type == "text")
        break

    _log_cost(totals, model, log_fn)
    return _extract_verdict_json(final_text) if final_text else None


SOL_MARKET_SYSTEM = """Eres un analista de mercado de criptomonedas especializado en Solana (SOL). Te dan indicadores tecnicos y de sentimiento del mercado de SOL y debes dar una lectura honesta de si es buen momento para MANTENER o ACUMULAR SOL.

SOL es un activo solido (no es una memecoin), asi que el riesgo no es un scam sino el riesgo de mercado: comprar caro antes de una caida. Se equilibrado y honesto: nadie puede predecir el precio con certeza. Da una lectura probabilistica, no promesas.

Considera: tendencia (medias moviles), momentum (cambios 7d/30d), RSI (sobrecompra/sobreventa) y el indice de miedo/codicia (contrarian: miedo extremo suele ser oportunidad, codicia extrema suele ser riesgo).

Responde con un JSON: {"outlook": "bullish"|"neutral"|"bearish", "confidence": entero 0-100, "reasoning": "explicacion breve en espanol (2-3 frases)"}. Termina SIEMPRE con ese JSON entre etiquetas <verdict></verdict>."""

SOL_SCHEMA = {
    "type": "object",
    "properties": {
        "outlook": {"type": "string", "enum": ["bullish", "neutral", "bearish"]},
        "confidence": {"type": "integer"},
        "reasoning": {"type": "string"},
    },
    "required": ["outlook", "confidence", "reasoning"],
    "additionalProperties": False,
}


async def sol_market_read(m: Dict[str, Any], log_fn=None) -> Optional[Dict[str, Any]]:
    """Lectura razonada del mercado de SOL por Claude. Devuelve {outlook, confidence, reasoning} o None."""
    if not is_enabled():
        return None
    if log_fn is None:
        def log_fn(level, msg):
            pass

    user_msg = (
        f"Indicadores actuales del mercado de SOL:\n"
        f"- Precio: ${m.get('price_usd')}\n"
        f"- Cambio 24h: {m.get('change_24h')}%\n"
        f"- Cambio 7d: {m.get('change_7d')}%\n"
        f"- Cambio 30d: {m.get('change_30d')}%\n"
        f"- Media movil 7d: ${m.get('sma7')} (precio {'por encima' if m.get('above_sma7') else 'por debajo'})\n"
        f"- Media movil 30d: ${m.get('sma30')} (precio {'por encima' if m.get('above_sma30') else 'por debajo'})\n"
        f"- RSI(14): {m.get('rsi14')}\n"
        f"- Indice miedo/codicia: {m.get('fear_greed')} ({m.get('fear_greed_label')})\n\n"
        f"¿Es buen momento para mantener/acumular SOL? Da tu lectura."
    )
    # SOL usa su proveedor favorito primero; si falla, cae al pool de respaldo.
    text = await _ask_llm(SOL_MARKET_SYSTEM, user_msg, log_fn, max_tokens=1500, role="sol")
    if not text:
        return None
    data = _extract_tagged_json(text, "verdict") or _extract_verdict_json(text)
    if not data:
        return None
    data["outlook"] = data.get("outlook") if data.get("outlook") in ("bullish", "neutral", "bearish") else "neutral"
    try:
        data["confidence"] = max(0, min(100, int(data.get("confidence", 0))))
    except (ValueError, TypeError):
        data["confidence"] = 0
    data["reasoning"] = str(data.get("reasoning", ""))[:400]
    return data


async def _gemini_review(user_msg: str, log_fn) -> Optional[Dict]:
    """IA GRATIS (Google Gemini) via REST. Devuelve el dict del veredicto o None."""
    key = _next_gemini_key()
    if not key:
        return None
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{config.GEMINI_MODEL}:generateContent?key={key}")
    body = {
        "system_instruction": {"parts": [{"text": _build_system_prompt()}]},
        "contents": [{"role": "user", "parts": [{"text": user_msg}]}],
        # Gemini 2.5 "piensa": sube el limite para que quepan pensamiento + respuesta
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2048},
    }
    try:
        await _throttle_gemini()
        async with httpx.AsyncClient(timeout=40) as client:
            r = await client.post(url, json=body)
        if r.status_code != 200:
            log_fn("WARNING", f"Gemini error {r.status_code} - se omite el filtro LLM")
            return None
        data = r.json()
        cands = data.get("candidates", [])
        if not cands:
            return None
        parts = cands[0].get("content", {}).get("parts", [])
        text = " ".join(p.get("text", "") for p in parts)
        if not text:
            return None
        log_fn("INFO", "LLM: Gemini (gratis) respondio")
        return _extract_verdict_json(text)
    except Exception as e:
        log_fn("WARNING", f"Gemini fallo: {e}")
        return None


async def llm_review(analysis, log_fn=None) -> Optional[Dict[str, Any]]:
    """
    Veredicto razonado. Intenta Claude (si hay key y saldo); si falla o no hay,
    usa Gemini (gratis). Si ninguno responde -> None (el bot sigue con el algoritmo).
    """
    if not config.ENABLE_LLM_REVIEW:
        return None
    if log_fn is None:
        def log_fn(level, msg):
            pass

    user_msg = _build_user_message(analysis)

    # 1) Claude (de pago) solo si esta activo y asignado a entrada/auto.
    entry_provider = _provider_preference("entry")
    if _claude_available() and entry_provider in ("claude", "auto"):
        try:
            client = _get_client()
            model = config.LLM_MODEL
            if config.ENABLE_LLM_WEBSEARCH:
                data = await _review_websearch(client, user_msg, model, log_fn)
            else:
                data = await _review_structured(client, user_msg, model, log_fn)
            if data:
                return _sanitize(data)
            log_fn("WARNING", "Claude no dio veredicto" + (" - probando Gemini gratis" if _gemini_available() else ""))
        except _anthropic.APIStatusError as e:
            log_fn("WARNING", f"Claude error API ({e.status_code})" + (" - probando Gemini gratis" if _gemini_available() else ""))
        except Exception as e:
            log_fn("WARNING", f"Claude fallo ({e})" + (" - probando Gemini gratis" if _gemini_available() else ""))

    # 2) Pool de IAs GRATIS. Entrada intenta primero el proveedor asignado.
    if _any_free_available():
        text = await _ask_pool(_build_system_prompt(), user_msg, 2048, log_fn, role="entry")
        if text:
            data = _extract_verdict_json(text)
            if data:
                return _sanitize(data)

    # 3) Si entrada tenia un proveedor gratis favorito y todo fallo, Claude queda como ultimo respaldo.
    if _claude_available() and entry_provider not in ("claude", "auto"):
        text = await _ask_llm(_build_system_prompt(), user_msg, log_fn, max_tokens=2048, role="claude")
        if text:
            data = _extract_verdict_json(text)
            if data:
                return _sanitize(data)

    return None


# ── Analisis experto de Solana (provider-aware) ─────────────────────────────────

def _extract_tagged_json(text: str, tag: str) -> Optional[Dict]:
    candidates = []
    m = re.search(rf"<{tag}>\s*(\{{.*?\}})\s*</{tag}>", text, re.DOTALL)
    if m:
        candidates.append(m.group(1))
    m2 = re.search(r"\{.*\}", text, re.DOTALL)
    if m2:
        candidates.append(m2.group(0))
    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            continue
    return None


# ── POOL de IAs GRATIS con rotacion + failover (para que NUNCA pare) ────────────
# Groq, Cerebras y OpenRouter hablan el formato OpenAI; Gemini su propio REST. Se
# llaman igual. Rotamos el proveedor inicial en cada llamada para repartir la carga,
# y si uno falla (limite/error) saltamos al siguiente al instante.
_pool_idx = [0]
_key_idx: Dict[str, int] = {}
_provider_cooldown_until: Dict[str, float] = {}


def _keys_of(raw: str) -> list:
    return [k.strip() for k in (raw or "").split(",") if k.strip()]


def _next_key(name: str, raw: str) -> Optional[str]:
    keys = _keys_of(raw)
    if not keys:
        return None
    i = _key_idx.get(name, 0)
    _key_idx[name] = i + 1
    return keys[i % len(keys)]


def _provider_available_now(name: str) -> bool:
    return time.time() >= _provider_cooldown_until.get(name, 0.0)


def _cooldown_provider(name: str, label: str, log_fn, seconds: Optional[int] = None):
    wait = seconds or config.LLM_PROVIDER_COOLDOWN_SECONDS
    _provider_cooldown_until[name] = time.time() + wait
    log_fn("WARNING", f"{label} rate-limit/429 - pausa {wait}s y uso otro proveedor")


def _groq_available() -> bool:
    return config.LLM_USE_GROQ and bool(_keys_of(config.GROQ_API_KEY))


def _cerebras_available() -> bool:
    return config.LLM_USE_CEREBRAS and bool(_keys_of(config.CEREBRAS_API_KEY))


def _openrouter_available() -> bool:
    return config.LLM_USE_OPENROUTER and bool(_keys_of(config.OPENROUTER_API_KEY))


def _any_free_available() -> bool:
    return _gemini_available() or _groq_available() or _cerebras_available() or _openrouter_available()


async def _gemini_ask(system_prompt: str, user_msg: str, max_tokens: int, log_fn) -> Optional[str]:
    if not _provider_available_now("gemini"):
        return None
    key = _next_gemini_key()
    if not key:
        return None
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{config.GEMINI_MODEL}:generateContent?key={key}")
    body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_msg}]}],
        "generationConfig": {"temperature": 0.5, "maxOutputTokens": max_tokens},
    }
    try:
        await _throttle_gemini()
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(url, json=body)
        if r.status_code == 200:
            cands = r.json().get("candidates", [])
            if cands:
                parts = cands[0].get("content", {}).get("parts", [])
                return (" ".join(p.get("text", "") for p in parts)) or None
        else:
            if r.status_code == 429:
                _cooldown_provider("gemini", "Gemini", log_fn)
            else:
                log_fn("WARNING", f"Gemini error {r.status_code}")
    except Exception as e:
        log_fn("WARNING", f"Gemini fallo: {e}")
    return None


async def _openai_ask(name: str, base_url: str, model: str, key_raw: str, label: str,
                      system_prompt: str, user_msg: str, max_tokens: int, log_fn) -> Optional[str]:
    """Llamada generica a cualquier proveedor compatible con OpenAI (Groq/Cerebras/OpenRouter)."""
    if not _provider_available_now(name):
        return None
    key = _next_key(name, key_raw)
    if not key:
        return None
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if name == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/camitoxpro12-afk/solana-bot"
        headers["X-Title"] = "Solana Trading Bot"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.5,
    }
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(f"{base_url}/chat/completions", json=body, headers=headers)
        if r.status_code == 200:
            choices = r.json().get("choices", [])
            if choices:
                msg = choices[0].get("message", {}) or {}
                # Modelos razonadores (gpt-oss, glm...) ponen la salida en 'content';
                # si viene vacia, usamos 'reasoning' como respaldo para no perder la respuesta.
                return (msg.get("content") or msg.get("reasoning")) or None
        else:
            if r.status_code == 429:
                retry_after = r.headers.get("retry-after")
                try:
                    wait = min(900, max(30, int(float(retry_after)))) if retry_after else None
                except (TypeError, ValueError):
                    wait = None
                _cooldown_provider(name, label, log_fn, wait)
            else:
                log_fn("WARNING", f"{label} error {r.status_code}")
    except Exception as e:
        log_fn("WARNING", f"{label} fallo: {e}")
    return None


def _free_providers() -> list:
    """IAs gratis disponibles, cada una con una llamada uniforme (system, user, max_tokens, log).
    Cada item es (id, etiqueta, callable)."""
    provs = []
    if _gemini_available() and _provider_available_now("gemini"):
        provs.append(("gemini", "Gemini gratis", _gemini_ask))
    if _groq_available() and _provider_available_now("groq"):
        provs.append(("groq", f"Groq ({config.GROQ_MODEL})",
                      lambda s, u, m, lf: _openai_ask("groq", config.GROQ_BASE_URL, config.GROQ_MODEL,
                                                      config.GROQ_API_KEY, "Groq", s, u, m, lf)))
    if _cerebras_available() and _provider_available_now("cerebras"):
        provs.append(("cerebras", f"Cerebras ({config.CEREBRAS_MODEL})",
                      lambda s, u, m, lf: _openai_ask("cerebras", config.CEREBRAS_BASE_URL, config.CEREBRAS_MODEL,
                                                      config.CEREBRAS_API_KEY, "Cerebras", s, u, m, lf)))
    if _openrouter_available() and _provider_available_now("openrouter"):
        provs.append(("openrouter", f"OpenRouter ({config.OPENROUTER_MODEL})",
                      lambda s, u, m, lf: _openai_ask("openrouter", config.OPENROUTER_BASE_URL, config.OPENROUTER_MODEL,
                                                      config.OPENROUTER_API_KEY, "OpenRouter", s, u, m, lf)))
    return provs


def _provider_preference(role: str) -> str:
    role = (role or "auto").lower()
    if role in ("gemini", "groq", "cerebras", "openrouter", "claude", "auto"):
        return role
    if role == "entry":
        return (config.LLM_ENTRY_PROVIDER or "gemini").lower()
    if role == "exit":
        return (config.LLM_EXIT_PROVIDER or "groq").lower()
    if role == "sol":
        return (config.LLM_SOL_PROVIDER or "cerebras").lower()
    return "auto"


async def _ask_pool(system_prompt: str, user_msg: str, max_tokens: int, log_fn, role: str = "auto") -> Optional[str]:
    """Usa el proveedor favorito para la tarea; si falla, salta al resto. Nunca para."""
    provs = _free_providers()
    if not provs:
        return None
    n = len(provs)
    preferred = _provider_preference(role)

    if preferred != "auto":
        preferred_items = [p for p in provs if p[0] == preferred]
        other_items = [p for p in provs if p[0] != preferred]
        ordered = preferred_items + other_items
    else:
        start = _pool_idx[0] % n
        _pool_idx[0] = (start + 1) % n
        ordered = [provs[(start + off) % n] for off in range(n)]

    if not ordered:
        ordered = provs

    for _, label, call in ordered:
        text = await call(system_prompt, user_msg, max_tokens, log_fn)
        if text:
            role_txt = {"entry": "entrada", "exit": "salida", "sol": "SOL"}.get(role, "pool")
            log_fn("INFO", f"LLM [{role_txt}]: {label} respondio")
            return text
        if len(ordered) > 1:
            log_fn("INFO", f"{label} sin respuesta - salto al siguiente proveedor")
    return None


async def _ask_llm(system_prompt: str, user_msg: str, log_fn, max_tokens: int = 1300, role: str = "auto") -> Optional[str]:
    """Claude (si esta on) y si no, el pool de IAs gratis con rotacion. Devuelve texto o None."""
    preferred = _provider_preference(role)
    # 1) Claude premium si esta activo y es favorito/auto (normalmente off: sin saldo)
    if _claude_available() and preferred in ("claude", "auto"):
        try:
            client = _get_client()
            kwargs = dict(
                model=config.LLM_MODEL, max_tokens=max_tokens,
                system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_msg}],
            )
            if _supports_thinking(config.LLM_MODEL) and config.ENABLE_LLM_THINKING:
                kwargs["thinking"] = {"type": "adaptive"}
                kwargs["max_tokens"] = max_tokens + 2500
                eff = _effort_for(config.LLM_MODEL)
                if eff:
                    kwargs["output_config"] = {"effort": eff}
            resp = await client.messages.create(**kwargs)
            text = next((b.text for b in resp.content if b.type == "text"), None)
            if text:
                return text
        except Exception as e:
            log_fn("WARNING", f"Claude (analisis) fallo: {e}")
    # 2) Pool de IAs gratis con rotacion + failover (nunca para)
    text = await _ask_pool(system_prompt, user_msg, max_tokens, log_fn, role=role)
    if text:
        return text

    # 3) Si el proveedor favorito era gratis y todo fallo, probar Claude como ultimo respaldo si esta activo.
    if _claude_available() and preferred not in ("claude", "auto"):
        try:
            client = _get_client()
            resp = await client.messages.create(
                model=config.LLM_MODEL,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_msg}],
            )
            return next((b.text for b in resp.content if b.type == "text"), None)
        except Exception as e:
            log_fn("WARNING", f"Claude (respaldo) fallo: {e}")
    return None


SOL_EXPERT_SYSTEM = """Eres un analista de Solana (SOL) de primer nivel, con vision integral: tecnica, fundamental, on-chain, DeFi y de sentimiento. Te doy un dossier con todos los datos publicos disponibles y debes dar un analisis EXPERTO y HONESTO sobre SOL.

Considera TODO en conjunto: tendencia y momentum, RSI, distancia al maximo historico, cambios 7d/30d/1 año, capitalizacion y volumen, TVL y yields de DeFi en Solana, noticias recientes, e indice de miedo/codicia (contrarian: miedo extremo suele ser oportunidad).

Se honesto: NADIE predice el futuro con certeza. Da una vision PROBABILISTICA, util y accionable: si conviene comprar/acumular/mantener/esperar/reducir/vender, en que horizonte (horas, dias, semanas), por que, y los riesgos. Si los datos no son concluyentes, dilo.

Responde en espanol y TERMINA SIEMPRE con un JSON entre <expert></expert>:
<expert>{"outlook":"alcista|neutral|bajista","recommendation":"comprar|acumular|mantener|esperar|reducir|vender","timeframe":"ej: dias a semanas","confidence":0-100,"summary":"2-4 frases","key_points":["punto1","punto2"],"risks":["riesgo1"]}</expert>"""


EXIT_SYSTEM = """Eres un trader experto gestionando EN VIVO una posicion ABIERTA de memecoin en Solana, como un scalper que vigila la grafica. Tu trabajo: leer el estado y AJUSTAR EL PLAN.

Decides DOS cosas:
1. action: "sell" (vender YA todo) o "hold" (seguir dentro con el plan ajustado).
2. El PLAN dinamico (donde poner el objetivo de ganancia y el stop):
   - target_pct: a que % de ganancia (vs entrada) tomar beneficio. Ej: 20 = vender en +20%.
   - stop_pct: a que % (vs entrada) cortar perdidas/asegurar. Negativo o positivo. Ej: -12 = cortar en -12%; 8 = asegurar ganancia vendiendo si cae a +8%.

Como pensar (estilo scalping "comprar la bajada, vender el rebote"):
- Si rebota con FUERZA (volumen y compras subiendo): deja correr -> sube el target y sube el stop detras del precio para proteger.
- Si el rebote se AGOTA o se gira (cae del maximo, mas ventas que compras): aprieta el stop cerca del precio o vende ya.
- Si lleva tiempo plano/cayendo sin reaccionar: mejor salir y buscar otra.
- Si ya hay buena ganancia: protege subiendo el stop a positivo (asegura beneficio).
- Evita micro-ganancias inutiles: un objetivo normal debe buscar al menos +8% si la estructura sigue viva.
- No subas el stop a breakeven/+0% demasiado pronto. Antes de +4% de ganancia, deja margen al trade o vende directamente si el riesgo es claro.

Las reglas rapidas ejecutan tu target/stop AL INSTANTE; tu solo defines DONDE estan. Se decisivo y breve.

Termina SIEMPRE con un JSON entre <exit></exit>:
<exit>{"action":"sell"|"hold","confidence":entero 0-100,"target_pct":numero,"stop_pct":numero,"reason":"explicacion breve en espanol"}</exit>"""


async def exit_review(pos: Dict, log_fn=None, market: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
    """La IA revisa una posicion abierta: decide vender/mantener Y ajusta target/stop.
    Devuelve {action, confidence, reason, target_pct, stop_pct} o None."""
    if log_fn is None:
        def log_fn(level, msg):
            pass
    if not (config.ENABLE_LLM_REVIEW and (_claude_available() or _any_free_available())):
        return None
    buy = pos.get("buy_price_usd") or 0
    cur = pos.get("current_price_usd") or buy
    high = pos.get("highest_price_usd") or buy
    tgt = pos.get("target_price_usd") or 0
    stp = pos.get("stop_price_usd") or 0
    pnl = ((cur - buy) / buy * 100) if buy > 0 else 0
    peak = ((high - buy) / buy * 100) if buy > 0 else 0
    drawdown = ((cur - high) / high * 100) if high > 0 else 0
    cur_tgt_pct = ((tgt - buy) / buy * 100) if buy > 0 else 0
    cur_stop_pct = ((stp - buy) / buy * 100) if buy > 0 else 0

    lines = [
        f"Posicion abierta de {pos.get('token_symbol', '?')}:",
        f"- Precio de entrada: ${buy:.8f}",
        f"- Precio actual: ${cur:.8f}",
        f"- Ganancia/perdida ahora: {pnl:+.1f}%",
        f"- Maximo alcanzado: {peak:+.1f}% (ahora {drawdown:+.1f}% por debajo de ese maximo)",
        f"- Plan ACTUAL: objetivo en {cur_tgt_pct:+.0f}%, stop en {cur_stop_pct:+.0f}%",
    ]
    if market:
        lines.append(
            "- Grafica/momentum: "
            f"5m {market.get('change_m5', 0):+.1f}%, 1h {market.get('change_h1', 0):+.1f}%, "
            f"6h {market.get('change_h6', 0):+.1f}%, 24h {market.get('change_h24', 0):+.1f}% | "
            f"compras/ventas 1h: {market.get('buys_h1', 0)}/{market.get('sells_h1', 0)} | "
            f"volumen 1h: ${market.get('volume_h1', 0):,.0f} | liquidez: ${market.get('liquidity', 0):,.0f}"
        )
    lines.append("Decide: vender ya o mantener, y ajusta el target/stop.")
    msg = "\n".join(lines)

    text = await _ask_llm(EXIT_SYSTEM, msg, log_fn, max_tokens=3000, role="exit")
    if not text:
        return None
    data = _extract_tagged_json(text, "exit")
    if not data:
        return None
    data["action"] = "sell" if data.get("action") == "sell" else "hold"
    try:
        data["confidence"] = max(0, min(100, int(data.get("confidence", 0))))
    except (ValueError, TypeError):
        data["confidence"] = 0
    # target/stop opcionales: se validan/limitan en quien los aplica
    for k in ("target_pct", "stop_pct"):
        if data.get(k) is not None:
            try:
                data[k] = float(data[k])
            except (ValueError, TypeError):
                data[k] = None
    data["reason"] = str(data.get("reason", ""))[:300]
    return data


async def sol_expert_analysis(dossier: str, log_fn=None) -> Optional[Dict[str, Any]]:
    """Analisis experto de SOL usando el proveedor activo. Devuelve dict o None."""
    if log_fn is None:
        def log_fn(level, msg):
            pass
    if not (config.ENABLE_LLM_REVIEW and (_claude_available() or _any_free_available())):
        return None
    text = await _ask_llm(SOL_EXPERT_SYSTEM, dossier, log_fn, max_tokens=4000, role="sol")
    if not text:
        return None
    data = _extract_tagged_json(text, "expert")
    if not data:
        return None
    data["outlook"] = data.get("outlook") if data.get("outlook") in ("alcista", "neutral", "bajista") else "neutral"
    try:
        data["confidence"] = max(0, min(100, int(data.get("confidence", 0))))
    except (ValueError, TypeError):
        data["confidence"] = 0
    data["recommendation"] = str(data.get("recommendation", ""))[:30]
    data["timeframe"] = str(data.get("timeframe", ""))[:60]
    data["summary"] = str(data.get("summary", ""))[:600]
    data["key_points"] = [str(x)[:200] for x in (data.get("key_points") or []) if isinstance(data.get("key_points"), list)][:6]
    data["risks"] = [str(x)[:200] for x in (data.get("risks") or []) if isinstance(data.get("risks"), list)][:5]
    return data
