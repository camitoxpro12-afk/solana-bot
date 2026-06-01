"""
Solana Trading Bot - Backend principal
FastAPI + WebSocket para dashboard en tiempo real
"""

import asyncio
import json
import time
from typing import Set, Optional
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import httpx

import config
import database as db
from models import BotStatus, TokenAnalysis
from scanner import scanner_loop
from trader import (get_sol_price_eur, get_sol_price_usd, calculate_position_size,
                    buy_token, sell_token, check_sellable, swap_sol_to_usdc, swap_usdc_to_sol)
from learner import run_learning_cycle, get_learning_summary
from news import news_loop, get_recent_news, get_sol_sentiment, refresh_news
from analyzer import fetch_dexscreener
import llm_analyst
import wallet


# ── Global bot state ───────────────────────────────────────────────────────────

class BotEngine:
    def __init__(self):
        self.running = False
        self.stop_event = asyncio.Event()
        self.tasks: list = []
        self.ws_clients: Set[WebSocket] = set()
        self.sol_price_usd: float = 0.0
        self.sol_price_eur: float = 0.0
        self.price_cache_time: float = 0.0
        self._position_monitor_task: Optional[asyncio.Task] = None
        self._ai_exit_reviews: dict[int, int] = {}

    async def broadcast(self, msg_type: str, data: dict):
        """Send message to all connected WebSocket clients"""
        if not self.ws_clients:
            return
        payload = json.dumps({"type": msg_type, "data": data})
        dead = set()
        for ws in self.ws_clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        self.ws_clients -= dead

    async def broadcast_log(self, level: str, message: str):
        db.add_log(level, message)
        await self.broadcast("log", {"level": level, "message": message, "ts": time.strftime("%H:%M:%S")})

    def _sync_log(self, level: str, message: str):
        """Log sincrono (para callbacks que no pueden await, p.ej. coste LLM)."""
        db.add_log(level, message)

    async def get_sol_prices(self) -> tuple[float, float]:
        now = time.time()
        if now - self.price_cache_time > config.PRICE_CACHE_SECONDS:
            try:
                self.sol_price_usd = await get_sol_price_usd()
                self.sol_price_eur = await get_sol_price_eur()
                self.price_cache_time = now
            except Exception:
                pass
        return self.sol_price_usd, self.sol_price_eur

    async def _get_free_sol(self) -> float:
        """SOL LIBRE (disponible para comprar). Real: balance real de la wallet.
        Paper: 50€ iniciales + P&L realizado - lo comprometido en posiciones abiertas."""
        if config.ENABLE_TRADING:
            if not config.PRIVATE_KEY:
                return 0.0
            try:
                return await wallet.get_sol_balance()
            except Exception:
                return 0.0
        _, sol_eur = await self.get_sol_prices()
        start = db.get_state("paper_start_sol", "")
        if start:
            start_sol = float(start)
        else:
            start_sol = (config.PAPER_START_EUR / sol_eur) if sol_eur > 0 else 0.0
            if start_sol > 0:
                db.set_state("paper_start_sol", str(start_sol))
        committed = sum(p.get("amount_sol", 0) for p in db.get_open_positions())
        return max(0.0, start_sol + db.get_total_pnl() - committed)

    async def get_status(self) -> dict:
        sol_usd, sol_eur = await self.get_sol_prices()
        free_sol = await self._get_free_sol()

        positions = db.get_open_positions()
        trades = db.get_trades(100)
        wins = sum(1 for t in trades if t.get("pnl_sol", 0) > 0)

        # Valor actual de las posiciones abiertas + P&L no realizado (a precio de mercado)
        invested = 0.0
        unrealized = 0.0
        for p in positions:
            bp = p.get("buy_price_usd") or 0
            cp = p.get("current_price_usd") or bp
            cur_val = p["amount_sol"] * (cp / bp) if bp > 0 else p.get("amount_sol", 0)
            invested += cur_val
            unrealized += cur_val - p.get("amount_sol", 0)
        equity = free_sol + invested  # patrimonio total

        return {
            "running": self.running,
            "mode": "live" if config.ENABLE_TRADING else "paper",
            "sol_balance": round(equity, 4),
            "eur_balance": round(equity * sol_eur, 2),
            "free_sol": round(free_sol, 4),
            "free_eur": round(free_sol * sol_eur, 2),
            "invested_sol": round(invested, 4),
            "invested_eur": round(invested * sol_eur, 2),
            "unrealized_pnl_sol": round(unrealized, 4),
            "unrealized_pnl_eur": round(unrealized * sol_eur, 2),
            "sol_price_eur": round(sol_eur, 2),
            "sol_price_usd": round(sol_usd, 2),
            "total_pnl_sol": round(db.get_total_pnl(), 4),
            "daily_pnl_sol": round(db.get_daily_pnl(), 4),
            "open_positions": len(positions),
            "total_trades": len(trades),
            "win_rate": round(wins / len(trades) * 100, 1) if trades else 0.0,
            "tokens_analyzed_today": db.get_tokens_analyzed_today(),
            "has_wallet": bool(config.PRIVATE_KEY),
        }

    # ── Token found callback ──────────────────────────────────────────────────

    async def on_token_found(self, token_data: dict):
        await self.broadcast("new_token", token_data)

    # ── Buy signal callback ───────────────────────────────────────────────────

    async def on_buy_signal(self, analysis: TokenAnalysis):
        positions = db.get_open_positions()
        if len(positions) >= config.MAX_POSITIONS:
            await self.broadcast_log("INFO", f"Max posiciones alcanzado ({config.MAX_POSITIONS}), omitiendo {analysis.symbol}")
            return

        # Check daily loss limit
        daily_pnl = db.get_daily_pnl()
        sol_bal = await self._get_free_sol()
        if daily_pnl < -(sol_bal * config.MAX_DAILY_LOSS_PCT):
            await self.broadcast_log("WARNING", "Limite de perdida diaria alcanzado - trading pausado")
            return

        # Check we already have a position in this token
        if any(p["token_address"] == analysis.address for p in positions):
            return

        # ── FILTRO DE MERCADO: no comprar memecoins si SOL esta bajista ────
        if config.ENABLE_MARKET_FILTER:
            try:
                import sol_market
                m = await sol_market.get_sol_market(with_llm=False)
                score = m.get("signal_score", 50)
                if score < config.MARKET_FILTER_MIN_SCORE:
                    await self.broadcast_log(
                        "INFO",
                        f"Mercado SOL bajista ({score}/100) - evito comprar {analysis.symbol} (las memecoins caen mas en rojo)"
                    )
                    return
            except Exception:
                pass

        # ── ANTI-FOMO: no comprar el techo de un pump ─────────────────────
        if analysis.price_change_1h and analysis.price_change_1h > config.MAX_PUMP_1H_PCT:
            await self.broadcast_log(
                "INFO",
                f"Evito {analysis.symbol}: ya subio +{analysis.price_change_1h:.0f}% en 1h (riesgo de comprar el techo)"
            )
            return

        # ── SEGUNDO FILTRO: razonamiento LLM (Claude) ──────────────────────
        if llm_analyst.is_enabled():
            await self.broadcast_log("INFO", f"Consultando a la IA sobre {analysis.symbol}...")
            verdict = await llm_analyst.llm_review(analysis, self._sync_log)
            if verdict:
                decision = verdict["decision"]
                conf = verdict["confidence"]
                reasoning = verdict["reasoning"]
                risks = verdict.get("key_risks", [])

                # Un solo mensaje (persistido) con el veredicto de la IA
                if decision == "veto":
                    risks_txt = f" · Riesgos: {', '.join(risks[:3])}" if risks else ""
                    await self.broadcast_log(
                        "WARNING",
                        f"🧠⛔ La IA VETÓ {analysis.symbol} ({conf}%): {reasoning}{risks_txt}"
                    )
                    return
                if conf < config.LLM_MIN_CONFIDENCE:
                    await self.broadcast_log(
                        "INFO",
                        f"🧠 La IA aprobó {analysis.symbol} pero con confianza baja ({conf}% < {config.LLM_MIN_CONFIDENCE:.0f}%) - omitido"
                    )
                    return
                await self.broadcast_log(
                    "TRADE",
                    f"🧠✅ La IA confirmó {analysis.symbol} ({conf}%): {reasoning}"
                )

        trade_sol = calculate_position_size(sol_bal)

        # ── ANTI-HONEYPOT: simula compra+venta antes de arriesgar dinero ──
        if config.ENABLE_HONEYPOT_CHECK:
            sellable, hp_reason = await check_sellable(analysis.address, trade_sol)
            if not sellable:
                await self.broadcast_log(
                    "WARNING",
                    f"Anti-honeypot BLOQUEO {analysis.symbol}: {hp_reason}"
                )
                return

        await self.broadcast_log(
            "TRADE",
            f"Ejecutando COMPRA: {analysis.name} ({analysis.symbol}) | Score: {analysis.scores.total:.1f} | {trade_sol:.4f} SOL"
        )

        success, tokens_received, price_usd, sig = await buy_token(analysis.address, trade_sol)

        if not success:
            await self.broadcast_log("ERROR", f"Compra fallida: {analysis.name}")
            return

        if price_usd == 0 and analysis.price_usd > 0:
            price_usd = analysis.price_usd

        target_price = price_usd * (1 + config.TAKE_PROFIT_PCT)
        stop_price = price_usd * (1 - config.STOP_LOSS_PCT)

        scores_dict = analysis.scores.model_dump() if analysis.scores else {}
        position_id = db.open_position(
            token_address=analysis.address,
            token_name=analysis.name,
            token_symbol=analysis.symbol,
            buy_price_usd=price_usd,
            buy_price_sol=analysis.price_sol,
            amount_sol=trade_sol,
            amount_tokens=tokens_received,
            target_price_usd=target_price,
            stop_price_usd=stop_price,
            scores=scores_dict,
            image_url=analysis.image_url,
        )

        await self.broadcast("position_opened", {
            "id": position_id,
            "token_name": analysis.name,
            "token_symbol": analysis.symbol,
            "token_address": analysis.address,
            "buy_price_usd": price_usd,
            "amount_sol": trade_sol,
            "target_price_usd": target_price,
            "stop_price_usd": stop_price,
            "score": analysis.scores.total,
            "tx": sig[:16] + "..." if sig and sig != "paper_trade_buy" else sig,
        })

        # Update balance broadcast
        await self.broadcast("balance_update", await self.get_status())

    # ── Position monitor ──────────────────────────────────────────────────────

    async def monitor_positions(self):
        """Polls DexScreener prices and triggers TP/SL"""
        while not self.stop_event.is_set():
            try:
                positions = db.get_open_positions()
                if positions:
                    addresses = [p["token_address"] for p in positions]
                    prices = await self._batch_prices(addresses)

                    for pos in positions:
                        addr = pos["token_address"]
                        current_price = prices.get(addr, 0)
                        if current_price <= 0:
                            continue

                        db.update_position_price(pos["id"], current_price)
                        buy_price = pos["buy_price_usd"]
                        pnl_pct = (current_price - buy_price) / buy_price * 100 if buy_price > 0 else 0

                        # Maximo historico (para trailing stop)
                        previous_high = pos.get("highest_price_usd", 0) or 0
                        highest = max(previous_high, current_price)
                        if highest > previous_high:
                            db.update_position_high(pos["id"], current_price)

                        await self.broadcast("position_update", {
                            "id": pos["id"],
                            "token_symbol": pos["token_symbol"],
                            "current_price_usd": current_price,
                            "pnl_pct": round(pnl_pct, 2),
                        })

                        # ── TAKE-PROFIT PARCIAL: vende una fraccion al llegar a Nx ──
                        if (config.ENABLE_PARTIAL_TP and not pos.get("partial_taken")
                                and buy_price > 0
                                and current_price >= buy_price * (1 + config.PARTIAL_TP_TRIGGER_PCT)):
                            await self._partial_take_profit(pos, current_price)
                            continue  # posicion modificada; re-evalua en el proximo ciclo

                        # ── Calcular stop efectivo (trailing si esta en beneficio) ──
                        effective_stop = pos["stop_price_usd"]
                        trailing_active = False
                        if config.ENABLE_TRAILING_STOP and highest > buy_price:
                            trail_stop = highest * (1 - config.TRAILING_STOP_PCT)
                            if trail_stop > effective_stop:
                                effective_stop = trail_stop
                                trailing_active = True

                        # ── Decidir salida ──
                        # El objetivo (take-profit) se respeta si la IA gestiona los niveles
                        # (asi se ejecuta su target al instante) o si no hay trailing.
                        ai_managed = (config.ENABLE_AI_EXIT and config.ENABLE_AI_DYNAMIC_LEVELS
                                      and llm_analyst.is_enabled())
                        take_profit_active = (not config.ENABLE_TRAILING_STOP) or ai_managed
                        target = pos.get("target_price_usd") or 0
                        if take_profit_active and target > 0 and current_price >= target:
                            await self._close_position(pos, current_price, "take_profit")
                        elif current_price <= effective_stop:
                            reason = "trailing_stop" if trailing_active else "stop_loss"
                            await self._close_position(pos, current_price, reason)

                await self.broadcast("balance_update", await self.get_status())

            except Exception as e:
                db.add_log("ERROR", f"Error monitoreando posiciones: {e}")

            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=config.PRICE_CHECK_INTERVAL)
            except asyncio.TimeoutError:
                pass

    async def _batch_prices(self, addresses: list) -> dict:
        """Fetch current prices for multiple tokens via DexScreener"""
        prices = {}
        async with httpx.AsyncClient(timeout=8) as client:
            for addr in addresses:
                try:
                    r = await client.get(
                        f"{config.DEXSCREENER_URL}/latest/dex/tokens/{addr}"
                    )
                    if r.status_code == 200:
                        data = r.json()
                        pairs = [p for p in (data.get("pairs") or []) if p.get("chainId") == "solana"]
                        if pairs:
                            best = max(pairs, key=lambda p: p.get("liquidity", {}).get("usd", 0) or 0)
                            prices[addr] = float(best.get("priceUsd") or 0)
                except Exception:
                    pass
        return prices

    async def _partial_take_profit(self, pos: dict, current_price: float):
        """Vende una fraccion de la posicion para asegurar capital y deja correr el resto."""
        fraction = config.PARTIAL_TP_SELL_FRACTION
        sell_tokens = pos["amount_tokens"] * fraction
        buy_price = pos["buy_price_usd"]

        success, sol_received, sig = await sell_token(pos["token_address"], sell_tokens)
        if not success:
            await self.broadcast_log("WARNING", f"Venta parcial fallida en {pos['token_symbol']} - se reintenta")
            return

        # Calcula precio de venta real si tenemos SOL recibido
        sell_price = current_price
        if sol_received > 0 and sell_tokens > 0:
            sol_usd, _ = await self.get_sol_prices()
            if sol_usd > 0:
                sell_price = (sol_received / sell_tokens) * sol_usd

        # Mueve el stop del resto a breakeven (precio de compra): el resto ya es "gratis"
        new_stop = max(pos["stop_price_usd"], buy_price)
        result = db.partial_sell(pos["id"], fraction, sell_price, new_stop)

        pnl_pct = (sell_price - buy_price) / buy_price * 100 if buy_price > 0 else 0
        await self.broadcast_log(
            "TRADE",
            f"TAKE-PROFIT PARCIAL {pos['token_symbol']}: vendido {fraction*100:.0f}% a +{pnl_pct:.0f}% "
            f"| stop del resto movido a breakeven, dejando correr el resto"
        )
        if result:
            await self.broadcast("trade_closed", {
                "token_name": pos["token_name"],
                "token_symbol": pos["token_symbol"],
                "reason": "partial_tp",
                "pnl_sol": round(result.get("pnl_sol", 0), 4),
                "pnl_pct": round(result.get("pnl_pct", 0), 1),
                "tx": sig[:16] + "..." if sig and "paper" not in sig else sig,
            })
        await self.broadcast("balance_update", await self.get_status())

    async def _close_position(self, pos: dict, current_price: float, reason: str):
        await self.broadcast_log(
            "TRADE",
            f"Cerrando {pos['token_symbol']}: {reason.upper()} | "
            f"Compra: ${pos['buy_price_usd']:.6f} -> Ahora: ${current_price:.6f}"
        )

        # Try to sell
        success, sol_received, sig = await sell_token(
            pos["token_address"],
            pos["amount_tokens"],
        )

        sell_price = current_price
        if success and sol_received > 0 and pos["amount_sol"] > 0:
            sol_usd, _ = await self.get_sol_prices()
            sell_price = (sol_received / pos["amount_tokens"]) * sol_usd if pos["amount_tokens"] > 0 else current_price

        result = db.close_position(pos["id"], sell_price, reason)
        if result:
            self._ai_exit_reviews.pop(int(pos["id"]), None)
            pnl = result.get("pnl_sol", 0)
            pnl_pct = result.get("pnl_pct", 0)
            emoji = "+" if pnl > 0 else ""
            await self.broadcast("trade_closed", {
                "token_name": pos["token_name"],
                "token_symbol": pos["token_symbol"],
                "reason": reason,
                "pnl_sol": round(pnl, 4),
                "pnl_pct": round(pnl_pct, 1),
                "tx": sig[:16] + "..." if sig and "paper" not in sig else sig,
            })
            await self.broadcast_log(
                "TRADE",
                f"Trade cerrado: {pos['token_symbol']} | P&L: {emoji}{pnl:.4f} SOL ({emoji}{pnl_pct:.1f}%)"
            )

            # ANTI-RUG: si la perdida es enorme (rug pull), a la LISTA NEGRA para
            # no volver a comprar esa moneda jamas.
            if pnl_pct <= -config.BLACKLIST_LOSS_PCT:
                db.add_to_blacklist(
                    pos["token_address"], pos.get("token_symbol", ""),
                    f"Rug/perdida grande {pnl_pct:.0f}% ({reason})"
                )
                await self.broadcast_log(
                    "WARNING",
                    f"🚫 {pos['token_symbol']} a la LISTA NEGRA (perdida {pnl_pct:.0f}%) - no se volvera a comprar"
                )
            # GANADORA: guardar como FAVORITA para vigilarla y volver a entrar en su proxima bajada
            elif pnl_pct >= config.FAVORITE_MIN_WIN_PCT and not db.is_blacklisted(pos["token_address"]):
                db.add_favorite(
                    pos["token_address"], pos.get("token_symbol", ""),
                    pos.get("token_name", ""), round(pnl_pct, 1)
                )
                await self.broadcast_log(
                    "INFO",
                    f"⭐ {pos['token_symbol']} guardada como FAVORITA (gano {pnl_pct:+.0f}%) - se vigilara para re-entrar"
                )

            # Trigger learning after each trade
            run_learning_cycle()

    # ── Swing trading de SOL (rota SOL <-> USDC segun el mercado) ───────────────

    async def sol_swing_loop(self):
        import sol_market
        while not self.stop_event.is_set():
            try:
                if config.ENABLE_SOL_SWING:
                    await self._check_sol_swing(sol_market)
            except Exception as e:
                db.add_log("ERROR", f"Error en swing de SOL: {e}")
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=config.SOL_SWING_INTERVAL)
            except asyncio.TimeoutError:
                pass

    async def _check_sol_swing(self, sol_market):
        m = await sol_market.get_sol_market(with_llm=False)
        if m.get("error"):
            return
        score = m.get("signal_score", 50)
        sol_price = m.get("price_usd", 0)
        state = db.get_state("sol_swing_state", "risk_on")

        # Mercado bajista y estamos en SOL -> proteger en USDC
        if state == "risk_on" and score <= config.SOL_SWING_EXIT_SCORE:
            sol_bal = await self._get_free_sol()
            amt = round(sol_bal * config.SOL_SWING_PCT, 4)
            if amt <= 0:
                return
            ok, usdc, sig = await swap_sol_to_usdc(amt)
            if ok and usdc > 0:
                db.set_state("sol_swing_state", "risk_off")
                db.set_state("sol_swing_usdc", str(usdc))
                db.set_state("sol_swing_sol_parked", str(amt))
                db.set_state("sol_swing_entry_price", str(sol_price))
                await self.broadcast_log(
                    "TRADE",
                    f"SWING SOL: mercado bajista ({score}/100) -> protegidos {amt:.4f} SOL en USDC (${usdc:.2f})"
                )
                await self.broadcast("balance_update", await self.get_status())

        # Mercado alcista y estamos en USDC -> recomprar SOL
        elif state == "risk_off" and score >= config.SOL_SWING_ENTER_SCORE:
            usdc = float(db.get_state("sol_swing_usdc", "0"))
            parked_sol = float(db.get_state("sol_swing_sol_parked", "0"))
            if usdc <= 0:
                db.set_state("sol_swing_state", "risk_on")
                return
            ok, sol_recv, sig = await swap_usdc_to_sol(usdc)
            if ok and sol_recv > 0:
                db.set_state("sol_swing_state", "risk_on")
                db.set_state("sol_swing_usdc", "0")
                res = db.record_swing_trade(parked_sol, sol_recv, sol_price)
                pnl = res["pnl_sol"]
                emoji = "+" if pnl >= 0 else ""
                await self.broadcast_log(
                    "TRADE",
                    f"SWING SOL: mercado alcista ({score}/100) -> recompra SOL. "
                    f"Resultado: {emoji}{pnl:.4f} SOL ({emoji}{res['pnl_pct']:.1f}%)"
                )
                await self.broadcast("trade_closed", {
                    "token_name": "Solana", "token_symbol": "SOL", "reason": "sol_swing",
                    "pnl_sol": round(pnl, 4), "pnl_pct": round(res["pnl_pct"], 1), "tx": sig,
                })
                await self.broadcast("balance_update", await self.get_status())

    # ── Revision de salida con IA (hibrido: complementa las reglas) ─────────────

    async def ai_exit_loop(self):
        while not self.stop_event.is_set():
            try:
                if config.ENABLE_AI_EXIT and llm_analyst.is_enabled():
                    await self._ai_review_positions()
            except Exception as e:
                db.add_log("ERROR", f"Error en revision IA de salidas: {e}")
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=config.AI_EXIT_INTERVAL)
            except asyncio.TimeoutError:
                pass

    async def _position_market(self, address: str) -> dict:
        """Lee la grafica/momentum en vivo de la moneda (para que la IA decida con datos)."""
        try:
            pair = await fetch_dexscreener(address)
        except Exception:
            pair = None
        if not pair:
            return {}
        pc = pair.get("priceChange", {}) or {}
        txns = (pair.get("txns", {}) or {}).get("h1", {}) or {}
        return {
            "change_m5": pc.get("m5", 0) or 0,
            "change_h1": pc.get("h1", 0) or 0,
            "change_h6": pc.get("h6", 0) or 0,
            "change_h24": pc.get("h24", 0) or 0,
            "buys_h1": txns.get("buys", 0) or 0,
            "sells_h1": txns.get("sells", 0) or 0,
            "volume_h1": (pair.get("volume", {}) or {}).get("h1", 0) or 0,
            "liquidity": (pair.get("liquidity", {}) or {}).get("usd", 0) or 0,
        }

    async def _ai_review_positions(self):
        for pos in db.get_open_positions():
            market = await self._position_market(pos["token_address"])
            review = await llm_analyst.exit_review(pos, self._sync_log, market=market)
            if not review:
                continue

            cur = pos.get("current_price_usd") or pos["buy_price_usd"]

            # 1) VENDER YA si la IA esta segura
            if review.get("action") == "sell" and review.get("confidence", 0) >= config.AI_EXIT_MIN_CONFIDENCE:
                await self.broadcast_log(
                    "TRADE",
                    f"🧠 La IA recomienda SALIR de {pos['token_symbol']} ({review['confidence']}%): {review['reason']}"
                )
                await self._close_position(pos, cur, "ai_exit")
                continue

            # 2) MANTENER + AJUSTAR la regla (objetivo/stop dinamicos)
            if config.ENABLE_AI_DYNAMIC_LEVELS:
                await self._apply_ai_levels(pos, review, cur)
            else:
                await self._log_ai_hold_summary(pos, review, cur)

    async def _apply_ai_levels(self, pos: dict, review: dict, cur: float):
        """Aplica el objetivo/stop que decide la IA. Las reglas rapidas los ejecutaran al instante."""
        buy = pos.get("buy_price_usd") or 0
        if buy <= 0 or cur <= 0:
            return
        tgt_pct = review.get("target_pct")
        stp_pct = review.get("stop_pct")
        new_target = pos.get("target_price_usd") or 0
        new_stop = pos.get("stop_price_usd") or 0

        if tgt_pct is not None:
            new_target = buy * (1 + tgt_pct / 100.0)
        if stp_pct is not None:
            new_stop = buy * (1 + stp_pct / 100.0)

        # Seguridad: el objetivo por encima del precio actual y el stop por debajo
        # (si la IA quisiera salir ya, deberia usar action=sell, no el plan).
        new_target = max(new_target, cur * 1.001)
        new_stop = min(new_stop, cur * 0.999)
        if new_stop <= 0:
            new_stop = pos.get("stop_price_usd") or (buy * 0.85)

        old_target = pos.get("target_price_usd") or 0
        old_stop = pos.get("stop_price_usd") or 0
        # Solo actualiza/loguea si el cambio es significativo (>1%)
        changed = (
            abs(new_target - old_target) > old_target * 0.01 or
            abs(new_stop - old_stop) > old_stop * 0.01
        )
        if not changed:
            await self._log_ai_hold_summary(pos, review, cur)
            return
        db.update_position_levels(pos["id"], new_target, new_stop)
        tgt_p = (new_target - buy) / buy * 100
        stop_p = (new_stop - buy) / buy * 100
        await self.broadcast_log(
            "TRADE",
            f"🧠 IA ajusta plan de {pos['token_symbol']}: objetivo {tgt_p:+.0f}% / stop {stop_p:+.0f}% | {review.get('reason','')}"
        )
        await self.broadcast("balance_update", await self.get_status())

    async def _log_ai_hold_summary(self, pos: dict, review: dict, cur: float):
        """Muestra que la IA sigue vigilando aunque mantenga el mismo plan."""
        if config.AI_EXIT_SUMMARY_EVERY <= 0:
            return
        pid = int(pos["id"])
        count = self._ai_exit_reviews.get(pid, 0) + 1
        self._ai_exit_reviews[pid] = count
        if count % config.AI_EXIT_SUMMARY_EVERY != 0:
            return
        buy = pos.get("buy_price_usd") or 0
        pnl = ((cur - buy) / buy * 100) if buy > 0 else 0
        tgt = pos.get("target_price_usd") or 0
        stp = pos.get("stop_price_usd") or 0
        tgt_p = ((tgt - buy) / buy * 100) if buy > 0 and tgt > 0 else 0
        stp_p = ((stp - buy) / buy * 100) if buy > 0 and stp > 0 else 0
        action = "mantiene" if review.get("action") != "sell" else "vigila salida"
        await self.broadcast_log(
            "INFO",
            f"🧠 IA {action} {pos['token_symbol']}: P&L {pnl:+.1f}% | "
            f"plan objetivo {tgt_p:+.0f}% / stop {stp_p:+.0f}% | {review.get('reason','')}"
        )

    # ── Start / Stop ──────────────────────────────────────────────────────────

    async def start(self):
        if self.running:
            return
        self.running = True
        self.stop_event.clear()

        await self.broadcast_log("INFO", "Bot iniciado - modo " + ("LIVE" if config.ENABLE_TRADING else "PAPER"))

        if llm_analyst.is_enabled():
            ws_note = " + busqueda web" if config.ENABLE_LLM_WEBSEARCH else ""
            await self.broadcast_log("INFO", f"Filtro LLM ACTIVO: {llm_analyst.provider_label()}{ws_note}")
            await self.broadcast_log("INFO", f"Roles IA: {llm_analyst.role_label()} (con respaldo automatico)")
        else:
            await self.broadcast_log("INFO", "Filtro LLM desactivado - solo analisis algoritmico (gratis)")

        # Resumen de la estrategia de salida y protecciones
        exit_parts = []
        if config.ENABLE_PARTIAL_TP:
            exit_parts.append(f"TP parcial {config.PARTIAL_TP_SELL_FRACTION*100:.0f}% a +{config.PARTIAL_TP_TRIGGER_PCT*100:.0f}%")
        if config.ENABLE_TRAILING_STOP:
            exit_parts.append(f"trailing stop {config.TRAILING_STOP_PCT*100:.0f}%")
        else:
            exit_parts.append(f"TP fijo +{config.TAKE_PROFIT_PCT*100:.0f}%")
        exit_parts.append(f"stop-loss {config.STOP_LOSS_PCT*100:.0f}%")
        await self.broadcast_log("INFO", "Estrategia salida: " + " | ".join(exit_parts))
        protecciones = []
        if config.ENABLE_HONEYPOT_CHECK:
            protecciones.append("anti-honeypot")
        if config.ENABLE_DEV_ANALYSIS:
            protecciones.append("analisis dev/bundle")
        protecciones.append(f"anti-rug (top10 max {config.MAX_TOP10_PCT:.0f}%)")
        bl_count = len(db.get_blacklist())
        protecciones.append(f"lista negra ({bl_count} vetadas)")
        fav_count = len(db.get_favorites())
        protecciones.append(f"favoritas ({fav_count} vigiladas)")
        if protecciones:
            await self.broadcast_log("INFO", "Protecciones activas: " + ", ".join(protecciones))

        if config.ENABLE_SOL_SWING:
            swing_st = db.get_state("sol_swing_state", "risk_on")
            estado = "en SOL (risk-on)" if swing_st == "risk_on" else "protegido en USDC"
            await self.broadcast_log(
                "INFO",
                f"Swing de SOL ACTIVO ({config.SOL_SWING_PCT*100:.0f}% del balance) - estado: {estado}"
            )

        if config.ENABLE_AI_EXIT and llm_analyst.is_enabled():
            modo_ia = "ajusta objetivo/stop y puede vender" if config.ENABLE_AI_DYNAMIC_LEVELS else "puede vender antes"
            await self.broadcast_log(
                "INFO",
                f"Salida con IA ACTIVA - re-evalua cada posicion cada {config.AI_EXIT_INTERVAL}s ({modo_ia}); "
                f"el gatillo rapido la ejecuta cada {config.PRICE_CHECK_INTERVAL}s"
            )

        # Wallet: obligatoria solo en modo REAL; en simulacion es opcional.
        pk = (config.PRIVATE_KEY or "").strip()
        if pk and pk != "TU_CLAVE_PRIVADA_AQUI":
            try:
                wallet.load_keypair()
                pub = wallet.get_public_key_str()
                bal = await wallet.get_sol_balance()
                await self.broadcast_log("INFO", f"Wallet: {pub[:8]}...{pub[-4:]} | Balance: {bal:.4f} SOL")
            except Exception as e:
                if config.ENABLE_TRADING:
                    await self.broadcast_log("ERROR", f"Clave de wallet invalida ({e}) - no se puede operar en REAL")
                    self.running = False
                    return
                await self.broadcast_log("WARNING", "Clave de wallet invalida - sigo en SIMULACION")
        else:
            await self.broadcast_log("INFO", "Modo simulacion (sin wallet real)")

        loop = asyncio.get_event_loop()
        self.tasks = [
            loop.create_task(scanner_loop(self.on_token_found, self.on_buy_signal, self.stop_event)),
            loop.create_task(self.monitor_positions()),
            loop.create_task(news_loop()),
            loop.create_task(self.sol_swing_loop()),
            loop.create_task(self.ai_exit_loop()),
        ]

    async def stop(self):
        if not self.running:
            return
        self.running = False
        self.stop_event.set()
        for task in self.tasks:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self.tasks.clear()
        await self.broadcast_log("INFO", "Bot detenido")
        await self.broadcast("balance_update", await self.get_status())


