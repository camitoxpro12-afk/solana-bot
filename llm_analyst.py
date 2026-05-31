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

import json
import re
from typing import Optional, Dict, Any

import config

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


def is_enabled() -> bool:
    return (
        ANTHROPIC_AVAILABLE
        and config.ENABLE_LLM_REVIEW
        and bool(config.ANTHROPIC_API_KEY)
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

    client = _get_client()
    model = config.LLM_MODEL
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
    kwargs = dict(
        model=model,
        system=[{"type": "text", "text": SOL_MARKET_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_msg}],
        output_config={"format": {"type": "json_schema", "schema": SOL_SCHEMA}},
    )
    if _supports_thinking(model) and config.ENABLE_LLM_THINKING:
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["max_tokens"] = 3000
    else:
        kwargs["max_tokens"] = 600

    try:
        resp = await client.messages.create(**kwargs)
    except Exception as e:
        log_fn("WARNING", f"LLM lectura SOL fallo: {e}")
        return None

    text = next((b.text for b in resp.content if b.type == "text"), None)
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = _extract_verdict_json(text)
    if not data:
        return None
    data["outlook"] = data.get("outlook") if data.get("outlook") in ("bullish", "neutral", "bearish") else "neutral"
    try:
        data["confidence"] = max(0, min(100, int(data.get("confidence", 0))))
    except (ValueError, TypeError):
        data["confidence"] = 0
    data["reasoning"] = str(data.get("reasoning", ""))[:400]
    return data


async def llm_review(analysis, log_fn=None) -> Optional[Dict[str, Any]]:
    """
    Pide a Claude un veredicto razonado. Devuelve dict {decision, confidence,
    key_risks, reasoning} o None (fail-open) si esta desactivado o la API falla.
    """
    if not is_enabled():
        return None
    if log_fn is None:
        def log_fn(level, msg):
            pass

    client = _get_client()
    model = config.LLM_MODEL
    user_msg = _build_user_message(analysis)

    try:
        if config.ENABLE_LLM_WEBSEARCH:
            data = await _review_websearch(client, user_msg, model, log_fn)
        else:
            data = await _review_structured(client, user_msg, model, log_fn)
    except _anthropic.APIStatusError as e:
        log_fn("WARNING", f"LLM error API ({e.status_code}): se omite el filtro LLM")
        return None
    except Exception as e:
        log_fn("WARNING", f"LLM error: {e} - se omite el filtro LLM")
        return None

    if data is None:
        log_fn("WARNING", "LLM no devolvio un veredicto parseable - se omite")
        return None
    return _sanitize(data)
