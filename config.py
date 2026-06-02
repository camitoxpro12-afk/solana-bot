import os
from dotenv import load_dotenv

# override=True: el archivo .env manda siempre, aunque existan variables de
# entorno con el mismo nombre. Evita confusiones de configuracion.
load_dotenv(override=True)

# Wallet
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")

# Trading parameters
MAX_TRADE_PCT = float(os.getenv("MAX_TRADE_PCT", "0.10"))
MIN_TRADE_SOL = float(os.getenv("MIN_TRADE_SOL", "0.02"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.25"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.12"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "3"))
SLIPPAGE_BPS = int(os.getenv("SLIPPAGE_BPS", "500"))

# Token filters
MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", "10000"))
MIN_SCORE = float(os.getenv("MIN_SCORE", "58"))
MIN_SCORE_TRENDING = float(os.getenv("MIN_SCORE_TRENDING", "50"))  # umbral mas flexible para trending/top

# Nivel de riesgo del bot: conservador | balanceado | agresivo (ajusta umbrales y la actitud del LLM)
RISK_MODE = os.getenv("RISK_MODE", "balanceado")

# Filtro de mercado global: NO comprar memecoins cuando SOL esta bajista (caen mas en rojo)
ENABLE_MARKET_FILTER = os.getenv("ENABLE_MARKET_FILTER", "true").lower() == "true"
MARKET_FILTER_MIN_SCORE = float(os.getenv("MARKET_FILTER_MIN_SCORE", "45"))
# Anti-FOMO: no comprar una moneda que ya subio demasiado en 1h (evita comprar el techo del pump)
MAX_PUMP_1H_PCT = float(os.getenv("MAX_PUMP_1H_PCT", "60"))

# === ESTRATEGIA DE ENTRADA ===
# "dip"  = comprar la BAJADA de una moneda en tendencia (esperar retroceso) y vender el rebote.
# "momentum" = comprar cuando sube (modo anterior).
STRATEGY = os.getenv("STRATEGY", "dip")
DIP_MIN_24H_RISE = float(os.getenv("DIP_MIN_24H_RISE", "15"))  # la moneda debe haber subido >= X% en 24h
DIP_MAX_1H = float(os.getenv("DIP_MAX_1H", "-3"))              # y estar bajando <= X% en 1h ahora (el retroceso)

# Confirmacion de rebote: despues de detectar el dip, espera unos segundos y solo
# compra si el precio deja de caer y aparece presion compradora. Es la parte que
# imita el "mirar rapido, esperar la bajada y entrar cuando rebota".
ENABLE_ENTRY_REBOUND_CONFIRMATION = os.getenv("ENABLE_ENTRY_REBOUND_CONFIRMATION", "true").lower() == "true"
ENTRY_CONFIRM_SAMPLES = int(os.getenv("ENTRY_CONFIRM_SAMPLES", "3"))
ENTRY_CONFIRM_INTERVAL_SECONDS = float(os.getenv("ENTRY_CONFIRM_INTERVAL_SECONDS", "4"))
ENTRY_CONFIRM_MIN_BOUNCE_PCT = float(os.getenv("ENTRY_CONFIRM_MIN_BOUNCE_PCT", "0.6"))
ENTRY_CONFIRM_MAX_EXTRA_DROP_PCT = float(os.getenv("ENTRY_CONFIRM_MAX_EXTRA_DROP_PCT", "3"))
ENTRY_CONFIRM_MIN_BUY_RATIO = float(os.getenv("ENTRY_CONFIRM_MIN_BUY_RATIO", "0.48"))
ENTRY_CONFIRM_MIN_5M_CHANGE_PCT = float(os.getenv("ENTRY_CONFIRM_MIN_5M_CHANGE_PCT", "-2"))

