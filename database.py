import sqlite3
import json
from datetime import datetime, date
from pathlib import Path
from typing import Optional, List, Dict, Any

DB_PATH = Path("bot.db")

DEFAULT_WEIGHTS = {
    "rugcheck": 1.0,
    "mint_authority": 1.0,
    "freeze_authority": 1.0,
    "liquidity": 1.0,
    "holder_distribution": 1.0,
    "volume_mc_ratio": 1.0,
    "price_trend": 1.0,
    "volume_trend": 1.0,
    "news_sentiment": 1.0,
    "social_score": 1.0,
}


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_address TEXT NOT NULL,
                token_name TEXT,
                token_symbol TEXT,
                buy_time TEXT DEFAULT (datetime('now')),
                buy_price_usd REAL,
                buy_price_sol REAL,
                amount_sol REAL,
                amount_tokens REAL,
                target_price_usd REAL,
                stop_price_usd REAL,
                current_price_usd REAL DEFAULT 0,
                highest_price_usd REAL DEFAULT 0,
                partial_taken INTEGER DEFAULT 0,
                image_url TEXT DEFAULT '',
                scores TEXT,
                status TEXT DEFAULT 'open'
            );

            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_address TEXT NOT NULL,
                token_name TEXT,
                token_symbol TEXT,
                buy_time TEXT,
                sell_time TEXT DEFAULT (datetime('now')),
                buy_price_usd REAL,
                sell_price_usd REAL,
                amount_sol REAL,
                pnl_sol REAL,
                pnl_pct REAL,
                scores TEXT,
                outcome TEXT
            );

            CREATE TABLE IF NOT EXISTS tokens_seen (
                address TEXT PRIMARY KEY,
                name TEXT,
                symbol TEXT,
                first_seen TEXT DEFAULT (datetime('now')),
                last_analyzed TEXT,
                score REAL,
                verdict TEXT,
                analysis TEXT
            );

            CREATE TABLE IF NOT EXISTS learning_weights (
                factor TEXT PRIMARY KEY,
                weight REAL DEFAULT 1.0,
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS bot_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT (datetime('now')),
                level TEXT,
                message TEXT
            );

            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        # Seed default learning weights if not present
        for factor, weight in DEFAULT_WEIGHTS.items():
            conn.execute(
                "INSERT OR IGNORE INTO learning_weights (factor, weight) VALUES (?, ?)",
                (factor, weight)
            )
        # Migracion: anade columnas nuevas a BDs existentes (ignora si ya existen)
        for col, ddl in [
            ("highest_price_usd", "REAL DEFAULT 0"),
            ("partial_taken", "INTEGER DEFAULT 0"),
            ("image_url", "TEXT DEFAULT ''"),
        ]:
            try:
                conn.execute(f"ALTER TABLE positions ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass
        conn.commit()


# ── Logs ──────────────────────────────────────────────────────────────────────

def add_log(level: str, message: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO bot_logs (level, message) VALUES (?, ?)",
            (level, message)
        )
        conn.commit()


def get_recent_logs(limit: int = 100) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM bot_logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


# ── Positions ─────────────────────────────────────────────────────────────────

def open_position(
    token_address: str, token_name: str, token_symbol: str,
    buy_price_usd: float, buy_price_sol: float,
    amount_sol: float, amount_tokens: float,
    target_price_usd: float, stop_price_usd: float,
    scores: dict, image_url: str = ""
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO positions
               (token_address, token_name, token_symbol, buy_price_usd, buy_price_sol,
                amount_sol, amount_tokens, target_price_usd, stop_price_usd,
                current_price_usd, highest_price_usd, partial_taken, image_url, scores)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
            (token_address, token_name, token_symbol, buy_price_usd, buy_price_sol,
             amount_sol, amount_tokens, target_price_usd, stop_price_usd,
             buy_price_usd, buy_price_usd, image_url, json.dumps(scores))
        )
        conn.commit()
        return cur.lastrowid


def update_position_high(position_id: int, price_usd: float):
    """Actualiza el maximo historico del precio (para el trailing stop)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE positions SET highest_price_usd = max(COALESCE(highest_price_usd, 0), ?) WHERE id = ?",
            (price_usd, position_id)
        )
        conn.commit()


def partial_sell(position_id: int, fraction: float, sell_price_usd: float,
                 new_stop_price_usd: float) -> Optional[Dict]:
    """
    Registra una venta PARCIAL: crea una fila en trades por la fraccion vendida,
    reduce la posicion y mueve el stop. La posicion sigue abierta con el resto.
    """
    with get_conn() as conn:
        pos = conn.execute("SELECT * FROM positions WHERE id = ?", (position_id,)).fetchone()
        if not pos:
            return None
        pos = dict(pos)
        if pos["buy_price_usd"] <= 0:
            return None

        sold_sol = pos["amount_sol"] * fraction
        pnl_pct = (sell_price_usd - pos["buy_price_usd"]) / pos["buy_price_usd"] * 100
        pnl_sol = sold_sol * (pnl_pct / 100)

        conn.execute(
            """INSERT INTO trades
               (token_address, token_name, token_symbol, buy_time, buy_price_usd,
                sell_price_usd, amount_sol, pnl_sol, pnl_pct, scores, outcome)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pos["token_address"], pos["token_name"], pos["token_symbol"],
             pos["buy_time"], pos["buy_price_usd"], sell_price_usd,
             sold_sol, pnl_sol, pnl_pct, pos["scores"], "partial_tp")
        )
        conn.execute(
            """UPDATE positions
               SET amount_tokens = amount_tokens * (1 - ?),
                   amount_sol = amount_sol * (1 - ?),
                   partial_taken = 1,
                   stop_price_usd = ?
               WHERE id = ?""",
            (fraction, fraction, new_stop_price_usd, position_id)
        )
        conn.commit()
        return {"pnl_sol": pnl_sol, "pnl_pct": pnl_pct}


