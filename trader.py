"""
Ejecutor de trades via Jupiter V6 Aggregator.
Jupiter es el mejor DEX aggregator de Solana - encuentra el mejor precio
entre Raydium, Orca, Meteora, etc.
"""

import asyncio
import math
from typing import Optional, Dict, Tuple
import httpx

import config
import wallet
from database import add_log


async def get_sol_price_usd() -> float:
    """Fetch current SOL price in USD from CoinGecko"""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                f"{config.COINGECKO_URL}/simple/price",
                params={"ids": "solana", "vs_currencies": "usd,eur"}
            )
            if r.status_code == 200:
                data = r.json()
                return float(data.get("solana", {}).get("usd", 0))
    except Exception:
        pass
    return 0.0


async def get_sol_price_eur() -> float:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                f"{config.COINGECKO_URL}/simple/price",
                params={"ids": "solana", "vs_currencies": "eur"}
            )
            if r.status_code == 200:
                data = r.json()
                return float(data.get("solana", {}).get("eur", 0))
    except Exception:
        pass
    return 0.0


async def get_jupiter_quote(
    input_mint: str,
    output_mint: str,
    amount_lamports: int,
    slippage_bps: int = None
) -> Optional[Dict]:
    if slippage_bps is None:
        slippage_bps = config.SLIPPAGE_BPS

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.get(
                config.JUPITER_QUOTE_URL,
                params={
                    "inputMint": input_mint,
                    "outputMint": output_mint,
                    "amount": str(amount_lamports),
                    "slippageBps": str(slippage_bps),
                    "onlyDirectRoutes": "false",
                    "asLegacyTransaction": "false",
                }
            )
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            add_log("ERROR", f"Jupiter quote error: {e}")
    return None


async def get_jupiter_swap_transaction(quote: Dict, public_key: str) -> Optional[str]:
    """Returns base64 encoded swap transaction"""
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(
                config.JUPITER_SWAP_URL,
                json={
                    "quoteResponse": quote,
                    "userPublicKey": public_key,
                    "wrapAndUnwrapSol": True,
                    "dynamicComputeUnitLimit": True,
                    "prioritizationFeeLamports": 5000,  # ~0.000005 SOL priority fee
                    "asLegacyTransaction": False,
                }
            )
            if r.status_code == 200:
                return r.json().get("swapTransaction")
        except Exception as e:
            add_log("ERROR", f"Jupiter swap build error: {e}")
    return None


async def buy_token(
    token_address: str,
    sol_amount: float,
    max_retries: int = 3
) -> Tuple[bool, float, float, str]:
    """
    Buy a token with SOL via Jupiter.
    Returns (success, tokens_received, price_usd, tx_signature)
    """
    if not config.ENABLE_TRADING:
        add_log("INFO", f"[PAPER] Simulando compra de {token_address} por {sol_amount:.4f} SOL")
        # price_usd=0.0 -> on_buy_signal usa el precio REAL de mercado como entrada,
        # asi la simulacion (y el aprendizaje) trabajan con datos reales.
        return True, sol_amount * 1_000_000, 0.0, "paper_trade_buy"

    lamports = int(sol_amount * 1e9)
    public_key = wallet.get_public_key_str()

    for attempt in range(max_retries):
        slippage = config.SLIPPAGE_BPS + attempt * 200  # Increase slippage on retry

        quote = await get_jupiter_quote(config.SOL_MINT, token_address, lamports, slippage)
        if not quote:
            add_log("WARNING", f"No hay cotizacion para {token_address} (intento {attempt+1})")
            await asyncio.sleep(2)
            continue

        out_amount = int(quote.get("outAmount", 0))
        if out_amount == 0:
            add_log("WARNING", f"Quote sin tokens de salida para {token_address}")
            break

        swap_tx_b64 = await get_jupiter_swap_transaction(quote, public_key)
        if not swap_tx_b64:
            add_log("WARNING", f"No se pudo construir tx para {token_address}")
            await asyncio.sleep(2)
            continue

        try:
            signature = await wallet.sign_and_send_transaction(swap_tx_b64)
            confirmed = await wallet.confirm_transaction(signature, timeout_seconds=60)

            if confirmed:
                # Calculate approximate price from quote
                price_impact = float(quote.get("priceImpactPct", 0))
                out_decimals = 6  # Most Solana tokens use 6 decimals
                tokens_received = out_amount / (10 ** out_decimals)
                price_usd = (sol_amount * await get_sol_price_usd()) / tokens_received if tokens_received > 0 else 0

                add_log("TRADE", f"COMPRA exitosa: {sol_amount:.4f} SOL -> {tokens_received:.0f} tokens | TX: {signature[:16]}... | Impacto: {price_impact:.2f}%")
                return True, tokens_received, price_usd, signature
            else:
                add_log("WARNING", f"Tx no confirmada (intento {attempt+1}): {signature[:16]}...")

        except Exception as e:
            add_log("ERROR", f"Error ejecutando compra (intento {attempt+1}): {e}")
            await asyncio.sleep(3)

    return False, 0.0, 0.0, ""