# === SALIDA CON IA (hibrido: la IA es el cerebro, las reglas el gatillo rapido) ===
# La IA revisa cada posicion cada X seg: mira el precio, momentum, graficas, y decide.
# - Puede VENDER YA si ve la moneda debilitarse.
# - Y AJUSTA la regla dinamicamente: mueve el objetivo (take-profit) y el stop segun
#   como evoluciona (deja correr si sube fuerte, aprieta si se debilita).
# Las reglas rapidas (cada PRICE_CHECK_INTERVAL seg) ejecutan esos niveles AL INSTANTE.
ENABLE_AI_EXIT = os.getenv("ENABLE_AI_EXIT", "true").lower() == "true"
AI_EXIT_INTERVAL = int(os.getenv("AI_EXIT_INTERVAL", "60"))       # la IA re-evalua cada posicion cada X seg
AI_EXIT_MIN_CONFIDENCE = float(os.getenv("AI_EXIT_MIN_CONFIDENCE", "60"))  # solo vende si la IA esta segura
# La IA mueve el objetivo y el stop (regla dinamica). Si lo apagas, solo decide vender/mantener.
ENABLE_AI_DYNAMIC_LEVELS = os.getenv("ENABLE_AI_DYNAMIC_LEVELS", "true").lower() == "true"
AI_EXIT_SUMMARY_EVERY = int(os.getenv("AI_EXIT_SUMMARY_EVERY", "3"))  # loguea resumen aunque el plan no cambie cada N revisiones
# Evita que la IA convierta todos los trades en micro-ganancias que no compensan.
AI_EXIT_MIN_TARGET_PCT = float(os.getenv("AI_EXIT_MIN_TARGET_PCT", "8"))        # objetivo minimo util
AI_EXIT_MIN_SELL_PROFIT_PCT = float(os.getenv("AI_EXIT_MIN_SELL_PROFIT_PCT", "2"))  # venta IA minima si sale en ganancia
AI_EXIT_LOCK_PROFIT_AFTER_PCT = float(os.getenv("AI_EXIT_LOCK_PROFIT_AFTER_PCT", "4"))  # solo asegurar breakeven tras +4%
AI_EXIT_EARLY_MAX_STOP_PCT = float(os.getenv("AI_EXIT_EARLY_MAX_STOP_PCT", "-5"))       # antes de +4%, stop no mas apretado que -5%

# === MONEDAS FAVORITAS (las ganadoras se guardan y re-analizan para volver a entrar) ===
# Si un trade cierra ganando >= este %, la moneda se guarda como "favorita" y el escaner
# la vigila constantemente para re-entrar en su proxima bajada (re-trade).
FAVORITE_MIN_WIN_PCT = float(os.getenv("FAVORITE_MIN_WIN_PCT", "5"))
FAVORITE_RESCAN_MINUTES = float(os.getenv("FAVORITE_RESCAN_MINUTES", "3"))  # re-analiza favoritas cada X min
RETRADE_COOLDOWN_MINUTES = float(os.getenv("RETRADE_COOLDOWN_MINUTES", "45"))  # espera antes de recomprar la misma moneda
# Concentracion de holders: si el top 10 de wallets posee mas de este % -> RECHAZO DURO.
# Esas monedas son las que hacen RUG PULL (un solo duenyo tira todo el supply de golpe).
# Bajado a 40 tras detectar que TODOS los rugs tenian holders muy concentrados.
MAX_TOP10_PCT = float(os.getenv("MAX_TOP10_PCT", "40"))
# Si una posicion se cierra perdiendo mas de este % (rug pull), la moneda va a la LISTA NEGRA
# y NO se vuelve a comprar jamas (evita re-comprar una moneda que ya te rugeo).
BLACKLIST_LOSS_PCT = float(os.getenv("BLACKLIST_LOSS_PCT", "50"))
MIN_TOKEN_AGE_MINUTES = int(os.getenv("MIN_TOKEN_AGE_MINUTES", "20"))
MAX_TOKEN_AGE_HOURS = int(os.getenv("MAX_TOKEN_AGE_HOURS", "48"))

# Fuentes de escaneo: que tipo de monedas analizar
SCAN_NEW = os.getenv("SCAN_NEW", "false").lower() == "true"          # recien creadas (pump.fun) - mucho spam
SCAN_TRENDING = os.getenv("SCAN_TRENDING", "true").lower() == "true"  # en tendencia (de moda)
SCAN_TOP = os.getenv("SCAN_TOP", "true").lower() == "true"            # top por volumen 24h
SCAN_PUMPFUN_TOP = os.getenv("SCAN_PUMPFUN_TOP", "false").lower() == "true"  # pump.fun top aun trae mucho spam
RESCAN_TRENDING_MINUTES = int(os.getenv("RESCAN_TRENDING_MINUTES", "7"))  # re-evaluar trending cada X min