def get_open_positions() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM positions WHERE status = 'open' ORDER BY buy_time DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def update_position_price(position_id: int, current_price_usd: float):
    with get_conn() as conn:
        conn.execute(
            "UPDATE positions SET current_price_usd = ? WHERE id = ?",
            (current_price_usd, position_id)
        )
        conn.commit()


def close_position(position_id: int, sell_price_usd: float, outcome: str) -> Optional[Dict]:
    with get_conn() as conn:
        pos = conn.execute(
            "SELECT * FROM positions WHERE id = ?", (position_id,)
        ).fetchone()
        if not pos:
            return None

        pos = dict(pos)
        pnl_pct = (sell_price_usd - pos["buy_price_usd"]) / pos["buy_price_usd"] * 100
        pnl_sol = pos["amount_sol"] * (pnl_pct / 100)

        conn.execute(
            """INSERT INTO trades
               (token_address, token_name, token_symbol, buy_time, buy_price_usd,
                sell_price_usd, amount_sol, pnl_sol, pnl_pct, scores, outcome)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pos["token_address"], pos["token_name"], pos["token_symbol"],
             pos["buy_time"], pos["buy_price_usd"], sell_price_usd,
             pos["amount_sol"], pnl_sol, pnl_pct, pos["scores"], outcome)
        )
        conn.execute(
            "UPDATE positions SET status = 'closed' WHERE id = ?", (position_id,)
        )
        conn.commit()
        return {"pnl_sol": pnl_sol, "pnl_pct": pnl_pct, "outcome": outcome}


def record_swing_trade(sol_in: float, sol_out: float, sol_price_usd: float) -> Dict:
    """Registra un ciclo de swing de SOL (USDC->SOL) en el historial de trades."""
    pnl_sol = sol_out - sol_in
    pnl_pct = (pnl_sol / sol_in * 100) if sol_in > 0 else 0.0
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO trades
               (token_address, token_name, token_symbol, buy_time, buy_price_usd,
                sell_price_usd, amount_sol, pnl_sol, pnl_pct, scores, outcome)
               VALUES (?, ?, ?, datetime('now'), ?, ?, ?, ?, ?, ?, ?)""",
            ("SOL", "Solana", "SOL", sol_price_usd, sol_price_usd,
             sol_in, pnl_sol, pnl_pct, "{}", "sol_swing")
        )
        conn.commit()
    return {"pnl_sol": pnl_sol, "pnl_pct": pnl_pct}


# ── Trades ────────────────────────────────────────────────────────────────────

def get_trades(limit: int = 50) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY sell_time DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_daily_pnl() -> float:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl_sol), 0) FROM trades WHERE date(sell_time) = date('now')"
        ).fetchone()
    return row[0] if row else 0.0


def get_win_rate() -> float:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        wins = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE pnl_sol > 0"
        ).fetchone()[0]
    return (wins / total * 100) if total > 0 else 0.0


def get_total_pnl() -> float:
    with get_conn() as conn:
        row = conn.execute("SELECT COALESCE(SUM(pnl_sol), 0) FROM trades").fetchone()
    return row[0] if row else 0.0


# ── Tokens seen ───────────────────────────────────────────────────────────────

def mark_token_seen(address: str, name: str, symbol: str,
                    score: float, verdict: str, analysis: dict):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO tokens_seen
               (address, name, symbol, last_analyzed, score, verdict, analysis)
               VALUES (?, ?, ?, datetime('now'), ?, ?, ?)""",
            (address, name, symbol, score, verdict, json.dumps(analysis))
        )
        conn.commit()


def get_recent_tokens(limit: int = 30) -> List[Dict]:
    """Tokens analizados recientemente, en formato listo para el dashboard."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tokens_seen ORDER BY last_analyzed DESC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for r in rows:
        r = dict(r)
        try:
            a = json.loads(r.get("analysis") or "{}")
        except (json.JSONDecodeError, TypeError):
            a = {}
        out.append({
            "address": r["address"],
            "name": r["name"],
            "symbol": r["symbol"],
            "score": r["score"],
            "verdict": r["verdict"],
            "reason": a.get("reason", ""),
            "liquidity_usd": a.get("liquidity", 0),
            "image_url": a.get("image_url", ""),
            "category": a.get("category", "new"),
            "status": "analyzed",
        })
    return out


def was_token_seen(address: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM tokens_seen WHERE address = ?", (address,)
        ).fetchone()
    return row is not None


def get_tokens_analyzed_today() -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM tokens_seen WHERE date(last_analyzed) = date('now')"
        ).fetchone()
    return row[0] if row else 0


# ── Learning weights ──────────────────────────────────────────────────────────

def get_weights() -> Dict[str, float]:
    with get_conn() as conn:
        rows = conn.execute("SELECT factor, weight FROM learning_weights").fetchall()
    return {r["factor"]: r["weight"] for r in rows}


def save_weights(weights: Dict[str, float]):
    with get_conn() as conn:
        for factor, weight in weights.items():
            conn.execute(
                """INSERT OR REPLACE INTO learning_weights (factor, weight, updated_at)
                   VALUES (?, ?, datetime('now'))""",
                (factor, weight)
            )
        conn.commit()


def get_recent_trades_for_learning(n: int = 20) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT pnl_pct, scores FROM trades ORDER BY sell_time DESC LIMIT ?", (n,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Bot state ─────────────────────────────────────────────────────────────────

def set_state(key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)",
            (key, value)
        )
        conn.commit()


def get_state(key: str, default: str = "") -> str:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM bot_state WHERE key = ?", (key,)
        ).fetchone()
    return row[0] if row else default
