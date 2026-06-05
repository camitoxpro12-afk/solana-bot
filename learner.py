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
    trades = [
        t for t in raw_trades
        if config.LEARNING_MIN_ABS_PNL_PCT
        <= abs(float(t.get("pnl_pct", 0) or 0))
        <= config.LEARNING_MAX_ABS_PNL_PCT
    ]
    if len(trades) < config.LEARNING_MIN_TRADES:
        return get_weights()

    # Parse scores and outcomes
    outcomes = []
    factor_scores = {}

    for trade in trades:
        pnl = trade.get("pnl_pct", 0) or 0
        outcomes.append(max(-20.0, min(20.0, pnl)))

        scores_raw = trade.get("scores")
        if not scores_raw:
            continue
        try:
            scores = json.loads(scores_raw) if isinstance(scores_raw, str) else scores_raw
            for factor, score in scores.items():
                if factor == "total":
                    continue
                if factor not in factor_scores:
                    factor_scores[factor] = []
                factor_scores[factor].append(float(score) if score else 0.0)
        except Exception:
            pass

    if not factor_scores or len(outcomes) < config.LEARNING_MIN_TRADES:
        return get_weights()

    outcomes_arr = np.array(outcomes, dtype=float)
    current_weights = get_weights()
    new_weights = dict(current_weights)

    adjustments = {}
    for factor, scores_list in factor_scores.items():
        if len(scores_list) < config.LEARNING_MIN_TRADES:
            continue

        # Pad/truncate to same length
        n = min(len(scores_list), len(outcomes_arr))
        s = np.array(scores_list[:n], dtype=float)
        o = outcomes_arr[:n]

        if s.std() < 0.01:
            continue  # No variance = no signal

        # Pearson correlation between factor scores and trade outcomes
        corr = np.corrcoef(s, o)[0, 1]
        if np.isnan(corr):
            continue

        # Positive correlation -> factor predicts profit -> increase weight
        # Negative correlation -> factor predicts loss -> decrease weight
        adjustment = 1.0 + (corr * config.LEARNING_RATE)
        adjustment = max(1.0 - config.LEARNING_RATE, min(1.0 + config.LEARNING_RATE, adjustment))

        old = current_weights.get(factor, 1.0)
        new = old * adjustment
        new = max(MIN_WEIGHT, min(MAX_WEIGHT, new))
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
