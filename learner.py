"""
Sistema de aprendizaje adaptativo.

Despues de cada 5 trades, analiza correlacion entre factores y resultados.
Ajusta pesos de forma que factores predictivos se amplifican y los
no-predictivos se reducen. Equivalente a un gradient descent simplificado.
"""

import json
import numpy as np
from typing import Dict
import config
from database import (
    get_weights, save_weights, get_recent_trades_for_learning,
    get_trades, add_log
)

MIN_WEIGHT = 0.3
MAX_WEIGHT = 3.0


def run_learning_cycle() -> Dict[str, float]:
    """
    Main learning function. Called after each trade closes.
    Returns updated weights.
    """
    raw_trades = get_recent_trades_for_learning(config.LEARNING_WINDOW_TRADES)

    # Matriz ALINEADA: cada muestra aporta su pnl Y sus scores JUNTOS (antes el pnl de
    # un trade se emparejaba con los scores de OTRO al saltarse filas sin scores).
    # Se excluyen swing y parciales (scores vacios / duplicados) y micro/outliers.
    samples = []  # lista de (pnl_clamped, scores_dict)
    for t in raw_trades:
        if (t.get("outcome") or "") in ("sol_swing", "partial_tp"):
            continue
        pnl = float(t.get("pnl_pct", 0) or 0)
        if not (config.LEARNING_MIN_ABS_PNL_PCT <= abs(pnl) <= config.LEARNING_MAX_ABS_PNL_PCT):
            continue
        scores_raw = t.get("scores")
        if not scores_raw:
            continue
        try:
            scores = json.loads(scores_raw) if isinstance(scores_raw, str) else scores_raw
        except Exception:
            continue
        if not isinstance(scores, dict) or not scores:
            continue
        samples.append((max(-20.0, min(20.0, pnl)), scores))

    # CONGELADO: con pocos datos limpios el "aprendizaje" es ruido que rompe el scoring.
    # Mejor dejar los pesos en 1.0 hasta tener una muestra de verdad.
    if len(samples) < config.LEARNING_MIN_CLEAN_TRADES:
        return get_weights()

    factors = set()
    for _, sc in samples:
        factors.update(k for k in sc.keys() if k != "total")

    current_weights = get_weights()
    new_weights = dict(current_weights)

    adjustments = {}
    for factor in factors:
        # Pares (score, pnl) de la MISMA muestra -> alineacion correcta.
        pairs = [(sc.get(factor), pnl) for pnl, sc in samples if sc.get(factor) is not None]
        if len(pairs) < config.LEARNING_MIN_SAMPLES_PER_FACTOR:
            continue
        s = np.array([float(p[0]) for p in pairs], dtype=float)
        o = np.array([float(p[1]) for p in pairs], dtype=float)
        if s.std() < 0.01:
            continue  # No variance = no signal

        corr = np.corrcoef(s, o)[0, 1]
        # Solo ajustar si la correlacion es SIGNIFICATIVA (no ruido espurio).
        if np.isnan(corr) or abs(corr) < config.LEARNING_MIN_CORR:
            continue

        adjustment = 1.0 + (corr * config.LEARNING_RATE)
        adjustment = max(1.0 - config.LEARNING_RATE, min(1.0 + config.LEARNING_RATE, adjustment))

        old = current_weights.get(factor, 1.0)
        new = max(MIN_WEIGHT, min(MAX_WEIGHT, old * adjustment))
        new_weights[factor] = round(new, 4)
        adjustments[factor] = round(corr, 3)

    # Normalize so mean weight stays near 1.0
    if new_weights:
        mean_w = sum(new_weights.values()) / len(new_weights)
        if mean_w > 0:
            new_weights = {k: round(v / mean_w, 4) for k, v in new_weights.items()}

    save_weights(new_weights)

    if adjustments:
        top_factors = sorted(adjustments.items(), key=lambda x: abs(x[1]), reverse=True)[:3]
        summary = ", ".join(f"{f}({v:+.2f})" for f, v in top_factors)
        add_log("INFO", f"Aprendizaje: ajustados {len(adjustments)} factores | Top correlaciones: {summary}")

    return new_weights


def get_learning_summary() -> Dict:
    """Return human-readable learning stats"""
    weights = get_weights()
    trades = get_trades(config.LEARNING_WINDOW_TRADES)
    signal_trades = [
        t for t in trades
        if config.LEARNING_MIN_ABS_PNL_PCT
        <= abs(float(t.get("pnl_pct", 0) or 0))
        <= config.LEARNING_MAX_ABS_PNL_PCT
    ]

    clean_trades = [
        t for t in trades
        if abs(float(t.get("pnl_pct", 0) or 0)) <= config.LEARNING_MAX_ABS_PNL_PCT
    ]
    profitable = [t for t in clean_trades if t.get("pnl_sol", 0) > 0]
    losing = [t for t in clean_trades if t.get("pnl_sol", 0) <= 0]

    # Find best and worst factors by weight
    sorted_w = sorted(weights.items(), key=lambda x: x[1], reverse=True)
    best_factors = sorted_w[:3]
    worst_factors = sorted_w[-3:]

    avg_win = sum(t.get("pnl_pct", 0) for t in profitable) / len(profitable) if profitable else 0
    avg_loss = sum(t.get("pnl_pct", 0) for t in losing) / len(losing) if losing else 0

    return {
        "total_trades": len(clean_trades),
        "signal_trades": len(signal_trades),
        "min_abs_pnl_pct": config.LEARNING_MIN_ABS_PNL_PCT,
        "max_abs_pnl_pct": config.LEARNING_MAX_ABS_PNL_PCT,
        "window_trades": config.LEARNING_WINDOW_TRADES,
        "learning_rate": config.LEARNING_RATE,
        "wins": len(profitable),
        "losses": len(losing),
        "avg_win_pct": round(avg_win, 1),
        "avg_loss_pct": round(avg_loss, 1),
        "best_factors": [{"factor": f, "weight": round(w, 3)} for f, w in best_factors],
        "worst_factors": [{"factor": f, "weight": round(w, 3)} for f, w in worst_factors],
        "weights": {k: round(v, 3) for k, v in weights.items()},
    }
