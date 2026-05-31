# 📖 Manual de Trading de Criptomonedas y Memecoins

> Este manual recoge la lógica que usa el bot y los principios que debes
> entender tú. Léelo entero antes de poner dinero real.

---

## ⚠️ LA VERDAD QUE NADIE TE DICE PRIMERO

Antes de nada, los números reales del trading de memecoins en Solana:

- **Más del 95% de las memecoins van a cero** en semanas o días.
- Estudios on-chain (2024) muestran que **~98% de los tokens de pump.fun**
  nunca llegan a "graduarse" y mueren.
- La mayoría de traders minoristas de memecoins **pierden dinero**. Los que
  ganan suelen ser bots con ventaja de velocidad, insiders o creadores.
- Convertir 50 € en 1.000 € es un **x20**. Es *posible* pero es **apostar**,
  no invertir. Lo más probable estadísticamente es perder parte o todo.

**Por eso este bot está diseñado con gestión de riesgo estricta:** posiciones
pequeñas, stop-loss, límite de pérdida diaria y filtros anti-scam. No para
"hacerte rico seguro" (eso no existe) sino para **darte la mejor probabilidad
posible mientras controlas cuánto puedes perder.**

Regla de oro: **solo pon dinero que estés 100% dispuesto a perder.**

---

## 1. CONCEPTOS BÁSICOS

### Bitcoin vs Altcoins vs Memecoins
| Tipo | Qué es | Riesgo | Ejemplo |
|------|--------|--------|---------|
| **Bitcoin (BTC)** | Reserva de valor digital, la más sólida | Bajo-medio | BTC |
| **Layer 1** | Blockchains con utilidad real | Medio | SOL, ETH |
| **Altcoins serias** | Proyectos con producto | Medio-alto | LINK, UNI |
| **Memecoins** | Sin utilidad, valor = comunidad/hype | **Extremo** | WIF, BONK, PEPE |

El bot opera **memecoins en Solana** porque es donde hay movimientos de x10-x100
en horas. También es donde está el 99% de los scams.

### Términos que debes conocer
- **Liquidez (Liquidity):** dinero en el pool que permite comprar/vender. Poca
  liquidez = no puedes vender = trampa.
- **Market Cap (MC):** valor total del token (precio × supply).
- **Slippage:** diferencia entre el precio esperado y el real al ejecutar.
- **Rug pull:** el creador retira la liquidez y el token vale 0 al instante.
- **Honeypot:** puedes comprar pero el contrato te impide vender.
- **Mint authority:** si está activa, el creador puede imprimir tokens infinitos
  y diluirte a cero.
- **Freeze authority:** si está activa, pueden congelar tus tokens.

---

## 2. CÓMO EL BOT VERIFICA UN TOKEN (Sistema de 10 capas)

Cada token recibe una puntuación de **0 a 100**. Solo compra si supera el umbral
(por defecto 62). Las capas:

### 🛡️ Seguridad (40 pts) — lo más importante
1. **RugCheck (20 pts):** consulta rugcheck.xyz, que detecta contratos maliciosos.
2. **Mint Authority (10 pts):** debe estar **revocada**. Si no → SCAM automático.
3. **Freeze Authority (10 pts):** debe estar **desactivada**. Si no → SCAM automático.

### 📊 Mercado (35 pts)
4. **Liquidez (15 pts):** mínimo configurable (4.000 $ por defecto). Más = mejor.
5. **Distribución de holders (10 pts):** si el top-10 de wallets tiene >45% →
   peligro de que un "ballena" venda y hunda el precio.
6. **Volumen/Market Cap (10 pts):** mucho volumen relativo = actividad real.

### 📈 Momentum (15 pts)
7. **Tendencia de precio (8 pts):** subiendo en 1h y 6h.
8. **Tendencia de volumen (7 pts):** ratio compras/ventas. Más compras = presión alcista.

### 📰 Social (10 pts)
9. **Noticias (5 pts):** sentimiento de noticias (CryptoPanic).
10. **Actividad social (5 pts):** comentarios/engagement en pump.fun.

### Descalificadores automáticos (→ "scam"/"skip" sin importar la puntuación)
- Mint o Freeze authority activos
- Liquidez por debajo del mínimo
- Token de menos de 20 min (demasiado nuevo, sin historial)
- Token de más de 48h (ya pasó el momento)
- Riesgo crítico de RugCheck: honeypot, blacklist, hidden owner, proxy

---

## 3. ESTRATEGIA DE ENTRADA Y SALIDA

### Cuándo COMPRAR (lo hace el bot solo)
- Puntuación ≥ umbral
- Seguridad verificada (sin mint/freeze authority)
- Liquidez suficiente
- Momentum positivo
- Menos posiciones abiertas que el máximo

### Cuándo VENDER (automático — estrategia "house money")
- **Take-profit parcial:** al llegar a **2x (+100%)**, vende la **mitad**. Así
  recuperas tu capital inicial y el resto que queda ya es "dinero gratis". Es la
  regla de oro para no devolver las ganancias.
- **Trailing stop (30%):** tras asegurar la mitad, el resto se deja correr con un
  stop que **sube con el precio**. Si el token hace +400%, capturas gran parte en
  vez de soltar a +150%. Solo vende cuando el precio cae un 30% desde su pico.