bot = BotEngine()


# ── FastAPI app ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    _apply_risk_mode(config.RISK_MODE, persist=False)  # coherencia de umbrales al arrancar
    db.add_log("INFO", "Base de datos inicializada")
    yield
    await bot.stop()


app = FastAPI(title="Solana Trading Bot", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    html_path = Path(__file__).parent / "frontend" / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.get("/api/status")
async def get_status():
    return await bot.get_status()


@app.get("/api/trades")
async def get_trades(limit: int = 50):
    return db.get_trades(limit)


def _compute_exit_plan(p: dict) -> dict:
    """Calcula los niveles de salida de una posicion (para mostrar el plan en el dashboard)."""
    buy = p.get("buy_price_usd") or 0
    cur = p.get("current_price_usd") or buy
    high = max(p.get("highest_price_usd") or 0, cur, buy)
    ai_managed = (config.ENABLE_AI_EXIT and config.ENABLE_AI_DYNAMIC_LEVELS and llm_analyst.is_enabled())
    plan = {"partial_taken": bool(p.get("partial_taken")), "ai_managed": ai_managed}
    if config.ENABLE_PARTIAL_TP and not p.get("partial_taken"):
        plan["partial_tp_price"] = round(buy * (1 + config.PARTIAL_TP_TRIGGER_PCT), 10)
        plan["partial_tp_pct"] = round(config.PARTIAL_TP_TRIGGER_PCT * 100)
    # Objetivo (take-profit): activo si lo gestiona la IA o si no hay trailing
    if ai_managed or not config.ENABLE_TRAILING_STOP:
        plan["take_profit_price"] = p.get("target_price_usd")
        tp = p.get("target_price_usd") or 0
        plan["take_profit_pct"] = round(((tp - buy) / buy * 100), 1) if buy > 0 else 0
    # Stop (con trailing como suelo si esta activo)
    if config.ENABLE_TRAILING_STOP:
        plan["mode"] = "ia" if ai_managed else "trailing"
        plan["trailing_pct"] = round(config.TRAILING_STOP_PCT * 100)
        plan["peak_pct"] = round(((high - buy) / buy * 100), 1) if buy > 0 else 0
        if high > buy:  # el trailing solo se activa si entro en beneficio (igual que el monitor)
            trail = high * (1 - config.TRAILING_STOP_PCT)
            plan["sell_price"] = round(max(p.get("stop_price_usd") or 0, trail), 10)
            plan["trailing_active"] = True
        else:
            plan["sell_price"] = p.get("stop_price_usd")
            plan["trailing_active"] = False
    else:
        plan["mode"] = "ia" if ai_managed else "fixed"
        plan["sell_price"] = p.get("stop_price_usd")
    # % al que esta el nivel de venta respecto a la compra
    sp = plan.get("sell_price") or 0
    plan["sell_at_pct"] = round(((sp - buy) / buy * 100), 1) if buy > 0 else 0
    plan["stop_loss_pct"] = round(-config.STOP_LOSS_PCT * 100)
    return plan


@app.get("/api/positions")
async def get_positions():
    positions = db.get_open_positions()
    for p in positions:
        p["exit_plan"] = _compute_exit_plan(p)
    return positions


@app.get("/api/logs")
async def get_logs(limit: int = 100, since_id: int = 0):
    if since_id > 0:
        return db.get_logs_since(since_id, limit)
    return db.get_recent_logs(limit)


@app.get("/api/blacklist")
async def get_blacklist():
    """Monedas vetadas por rug pull / perdida grande (no se vuelven a comprar)."""
    return db.get_blacklist()


@app.get("/api/favorites")
async def get_favorites():
    """Monedas ganadoras guardadas: se re-analizan para volver a entrar en su bajada."""
    return db.get_favorites()


@app.get("/api/learning")
async def get_learning():
    return get_learning_summary()


@app.get("/api/news")
async def get_news():
    # Carga perezosa: si no hay noticias aun, refresca al instante
    if not get_recent_news():
        try:
            await refresh_news()
        except Exception:
            pass
    return {
        "news": get_recent_news(),
        "sol_sentiment": get_sol_sentiment(),
    }


@app.get("/api/sol_expert")
async def get_sol_expert_endpoint():
    import sol_expert
    return await sol_expert.get_sol_expert(bot._sync_log)


@app.get("/api/sol_market")
async def get_sol_market_endpoint():
    import sol_market
    data = await sol_market.get_sol_market(with_llm=False)  # el analisis lo da /api/sol_expert
    data["swing_enabled"] = config.ENABLE_SOL_SWING
    data["swing_state"] = db.get_state("sol_swing_state", "risk_on")
    data["swing_usdc"] = float(db.get_state("sol_swing_usdc", "0") or 0)
    data["swing_protected_sol"] = float(db.get_state("sol_swing_sol_parked", "0") or 0) if data["swing_state"] == "risk_off" else 0.0
    data["swing_pct"] = config.SOL_SWING_PCT
    swing = db.get_swing_stats()
    data["swing_trades"] = swing["count"]
    data["swing_pnl_sol"] = swing["pnl_sol"]
    data["swing_pnl_eur"] = round(swing["pnl_sol"] * data.get("price_eur", 0), 2)
    return data


RISK_PRESETS = {
    "conservador": {"MIN_SCORE": 65, "MIN_SCORE_TRENDING": 60, "LLM_MIN_CONFIDENCE": 60},
    "balanceado":  {"MIN_SCORE": 58, "MIN_SCORE_TRENDING": 50, "LLM_MIN_CONFIDENCE": 48},
    "agresivo":    {"MIN_SCORE": 50, "MIN_SCORE_TRENDING": 42, "LLM_MIN_CONFIDENCE": 40},
}


def _apply_risk_mode(mode: str, persist: bool = True):
    p = RISK_PRESETS.get(mode)
    if not p:
        return
    config.RISK_MODE = mode
    config.MIN_SCORE = p["MIN_SCORE"]
    config.MIN_SCORE_TRENDING = p["MIN_SCORE_TRENDING"]
    config.LLM_MIN_CONFIDENCE = p["LLM_MIN_CONFIDENCE"]
    if persist:
        _persist_env("RISK_MODE", mode)
        _persist_env("MIN_SCORE", str(p["MIN_SCORE"]))
        _persist_env("MIN_SCORE_TRENDING", str(p["MIN_SCORE_TRENDING"]))
        _persist_env("LLM_MIN_CONFIDENCE", str(p["LLM_MIN_CONFIDENCE"]))


def _persist_env(key: str, value: str):
    """Actualiza (o anade) una variable en el archivo .env para que sobreviva reinicios."""
    env_path = Path(__file__).parent / ".env"
    lines = []
    found = False
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(key + "="):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@app.post("/api/wallet")
async def set_wallet(data: dict):
    """Configura la clave privada de la wallet desde la UI (se guarda en .env local)."""
    pk = (data or {}).get("private_key", "").strip()
    if not pk:
        raise HTTPException(status_code=400, detail="Clave vacia")
    try:
        pubkey = wallet.set_private_key(pk)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Clave invalida: {e}")
    try:
        _persist_env("PRIVATE_KEY", pk)
    except Exception:
        pass  # si no puede escribir el .env, al menos queda en memoria esta sesion
    await bot.broadcast_log("INFO", f"Wallet configurada: {pubkey[:8]}...{pubkey[-4:]}")
    return {"pubkey": pubkey}


@app.post("/api/mode")
async def set_mode(data: dict):
    """Cambia entre 'paper' (simulacion) y 'live' (dinero real)."""
    mode = (data or {}).get("mode", "paper")
    if mode == "live":
        if not config.PRIVATE_KEY:
            raise HTTPException(status_code=400, detail="Configura PRIVATE_KEY en el .env antes de activar el modo real")
        try:
            wallet.load_keypair()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"No se pudo cargar la wallet: {e}")
        config.ENABLE_TRADING = True
        await bot.broadcast_log("WARNING", "⚠️ MODO REAL activado - el bot operara con DINERO DE VERDAD")
    else:
        config.ENABLE_TRADING = False
        await bot.broadcast_log("INFO", "Modo SIMULACIÓN (paper) activado - sin dinero real")
    await bot.broadcast("balance_update", await bot.get_status())
    return {"mode": "live" if config.ENABLE_TRADING else "paper"}