# Prefiltro rapido del escaner: evita gastar RugCheck/IA en tokens sin mercado real.
SCAN_MIN_LIQUIDITY_USD = float(os.getenv("SCAN_MIN_LIQUIDITY_USD", "10000"))
SCAN_MIN_VOLUME_1H_USD = float(os.getenv("SCAN_MIN_VOLUME_1H_USD", "5000"))
SCAN_MIN_TXNS_1H = int(os.getenv("SCAN_MIN_TXNS_1H", "25"))
SCAN_MIN_24H_CHANGE_PCT = float(os.getenv("SCAN_MIN_24H_CHANGE_PCT", str(DIP_MIN_24H_RISE)))
SCAN_MIN_6H_CHANGE_PCT = float(os.getenv("SCAN_MIN_6H_CHANGE_PCT", "0"))
SCAN_MAX_1H_CHANGE_PCT = float(os.getenv("SCAN_MAX_1H_CHANGE_PCT", "80"))
SCAN_MIN_BUY_RATIO_1H = float(os.getenv("SCAN_MIN_BUY_RATIO_1H", "0.45"))

# APIs
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY", "")
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")
BIRDEYE_BASE_URL = os.getenv("BIRDEYE_BASE_URL", "https://public-api.birdeye.so")
ENABLE_BIRDEYE_ENTRY_DATA = os.getenv("ENABLE_BIRDEYE_ENTRY_DATA", "true").lower() == "true"

# Capa de razonamiento LLM (Claude)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ENABLE_LLM_REVIEW = os.getenv("ENABLE_LLM_REVIEW", "true").lower() == "true"
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
LLM_MIN_CONFIDENCE = float(os.getenv("LLM_MIN_CONFIDENCE", "48"))
LLM_EFFORT = os.getenv("LLM_EFFORT", "medium")  # low | medium | high | max (solo Sonnet/Opus)
# Razonamiento profundo (thinking) en modelos potentes: mas listo pero ~3x coste.
# Apagalo para gastar menos sin cambiar de modelo.
ENABLE_LLM_THINKING = os.getenv("ENABLE_LLM_THINKING", "true").lower() == "true"
# Busqueda web para Claude (mas informado pero mas lento y con coste ~$0.01/busqueda)
ENABLE_LLM_WEBSEARCH = os.getenv("ENABLE_LLM_WEBSEARCH", "false").lower() == "true"
# IA GRATIS de respaldo (Google Gemini): se usa si Claude falla/se acaba el saldo.
# Consigue una key gratis en https://aistudio.google.com/apikey
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
# Interruptores de proveedor (se pueden cambiar desde Ajustes en el dashboard)
LLM_USE_CLAUDE = os.getenv("LLM_USE_CLAUDE", "false").lower() == "true"  # off por defecto (sin saldo)
LLM_USE_GEMINI = os.getenv("LLM_USE_GEMINI", "true").lower() == "true"   # Gemini gratis por defecto

# === MAS IAs GRATIS (rotacion automatica: cuando una llega a su limite GRATIS,
# el bot salta a la siguiente al instante y NUNCA para). Todas son gratis, rapidas
# y compatibles con OpenAI. Consigue las claves gratis (sin tarjeta):
#   Groq       -> https://console.groq.com/keys     (rapidisima, ~14.400 peticiones/dia)
#   Cerebras   -> https://cloud.cerebras.ai/        (1 millon de tokens/dia)
#   OpenRouter -> https://openrouter.ai/keys        (decenas de modelos gratis)
# Pega cada clave en el .env y se activa sola. Cuantas mas pongas, mas continuo va.
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
LLM_USE_GROQ = os.getenv("LLM_USE_GROQ", "true").lower() == "true"

CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "")
CEREBRAS_MODEL = os.getenv("CEREBRAS_MODEL", "gpt-oss-120b")
CEREBRAS_BASE_URL = os.getenv("CEREBRAS_BASE_URL", "https://api.cerebras.ai/v1")
LLM_USE_CEREBRAS = os.getenv("LLM_USE_CEREBRAS", "true").lower() == "true"

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
LLM_USE_OPENROUTER = os.getenv("LLM_USE_OPENROUTER", "true").lower() == "true"

# Asignacion de proveedor por tarea. Cada tarea usa su proveedor favorito primero,
# pero si falla o llega a limite, cae al pool de respaldo automaticamente.
# Valores validos: gemini | groq | cerebras | openrouter | claude | auto
LLM_ENTRY_PROVIDER = os.getenv("LLM_ENTRY_PROVIDER", "gemini")      # filtro/decision de compra
LLM_EXIT_PROVIDER = os.getenv("LLM_EXIT_PROVIDER", "groq")          # posiciones abiertas (rapidez)
LLM_SOL_PROVIDER = os.getenv("LLM_SOL_PROVIDER", "cerebras")        # analisis experto de SOL
LLM_PROVIDER_COOLDOWN_SECONDS = int(os.getenv("LLM_PROVIDER_COOLDOWN_SECONDS", "180"))

# Intervals (seconds)
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "30"))
PRICE_CHECK_INTERVAL = int(os.getenv("PRICE_CHECK_INTERVAL", "5"))
NEWS_INTERVAL = int(os.getenv("NEWS_INTERVAL", "120"))
PRICE_CACHE_SECONDS = 300  # Cache SOL/EUR price 5 min

# Risk management
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.25"))
MAX_DAILY_TRADES = int(os.getenv("MAX_DAILY_TRADES", "24"))
GLOBAL_TRADE_COOLDOWN_SECONDS = int(os.getenv("GLOBAL_TRADE_COOLDOWN_SECONDS", "120"))
MAX_CONSECUTIVE_LOSSES = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3"))
LOSS_PAUSE_MINUTES = float(os.getenv("LOSS_PAUSE_MINUTES", "45"))
TOKEN_RECENT_HOURS = float(os.getenv("TOKEN_RECENT_HOURS", "24"))
TOKEN_MAX_RECENT_LOSSES = int(os.getenv("TOKEN_MAX_RECENT_LOSSES", "2"))
TOKEN_MIN_RECENT_WIN_RATE = float(os.getenv("TOKEN_MIN_RECENT_WIN_RATE", "35"))
TOKEN_MIN_RECENT_AVG_PNL_PCT = float(os.getenv("TOKEN_MIN_RECENT_AVG_PNL_PCT", "-0.2"))
ENABLE_TRADING = os.getenv("ENABLE_TRADING", "true").lower() == "true"
# Balance virtual inicial en modo simulacion (paper). En modo real usa el de la wallet.
PAPER_START_EUR = float(os.getenv("PAPER_START_EUR", "50"))
PAPER_SOL_PRICE_USD = float(os.getenv("PAPER_SOL_PRICE_USD", "80"))

# === ESTRATEGIA DE SALIDA AVANZADA ===
# Take-profit parcial: vende una fraccion al llegar a Nx para recuperar capital
ENABLE_PARTIAL_TP = os.getenv("ENABLE_PARTIAL_TP", "true").lower() == "true"
PARTIAL_TP_TRIGGER_PCT = float(os.getenv("PARTIAL_TP_TRIGGER_PCT", "0.08"))   # +8%: captura rebotes utiles
PARTIAL_TP_SELL_FRACTION = float(os.getenv("PARTIAL_TP_SELL_FRACTION", "0.35"))  # vende una parte y deja correr
ENABLE_FAST_BREAKEVEN = os.getenv("ENABLE_FAST_BREAKEVEN", "true").lower() == "true"
BREAKEVEN_AFTER_PCT = float(os.getenv("BREAKEVEN_AFTER_PCT", "4"))
BREAKEVEN_STOP_PCT = float(os.getenv("BREAKEVEN_STOP_PCT", "0.5"))

