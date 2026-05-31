import os
from dotenv import load_dotenv

# override=True: el archivo .env manda siempre, aunque existan variables de
# entorno con el mismo nombre. Evita confusiones de configuracion.
load_dotenv(override=True)

# Wallet
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")

# Trading parameters
MAX_TRADE_PCT = float(os.getenv("MAX_TRADE_PCT", "0.18"))
MIN_TRADE_SOL = float(os.getenv("MIN_TRADE_SOL", "0.02"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "1.5"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.35"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "3"))
SLIPPAGE_BPS = int(os.getenv("SLIPPAGE_BPS", "500"))

# Token filters
MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", "4000"))
MIN_SCORE = float(os.getenv("MIN_SCORE", "58"))
MIN_SCORE_TRENDING = float(os.getenv("MIN_SCORE_TRENDING", "50"))  # umbral mas flexible para trending/top

# Nivel de riesgo del bot: conservador | balanceado | agresivo (ajusta umbrales y la actitud del LLM)
RISK_MODE = os.getenv("RISK_MODE", "balanceado")
MAX_TOP10_PCT = float(os.getenv("MAX_TOP10_PCT", "45"))
MIN_TOKEN_AGE_MINUTES = int(os.getenv("MIN_TOKEN_AGE_MINUTES", "20"))
MAX_TOKEN_AGE_HOURS = int(os.getenv("MAX_TOKEN_AGE_HOURS", "48"))

# Fuentes de escaneo: que tipo de monedas analizar
SCAN_NEW = os.getenv("SCAN_NEW", "false").lower() == "true"          # recien creadas (pump.fun) - mucho spam
SCAN_TRENDING = os.getenv("SCAN_TRENDING", "true").lower() == "true"  # en tendencia (de moda)
SCAN_TOP = os.getenv("SCAN_TOP", "true").lower() == "true"            # top por volumen 24h
RESCAN_TRENDING_MINUTES = int(os.getenv("RESCAN_TRENDING_MINUTES", "20"))  # re-evaluar trending cada X min

# APIs
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY", "")
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")

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

# Intervals (seconds)
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "30"))
PRICE_CHECK_INTERVAL = int(os.getenv("PRICE_CHECK_INTERVAL", "15"))
NEWS_INTERVAL = int(os.getenv("NEWS_INTERVAL", "120"))
PRICE_CACHE_SECONDS = 300  # Cache SOL/EUR price 5 min

# Risk management
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.25"))
ENABLE_TRADING = os.getenv("ENABLE_TRADING", "true").lower() == "true"
# Balance virtual inicial en modo simulacion (paper). En modo real usa el de la wallet.
PAPER_START_EUR = float(os.getenv("PAPER_START_EUR", "50"))

# === ESTRATEGIA DE SALIDA AVANZADA ===
# Take-profit parcial: vende una fraccion al llegar a Nx para recuperar capital
ENABLE_PARTIAL_TP = os.getenv("ENABLE_PARTIAL_TP", "true").lower() == "true"
PARTIAL_TP_TRIGGER_PCT = float(os.getenv("PARTIAL_TP_TRIGGER_PCT", "1.0"))   # +100% = 2x
PARTIAL_TP_SELL_FRACTION = float(os.getenv("PARTIAL_TP_SELL_FRACTION", "0.5"))  # vende la mitad

# Trailing stop: el stop sube con el precio para capturar pumps grandes.
# Si esta activo, sustituye al take-profit fijo (deja correr al ganador).
ENABLE_TRAILING_STOP = os.getenv("ENABLE_TRAILING_STOP", "true").lower() == "true"
TRAILING_STOP_PCT = float(os.getenv("TRAILING_STOP_PCT", "0.30"))  # cae 30% desde el pico -> vende

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
SOL_SWING_PCT = float(os.getenv("SOL_SWING_PCT", "0.3"))            # % del balance que gestiona el swing
SOL_SWING_ENTER_SCORE = float(os.getenv("SOL_SWING_ENTER_SCORE", "55"))  # vuelve a SOL si senal >=
SOL_SWING_EXIT_SCORE = float(os.getenv("SOL_SWING_EXIT_SCORE", "35"))    # protege en USDC si senal <=
SOL_SWING_INTERVAL = int(os.getenv("SOL_SWING_INTERVAL", "600"))         # revisa cada 10 min

# Jupiter API (lite-api = gratis, sin key). La vieja quote-api.jup.ag fue retirada.
JUPITER_QUOTE_URL = os.getenv("JUPITER_QUOTE_URL", "https://lite-api.jup.ag/swap/v1/quote")
JUPITER_SWAP_URL = os.getenv("JUPITER_SWAP_URL", "https://lite-api.jup.ag/swap/v1/swap")

# Analysis API base URLs
DEXSCREENER_URL = "https://api.dexscreener.com"
RUGCHECK_URL = "https://api.rugcheck.xyz/v1"
PUMPFUN_API = "https://frontend-api.pump.fun"
COINGECKO_URL = "https://api.coingecko.com/api/v3"
GECKOTERMINAL_URL = "https://api.geckoterminal.com/api/v2"  # tendencia/top on-chain de Solana