@app.get("/api/tokens")
async def get_tokens(limit: int = 30):
    return db.get_recent_tokens(limit)


@app.get("/api/settings")
async def get_settings():
    return {
        "llm_model": config.LLM_MODEL,
        "llm_effort": config.LLM_EFFORT,
        "risk_mode": config.RISK_MODE,
        "llm_enabled": config.ENABLE_LLM_REVIEW,
        "websearch": config.ENABLE_LLM_WEBSEARCH,
        "use_claude": config.LLM_USE_CLAUDE,
        "use_gemini": config.LLM_USE_GEMINI,
        "use_groq": config.LLM_USE_GROQ,
        "use_cerebras": config.LLM_USE_CEREBRAS,
        "use_openrouter": config.LLM_USE_OPENROUTER,
        "has_claude_key": bool(config.ANTHROPIC_API_KEY),
        "has_gemini_key": bool(config.GEMINI_API_KEY),
        "has_groq_key": bool(config.GROQ_API_KEY),
        "has_cerebras_key": bool(config.CEREBRAS_API_KEY),
        "has_openrouter_key": bool(config.OPENROUTER_API_KEY),
        "provider_label": llm_analyst.provider_label(),
        "role_label": llm_analyst.role_label(),
        "entry_provider": config.LLM_ENTRY_PROVIDER,
        "exit_provider": config.LLM_EXIT_PROVIDER,
        "sol_provider": config.LLM_SOL_PROVIDER,
    }