- **Stop-loss (-35%):** si la cosa va mal desde el principio, corta la pérdida.
- **Cierre manual:** puedes cerrar cualquier posición desde el dashboard.

> Con esta combinación, un solo acierto grande (un x5 o x10) compensa varias
> pérdidas pequeñas. Es así como, estadísticamente, se sobrevive en memecoins.

### Protecciones antes de comprar
- **Anti-honeypot:** el bot simula una compra Y una venta de vuelta *antes* de
  arriesgar dinero. Si el token no se puede vender (honeypot) o tiene un impuesto
  abusivo, **no compra**.
- **Análisis dev/bundle:** detecta si el creador o un solo holder acumula
  demasiado %, o si hubo insiders/snipers en el lanzamiento.
- **Filtro de Claude (IA):** Claude razona sobre todos los datos y puede vetar
  una compra que el algoritmo aprobó.

### Reglas de gestión de capital
- **Máximo 18% del balance** por operación (nunca lo apuestes todo).
- **Máximo 3 posiciones** simultáneas (diversificar).
- **Límite de pérdida diaria del 25%:** si pierdes eso en un día, el bot para.

> Estas reglas existen porque en memecoins **la supervivencia es la estrategia.**
> Si proteges el capital, basta con que 1 de cada 4 trades sea un x3 para ir ganando.

---

## 4. LO QUE NUNCA DEBES HACER

❌ **FOMO:** comprar algo solo porque está subiendo mucho y "no quieres perdértelo".
   Normalmente compras justo en el techo.
❌ **Comprar sin verificar liquidez.** Si no puedes vender, da igual cuánto suba.
❌ **Ignorar el mint authority.** Es la trampa #1.
❌ **Poner todo en un token.** Una sola posición puede ir a cero.
❌ **Perseguir pérdidas** ("revenge trading"). El bot lo evita con el límite diario.
❌ **Confiar en grupos de Telegram/influencers que "recomiendan" tokens.**
   Casi siempre es pump & dump: ellos compran antes, tú compras, ellos venden.
❌ **Dejar la clave privada de tu wallet principal en el bot.** Usa una wallet
   secundaria y dedicada solo a esto.

---

## 5. LO QUE SÍ DEBES HACER

✅ Empezar en **modo PAPER** (simulación) para ver cómo se comporta sin arriesgar.
✅ Usar una **wallet dedicada** con solo el dinero que vas a tradear.
✅ **Retirar ganancias.** Si llegas a 100 €, saca tus 50 € iniciales y juega solo
   con la ganancia. Así ya no puedes "perder".
✅ Revisar el registro de aprendizaje: el bot ajusta su estrategia con cada trade.
✅ Configurar las APIs gratuitas (Helius, CryptoPanic) para mejor información.
✅ Entender que **esto es de alto riesgo** y actuar en consecuencia.

---

## 6. CÓMO APRENDE EL BOT

Después de cada trade cerrado, el bot:
1. Guarda qué puntuación tenía cada factor (liquidez, seguridad, momentum...).
2. Calcula la **correlación** entre cada factor y si el trade fue ganador.
3. **Aumenta el peso** de los factores que predicen ganancias y **reduce** los
   que no sirven.
4. Con el tiempo, su análisis se adapta a lo que realmente funciona *en el
   mercado actual*.

Esto se ve en la pestaña "Aprendizaje" del dashboard. No es magia ni una red
neuronal gigante: es estadística honesta sobre tus propios resultados.

---

## 7. ESTRATEGIA PARA 50 € → 100 € → 1.000 €

Plan realista por fases:

**Fase 1 (50 € → 100 €):** Conservador. Stop-loss ajustado, solo tokens con
score alto. Objetivo: duplicar sin reventar la cuenta. *Probabilidad: media.*

**Fase 2 (100 € → 300 €):** Retira tus 50 € originales. Ahora juegas con
ganancia. Puedes permitir algo más de riesgo.

**Fase 3 (300 € → 1.000 €):** Necesitas aciertos grandes (x3-x5). Aquí la suerte
pesa mucho. *Probabilidad: baja pero no imposible.*

**La matemática honesta:** llegar a 1.000 € desde 50 € requiere encadenar varios
aciertos grandes sin un fallo catastrófico. El bot maximiza tus probabilidades,
pero **nadie puede garantizar este resultado.** Trátalo como una apuesta con
ventaja, no como una cuenta de ahorros.

---

## 8. HERRAMIENTAS QUE USA EL BOT

| Herramienta | Para qué | Gratis |
|-------------|----------|--------|
| **Jupiter** | Ejecutar swaps al mejor precio | ✅ |
| **DexScreener** | Precio, liquidez, volumen, edad | ✅ |
| **RugCheck** | Detección de scams, authorities, holders | ✅ |
| **pump.fun** | Nuevos tokens, actividad social | ✅ |
| **CryptoPanic** | Noticias y sentimiento | ✅ (con API key) |
| **CoinGecko** | Precio SOL/EUR | ✅ |
| **Helius** | RPC rápido (recomendado) | ✅ hasta 1M req/mes |

---

*Este manual es educativo. No es asesoramiento financiero. El trading de
criptomonedas conlleva riesgo de pérdida total. Tú eres responsable de tus
decisiones y de tu dinero.*
