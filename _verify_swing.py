import sqlite3, sys
con = sqlite3.connect(sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\camit\Desktop\programa\solana-bot\bot.db")
con.row_factory = sqlite3.Row
cur = con.cursor()
for k in ("sol_swing_state","sol_swing_usdc","sol_swing_sol_parked","sol_swing_entry_price","paper_start_sol"):
    r = cur.execute("SELECT value FROM bot_state WHERE key=?", (k,)).fetchone()
    print(k, "=", r["value"] if r else None)
print("---- schema ----")
for r in cur.execute("SELECT name, sql FROM sqlite_master WHERE type='table'"):
    print(r["name"], "|", (r["sql"] or "").replace("\n", " ")[:300])
print("---- swing trades ----")
for r in cur.execute("SELECT * FROM trades WHERE outcome='sol_swing' ORDER BY id"):
    print(dict(r))
print("---- totals ----")
r = cur.execute("SELECT COUNT(*), COALESCE(SUM(pnl_sol),0) FROM trades").fetchone()
print("all trades:", r[0], "net pnl_sol:", r[1])
r = cur.execute("SELECT COUNT(*), COALESCE(SUM(pnl_sol),0) FROM trades WHERE outcome='sol_swing'").fetchone()
print("swing trades:", r[0], "swing pnl_sol:", r[1])
r = cur.execute("SELECT COUNT(*), COALESCE(SUM(pnl_sol),0) FROM trades WHERE outcome<>'sol_swing'").fetchone()
print("non-swing trades:", r[0], "non-swing pnl_sol:", r[1])
print("---- worst individual trades ----")
for r in cur.execute("SELECT id, token_symbol, pnl_sol, pnl_pct, outcome FROM trades ORDER BY pnl_sol ASC LIMIT 5"):
    print(dict(r))
print("---- swing-related logs (last 40) ----")
try:
    for r in cur.execute("SELECT * FROM logs WHERE message LIKE '%SWING%' COLLATE NOCASE ORDER BY id DESC LIMIT 40"):
        print(dict(r))
except Exception as e:
    print("logs err:", e)
