"""Prueba de humo: valida la logica del nucleo sin red ni wallet."""
import os
os.environ["PRIVATE_KEY"] = ""  # evita cargar wallet

import database as db
from models import TokenScores, TokenAnalysis
from analyzer import (
    _score_liquidity, _score_rugcheck, _score_mint_freeze,
    _score_holder_distribution, _score_volume_mc, _determine_verdict
)
import learner

print("=" * 50)
print("PRUEBA DE HUMO - Solana Bot")
print("=" * 50)

# 1. Base de datos
db.DB_PATH = __import__("pathlib").Path("test_smoke.db")
if db.DB_PATH.exists():
    db.DB_PATH.unlink()
db.init_db()
print("[OK] Base de datos inicializada")

# 2. Pesos por defecto
weights = db.get_weights()
assert len(weights) == 10, f"esperados 10 factores, hay {len(weights)}"
assert all(abs(w - 1.0) < 0.01 for w in weights.values())
print(f"[OK] {len(weights)} pesos de aprendizaje sembrados a 1.0")

# 3. Funciones de puntuacion
assert _score_liquidity(60000) == 15.0
assert _score_liquidity(1000) == 0.0
assert _score_liquidity(7000) == 6.0
print("[OK] Puntuacion de liquidez correcta")

# RugCheck: token seguro (score bajo de riesgo) -> puntuacion alta
safe_report = {"score_normalised": 5, "risks": []}
score, risks = _score_rugcheck(safe_report)
assert score > 18, f"token seguro deberia puntuar alto, dio {score}"
risky_report = {"score_normalised": 90, "risks": [{"name": "High risk"}]}
score2, _ = _score_rugcheck(risky_report)
assert score2 < 5, f"token peligroso deberia puntuar bajo, dio {score2}"
print(f"[OK] RugCheck: seguro={score}/20, peligroso={score2}/20")

# Mint/Freeze authority
report_mint_active = {"risks": [{"name": "Mint Authority still enabled"}]}
mint_s, freeze_s = _score_mint_freeze(report_mint_active, None)
assert mint_s == 0.0, f"mint activo debe dar 0, dio {mint_s}"
print(f"[OK] Mint authority activo detectado (score={mint_s})")

# Distribucion de holders
report_holders = {"topHolders": [{"pct": 5, "isLP": False} for _ in range(10)]}
dist, top10, _ = _score_holder_distribution(report_holders)
assert top10 == 50.0
assert dist == 0.0, f"top10=50% debe dar 0 pts, dio {dist}"
print(f"[OK] Distribucion holders: top10={top10}% -> {dist} pts")

# 4. Veredictos
verdict, reason = _determine_verdict(75, 10000, 60, 10, 10, [])
assert verdict == "buy", f"score 75 deberia ser buy, dio {verdict}"
verdict2, _ = _determine_verdict(75, 10000, 60, 0, 10, [])
assert verdict2 == "scam", "mint activo deberia ser scam"
verdict3, _ = _determine_verdict(75, 100, 60, 10, 10, [])
assert verdict3 == "skip", "liquidez baja deberia ser skip"
verdict4, _ = _determine_verdict(40, 10000, 60, 10, 10, [])
assert verdict4 == "skip", "score bajo deberia ser skip"
verdict5, _ = _determine_verdict(75, 10000, 60, 10, 10, ["honeypot detected"])
assert verdict5 == "scam", "honeypot deberia ser scam"
print("[OK] Veredictos: buy/scam/skip correctos en 5 casos")

# 5. TokenScores suma total
ts = TokenScores(rugcheck=20, mint_authority=10, freeze_authority=10,
                 liquidity=15, holder_distribution=10, volume_mc_ratio=10,
                 price_trend=8, volume_trend=7, news_sentiment=5, social_score=5)
total = ts.compute_total()
assert total == 100.0, f"suma maxima deberia ser 100, dio {total}"
print(f"[OK] TokenScores suma correctamente: {total}/100")

# 6. Ciclo de posicion completo
pid = db.open_position("ADDR123", "TestCoin", "TEST", 0.001, 0.0001,
                       0.05, 50000, 0.0025, 0.00065, ts.model_dump())
positions = db.get_open_positions()
assert len(positions) == 1
print(f"[OK] Posicion abierta (id={pid})")

result = db.close_position(pid, 0.0025, "take_profit")
assert result["pnl_pct"] > 100, f"venta a 2.5x deberia dar +150%, dio {result['pnl_pct']}"
assert len(db.get_open_positions()) == 0
print(f"[OK] Posicion cerrada con P&L: +{result['pnl_pct']:.0f}%")

# 7. Aprendizaje (necesita >= 5 trades)
import json
for i in range(8):
    pnl = 50 if i % 2 == 0 else -30
    scores = TokenScores(rugcheck=18 if pnl > 0 else 5, liquidity=15 if pnl > 0 else 3)
    scores.compute_total()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO trades (token_address, token_name, token_symbol, buy_price_usd, sell_price_usd, amount_sol, pnl_sol, pnl_pct, scores, outcome) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"A{i}", "T", "T", 0.001, 0.001, 0.05, pnl/1000, pnl, json.dumps(scores.model_dump()), "test")
        )
        conn.commit()

new_weights = learner.run_learning_cycle()
assert new_weights is not None
summary = learner.get_learning_summary()
assert summary["total_trades"] >= 8
print(f"[OK] Aprendizaje ejecutado: {summary['wins']} ganados, {summary['losses']} perdidos")
print(f"     RugCheck weight ajustado: {new_weights.get('rugcheck', 1.0):.3f}")
print(f"     Liquidez weight ajustado: {new_weights.get('liquidity', 1.0):.3f}")

print("=" * 50)
print("TODAS LAS PRUEBAS PASARON")
print("=" * 50)

# Limpieza (best-effort: en Windows el .db puede quedar bloqueado)
import gc
gc.collect()
try:
    db.DB_PATH.unlink()
except (PermissionError, FileNotFoundError):
    pass