@app.post("/api/settings")
async def update_settings(data: dict):
    data = data or {}
    if data.get("llm_model") in ("claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-8"):
        config.LLM_MODEL = data["llm_model"]
        _persist_env("LLM_MODEL", config.LLM_MODEL)
    if data.get("llm_effort") in ("low", "medium", "high", "max"):
        config.LLM_EFFORT = data["llm_effort"]
        _persist_env("LLM_EFFORT", config.LLM_EFFORT)
    if data.get("risk_mode") in RISK_PRESETS:
        _apply_risk_mode(data["risk_mode"])
    if "websearch" in data:
        config.ENABLE_LLM_WEBSEARCH = bool(data["websearch"])
        _persist_env("ENABLE_LLM_WEBSEARCH", "true" if config.ENABLE_LLM_WEBSEARCH else "false")
    if "use_claude" in data:
        config.LLM_USE_CLAUDE = bool(data["use_claude"])
        _persist_env("LLM_USE_CLAUDE", "true" if config.LLM_USE_CLAUDE else "false")
    if "use_gemini" in data:
        config.LLM_USE_GEMINI = bool(data["use_gemini"])
        _persist_env("LLM_USE_GEMINI", "true" if config.LLM_USE_GEMINI else "false")
    if "use_groq" in data:
        config.LLM_USE_GROQ = bool(data["use_groq"])
        _persist_env("LLM_USE_GROQ", "true" if config.LLM_USE_GROQ else "false")
    if "use_cerebras" in data:
        config.LLM_USE_CEREBRAS = bool(data["use_cerebras"])
        _persist_env("LLM_USE_CEREBRAS", "true" if config.LLM_USE_CEREBRAS else "false")
    if "use_openrouter" in data:
        config.LLM_USE_OPENROUTER = bool(data["use_openrouter"])
        _persist_env("LLM_USE_OPENROUTER", "true" if config.LLM_USE_OPENROUTER else "false")
    if "llm_enabled" in data:
        config.ENABLE_LLM_REVIEW = bool(data["llm_enabled"])
        _persist_env("ENABLE_LLM_REVIEW", "true" if config.ENABLE_LLM_REVIEW else "false")
    valid_providers = {"gemini", "groq", "cerebras", "openrouter", "claude", "auto"}
    if data.get("entry_provider") in valid_providers:
        config.LLM_ENTRY_PROVIDER = data["entry_provider"]
        _persist_env("LLM_ENTRY_PROVIDER", config.LLM_ENTRY_PROVIDER)
    if data.get("exit_provider") in valid_providers:
        config.LLM_EXIT_PROVIDER = data["exit_provider"]
        _persist_env("LLM_EXIT_PROVIDER", config.LLM_EXIT_PROVIDER)
    if data.get("sol_provider") in valid_providers:
        config.LLM_SOL_PROVIDER = data["sol_provider"]
        _persist_env("LLM_SOL_PROVIDER", config.LLM_SOL_PROVIDER)
    await bot.broadcast_log("INFO", f"Ajustes guardados: modelo={config.LLM_MODEL}, esfuerzo={config.LLM_EFFORT}, riesgo={config.RISK_MODE}")
    await bot.broadcast("balance_update", await bot.get_status())
    return await get_settings()


