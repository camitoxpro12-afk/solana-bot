# 🤖 Solana Trading Bot

Bot autónomo de trading de memecoins en Solana, con dashboard web en tiempo real,
verificación anti-scam de 10 capas, conexión a noticias y aprendizaje adaptativo.

![mode](https://img.shields.io/badge/red-Solana-purple) ![status](https://img.shields.io/badge/modo-paper%20%7C%20live-blue)

---

## ⚡ Inicio rápido

1. **Instala Python 3.10+** (https://python.org) — marca "Add to PATH".
2. Doble clic en **`run.bat`**.
3. La primera vez creará el archivo `.env`. Ábrelo y configura tu `PRIVATE_KEY`.
4. Vuelve a ejecutar `run.bat`.
5. Abre **http://localhost:8000** en tu navegador.
6. **Empieza en modo PAPER** (`ENABLE_TRADING=false`) para probar sin riesgo.

> 📖 **Lee `MANUAL_CRIPTO.md` antes de usar dinero real.** Explica los riesgos
> reales y cómo funciona cada parte.

---

## 🔑 Configurar tu wallet

> ⚠️ **Usa una wallet secundaria/dedicada, NUNCA tu wallet principal.**

1. En Phantom: **Settings → Security & Privacy → Export Private Key**.
2. Copia la clave y pégala en `.env`:
   ```
   PRIVATE_KEY=tu_clave_aqui
   ```
3. (Recomendado) Consigue un RPC gratis en https://helius.dev y ponlo en `RPC_URL`.

---

## 🎛️ Cómo funciona

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  SCANNER    │────▶│   ANALYZER   │────▶│   TRADER    │
│ pump.fun +  │     │  10 capas    │     │  Jupiter    │
│ DexScreener │     │  score 0-100 │     │  swap SOL   │
└─────────────┘     └──────────────┘     └─────────────┘
                            │                    │
                    ┌───────▼────────┐   ┌───────▼────────┐
                    │  NEWS (CryptoP)│   │ MONITOR (TP/SL)│
                    └────────────────┘   └───────┬────────┘
                                                 │
                                         ┌───────▼────────┐
                                         │ LEARNER        │
                                         │ ajusta pesos   │
                                         └────────────────┘
```

1. **Scanner** detecta tokens nuevos cada 30s.
2. **Analyzer** los puntúa en 10 dimensiones (seguridad, mercado, momentum, social).
3. Si supera el umbral → **Claude (LLM)** revisa el candidato y confirma o veta con razonamiento.
4. Si Claude confirma → **Trader** compra vía Jupiter.
5. **Monitor** vigila el precio y vende en take-profit (+150%) o stop-loss (-35%).
6. **Learner** ajusta los pesos del análisis según los resultados reales.

> 🧠 **Capa de razonamiento (Claude):** además del análisis algorítmico, el bot
> consulta a Claude (Haiku) como segundo filtro antes de cada compra. Claude
> razona sobre los datos del token y puede **vetar** una compra que el algoritmo
> aprobó. Solo se invoca sobre candidatos (no sobre cada token escaneado), así
> el coste es mínimo. Configúralo con `ANTHROPIC_API_KEY` en `.env`; desactívalo
> con `ENABLE_LLM_REVIEW=false`.

---

## ⚙️ Parámetros principales (`.env`)

| Variable | Defecto | Qué hace |
|----------|---------|----------|
| `ENABLE_TRADING` | `true` | `false` = simulación (paper trading) |
| `MAX_TRADE_PCT` | `0.18` | Máx % del balance por trade |
| `TAKE_PROFIT_PCT` | `1.5` | Vender a +150% |
| `STOP_LOSS_PCT` | `0.35` | Cortar a -35% |
| `MAX_POSITIONS` | `3` | Posiciones simultáneas |
| `MIN_SCORE` | `62` | Puntuación mínima para comprar |
| `MIN_LIQUIDITY_USD` | `4000` | Liquidez mínima |
| `MAX_DAILY_LOSS_PCT` | `0.25` | Para el bot si pierde 25% en un día |

---

## 💶 ¿Cuánto invertir?

Mi recomendación honesta para tu objetivo (50 € → 100-1000 €):

- **Empieza con 50 €**, como dijiste. Es una cantidad razonable para aprender.
- **NO pongas más de lo que puedas perder por completo.** En memecoins eso es
  un escenario real, no teórico.
- Mantén ~0.02 SOL extra en la wallet para comisiones de red.
- **Primera semana en PAPER.** Mira cómo se comporta antes de arriesgar.
- Cuando llegues a 100 €, **retira los 50 € iniciales.** A partir de ahí juegas
  con ganancia y ya no puedes perder tu capital.

---

## 📊 Dashboard

- **Balance y P&L** en SOL y EUR en tiempo real.
- **Posiciones abiertas** con P&L live y barra de progreso TP/SL.
- **Tokens analizados** con puntuación y veredicto (buy/skip/scam).
- **Registro en vivo** de toda la actividad.
- **Historial** de trades cerrados.
- **Aprendizaje:** pesos que el bot ha ajustado.
- **Noticias** y sentimiento de SOL.

---

## 📁 Estructura

```
solana-bot/
├── main.py          # Servidor FastAPI + orquestación + WebSocket
├── config.py        # Carga configuración desde .env
├── models.py        # Modelos de datos (Pydantic)
├── database.py      # SQLite: posiciones, trades, logs, pesos
├── wallet.py        # Firma de transacciones, balances (solders/solana)
├── analyzer.py      # Motor de análisis de 10 capas
├── scanner.py       # Descubrimiento de tokens nuevos
├── trader.py        # Ejecución de swaps vía Jupiter
├── llm_analyst.py   # Capa de razonamiento con Claude (segundo filtro)
├── news.py          # Noticias y análisis de sentimiento
├── learner.py       # Aprendizaje adaptativo (ajuste de pesos)
├── frontend/
│   └── index.html   # Dashboard completo
├── requirements.txt
├── run.bat          # Arranque para Windows
├── .env.example     # Plantilla de configuración
├── MANUAL_CRIPTO.md # Manual de trading (LÉELO)
├── DEPLOY_VPS.md    # Guía para correr 24/7 en un servidor
├── solana-bot.service # Servicio systemd para el VPS
└── README.md
```

## 🖥️ Correr 24/7 en un servidor

Para que el bot vigile tus posiciones aunque tu PC esté apagado, mira
**[DEPLOY_VPS.md](DEPLOY_VPS.md)** — guía paso a paso para desplegarlo en un VPS
barato (~4-6 €/mes) con reinicio automático y acceso seguro al dashboard.

---

## ⚠️ Aviso legal

Software educativo. No es asesoramiento financiero. El trading de criptomonedas,
y en especial de memecoins, conlleva **riesgo de pérdida total**. La mayoría de
traders pierden dinero. Tú eres el único responsable de tu dinero y tus decisiones.