# Trailing stop: el stop sube con el precio para capturar pumps grandes.
# Si esta activo, sustituye al take-profit fijo (deja correr al ganador).
ENABLE_TRAILING_STOP = os.getenv("ENABLE_TRAILING_STOP", "true").lower() == "true"
TRAILING_STOP_PCT = float(os.getenv("TRAILING_STOP_PCT", "0.12"))  # cae 12% desde el pico -> protege rebotes

# Anti-honeypot: simula compra+venta via Jupiter antes de comprar de verdad
ENABLE_HONEYPOT_CHECK = os.getenv("ENABLE_HONEYPOT_CHECK", "true").lower() == "true"
HONEYPOT_MAX_ROUNDTRIP_LOSS_PCT = float(os.getenv("HONEYPOT_MAX_ROUNDTRIP_LOSS_PCT", "0.30"))

# Analisis dev/bundle: marca concentracion del creador / insiders
ENABLE_DEV_ANALYSIS = os.getenv("ENABLE_DEV_ANALYSIS", "true").lower() == "true"
MAX_CREATOR_PCT = float(os.getenv("MAX_CREATOR_PCT", "15"))  # un holder con mas % = bandera roja

# SOL mint address
SOL_MINT = "So11111111111111111111111111111111111111112"
WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # dolar estable

# === SWING TRADING DE SOL (rotar SOL <-> USDC segun el mercado) ===
# Mantiene SOL cuando el mercado esta alcista; lo protege en USDC cuando esta bajista.
ENABLE_SOL_SWING = os.getenv("ENABLE_SOL_SWING", "true").lower() == "true"
SOL_SWING_PCT = float(os.getenv("SOL_SWING_PCT", "0.4"))            # % del balance que gestiona el swing
SOL_SWING_ENTER_SCORE = float(os.getenv("SOL_SWING_ENTER_SCORE", "55"))  # vuelve a SOL si senal >=
SOL_SWING_EXIT_SCORE = float(os.getenv("SOL_SWING_EXIT_SCORE", "42"))    # protege en USDC si senal <=
SOL_SWING_INTERVAL = int(os.getenv("SOL_SWING_INTERVAL", "300"))         # revisa cada 5 min

# Aprendizaje: ignora micro-resultados porque no son senal real (slippage/ruido).
LEARNING_MIN_TRADES = int(os.getenv("LEARNING_MIN_TRADES", "8"))
LEARNING_MIN_ABS_PNL_PCT = float(os.getenv("LEARNING_MIN_ABS_PNL_PCT", "1.0"))
LEARNING_WINDOW_TRADES = int(os.getenv("LEARNING_WINDOW_TRADES", "100"))
LEARNING_RATE = float(os.getenv("LEARNING_RATE", "0.05"))

# Jupiter API (lite-api = gratis, sin key). La vieja quote-api.jup.ag fue retirada.
JUPITER_QUOTE_URL = os.getenv("JUPITER_QUOTE_URL", "https://lite-api.jup.ag/swap/v1/quote")
JUPITER_SWAP_URL = os.getenv("JUPITER_SWAP_URL", "https://lite-api.jup.ag/swap/v1/swap")
JUPITER_DYNAMIC_SLIPPAGE = os.getenv("JUPITER_DYNAMIC_SLIPPAGE", "true").lower() == "true"
JUPITER_PRIORITY_LEVEL = os.getenv("JUPITER_PRIORITY_LEVEL", "veryHigh")
JUPITER_MAX_PRIORITY_LAMPORTS = int(os.getenv("JUPITER_MAX_PRIORITY_LAMPORTS", "1000000"))
MAX_PRICE_IMPACT_PCT = float(os.getenv("MAX_PRICE_IMPACT_PCT", "5"))

# Analysis API base URLs
DEXSCREENER_URL = "https://api.dexscreener.com"
RUGCHECK_URL = "https://api.rugcheck.xyz/v1"
PUMPFUN_API = "https://frontend-api.pump.fun"
COINGECKO_URL = "https://api.coingecko.com/api/v3"
GECKOTERMINAL_URL = "https://api.geckoterminal.com/api/v2"  # tendencia/top on-chain de Solana