@app.post("/api/start")
async def start_bot():
    await bot.start()
    return {"status": "started"}


@app.post("/api/stop")
async def stop_bot():
    await bot.stop()
    return {"status": "stopped"}


@app.post("/api/positions/{position_id}/close")
async def force_close_position(position_id: int):
    positions = db.get_open_positions()
    pos = next((p for p in positions if p["id"] == position_id), None)
    if not pos:
        raise HTTPException(status_code=404, detail="Posicion no encontrada")

    prices = await bot._batch_prices([pos["token_address"]])
    current_price = prices.get(pos["token_address"], pos["buy_price_usd"])
    await bot._close_position(pos, current_price, "manual")
    return {"status": "closed"}


@app.post("/api/config")
async def update_config(data: dict):
    allowed = {
        "TAKE_PROFIT_PCT", "STOP_LOSS_PCT", "MAX_POSITIONS",
        "MIN_SCORE", "MIN_LIQUIDITY_USD", "MAX_TRADE_PCT", "ENABLE_TRADING"
    }
    updated = {}
    for k, v in data.items():
        if k in allowed:
            setattr(config, k, type(getattr(config, k))(v))
            updated[k] = v
    return {"updated": updated}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    bot.ws_clients.add(websocket)
    try:
        # Send initial state
        await websocket.send_text(json.dumps({
            "type": "init",
            "data": {
                "status": await bot.get_status(),
                "logs": db.get_recent_logs(50),
                "positions": db.get_open_positions(),
                "trades": db.get_trades(20),
                "tokens": db.get_recent_tokens(30),
            }
        }))
        # Keep connection alive
        while True:
            await asyncio.sleep(30)
            await websocket.send_text(json.dumps({"type": "ping", "data": {}}))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        bot.ws_clients.discard(websocket)