async def sell_token(
    token_address: str,
    token_amount: float,
    decimals: int = 6,
    max_retries: int = 3
) -> Tuple[bool, float, str]:
    """
    Sell all tokens back to SOL via Jupiter.
    Returns (success, sol_received, tx_signature)
    """
    if not config.ENABLE_TRADING:
        add_log("INFO", f"[PAPER] Simulando venta de {token_amount:.0f} tokens de {token_address}")
        # sol_received=0.0 -> el cierre usa el precio REAL actual como precio de venta,
        # de modo que el P&L simulado refleja el movimiento real del mercado.
        return True, 0.0, "paper_trade_sell"

    token_lamports = int(token_amount * (10 ** decimals))
    if token_lamports <= 0:
        add_log("WARNING", f"Cantidad de tokens invalida: {token_amount}")
        return False, 0.0, ""

    public_key = wallet.get_public_key_str()

    for attempt in range(max_retries):
        slippage = config.SLIPPAGE_BPS + attempt * 300

        quote = await get_jupiter_quote(token_address, config.SOL_MINT, token_lamports, slippage)
        if not quote:
            add_log("WARNING", f"No hay cotizacion para vender {token_address} (intento {attempt+1})")
            if attempt == max_retries - 1:
                add_log("ERROR", f"SIN LIQUIDEZ para vender {token_address} - posible rug")
            await asyncio.sleep(3)
            continue

        out_lamports = int(quote.get("outAmount", 0))
        sol_received = out_lamports / 1e9

        swap_tx_b64 = await get_jupiter_swap_transaction(quote, public_key)
        if not swap_tx_b64:
            await asyncio.sleep(2)
            continue

        try:
            signature = await wallet.sign_and_send_transaction(swap_tx_b64)
            confirmed = await wallet.confirm_transaction(signature, timeout_seconds=60)

            if confirmed:
                add_log("TRADE", f"VENTA exitosa: {token_amount:.0f} tokens -> {sol_received:.4f} SOL | TX: {signature[:16]}...")
                return True, sol_received, signature
            else:
                add_log("WARNING", f"Tx venta no confirmada (intento {attempt+1})")

        except Exception as e:
            add_log("ERROR", f"Error ejecutando venta (intento {attempt+1}): {e}")
            await asyncio.sleep(3)

    return False, 0.0, ""


async def swap_sol_to_usdc(sol_amount: float) -> Tuple[bool, float, str]:
    """Convierte SOL -> USDC (proteccion en mercado bajista). (success, usdc_received, sig)"""
    if not config.ENABLE_TRADING:
        price = await get_sol_price_usd()
        if price <= 0:
            price = config.PAPER_SOL_PRICE_USD
        add_log("INFO", f"[PAPER] Simulando SOL->USDC: {sol_amount:.4f} SOL -> ${sol_amount*price:.2f}")
        return True, sol_amount * price, "paper_swap"
    ok, usdc_received, _price, sig = await buy_token(config.USDC_MINT, sol_amount)
    return ok, usdc_received, sig


async def swap_usdc_to_sol(usdc_amount: float) -> Tuple[bool, float, str]:
    """Convierte USDC -> SOL (recompra en mercado alcista). (success, sol_received, sig)"""
    if not config.ENABLE_TRADING:
        price = await get_sol_price_usd()
        if price <= 0:
            price = config.PAPER_SOL_PRICE_USD
        sol = (usdc_amount / price) if price > 0 else 0.0
        add_log("INFO", f"[PAPER] Simulando USDC->SOL: ${usdc_amount:.2f} -> {sol:.4f} SOL")
        return True, sol, "paper_swap"
    return await sell_token(config.USDC_MINT, usdc_amount, decimals=6)


async def check_sellable(token_address: str, sol_amount: float) -> Tuple[bool, str]:
    """
    Anti-honeypot: simula una compra y la venta de vuelta usando SOLO cotizaciones
    de Jupiter (sin gastar dinero). Detecta tokens que no se pueden vender o con
    impuestos abusivos. Devuelve (es_seguro, motivo_si_no).
    """
    lamports = int(sol_amount * 1e9)

    # 1. ¿Hay ruta de COMPRA? (SOL -> token)
    buy_q = await get_jupiter_quote(config.SOL_MINT, token_address, lamports)
    if not buy_q or int(buy_q.get("outAmount", 0)) == 0:
        return False, "Sin ruta de compra (liquidez nula)"
    tokens_out = int(buy_q.get("outAmount", 0))

    # 2. ¿Hay ruta de VENTA de vuelta? (token -> SOL). Si no, es honeypot.
    sell_q = await get_jupiter_quote(token_address, config.SOL_MINT, tokens_out)
    if not sell_q or int(sell_q.get("outAmount", 0)) == 0:
        return False, "No se puede vender de vuelta (posible HONEYPOT)"

    # 3. Perdida ida y vuelta. Una perdida enorme = impuesto oculto / honeypot.
    sol_back = int(sell_q.get("outAmount", 0)) / 1e9
    roundtrip_loss = (sol_amount - sol_back) / sol_amount if sol_amount > 0 else 1.0
    if roundtrip_loss > config.HONEYPOT_MAX_ROUNDTRIP_LOSS_PCT:
        return False, f"Perdida ida/vuelta {roundtrip_loss*100:.0f}% (impuesto alto / honeypot)"

    return True, ""


def calculate_position_size(sol_balance: float) -> float:
    """
    Dynamic position sizing based on current balance.
    Never risk more than MAX_TRADE_PCT, never less than MIN_TRADE_SOL.
    """
    size = sol_balance * config.MAX_TRADE_PCT
    size = max(config.MIN_TRADE_SOL, min(size, sol_balance * 0.4))  # Hard cap at 40%
    return round(size, 4)
