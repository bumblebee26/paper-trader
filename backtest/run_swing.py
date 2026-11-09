"""Run the swing backtest on the lakehouse's NSE daily prices and report in plain words.

  python backtest/run_swing.py            # needs AWS credentials (SSM: Databricks settings, Telegram)
  python backtest/run_swing.py --no-send  # print only

Fetches daily bars for the 250 most traded NSE stocks of the first 60 sessions (plus NIFTYBEES)
from stocks.silver.nse_eq_daily, runs four variants, saves backtests/swing/<date>.json in the state
bucket and sends a Telegram summary. Started from GitHub Actions (workflow "Swing backtest").
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "trader"), HERE]

import brief  # noqa: E402  (Databricks SQL helpers)
import market  # noqa: E402
import storage  # noqa: E402
import swing  # noqa: E402

UNIVERSE_SQL = """
WITH eq AS (SELECT symbol, trade_date, turnover FROM stocks.silver.nse_eq_daily WHERE series = 'EQ'),
first60 AS (SELECT trade_date FROM (SELECT DISTINCT trade_date FROM eq) ORDER BY trade_date LIMIT 60)
SELECT symbol, percentile_approx(turnover, 0.5) AS median_turnover, count(*) AS sessions
FROM eq JOIN first60 USING (trade_date)
GROUP BY symbol HAVING sessions >= 50 ORDER BY median_turnover DESC LIMIT 250
"""
BARS_SQL = """
SELECT symbol, CAST(trade_date AS STRING) AS d, open, high, low, close, prev_close, volume
FROM stocks.silver.nse_eq_daily
WHERE series = 'EQ' AND symbol IN ({symbols})
ORDER BY symbol, trade_date
"""
VARIANTS = {
    "pullback": {"setups": ("pullback",)},
    "breakout": {"setups": ("breakout",)},
    "both": {"setups": ("pullback", "breakout")},
    "both_rs": {"setups": ("pullback", "breakout"), "rs_filter": True},
}
NAMES = {"pullback": "Pullbacks only", "breakout": "Breakouts only", "both": "Both setups",
         "both_rs": "Both, only stocks beating Nifty"}


def sql_all(statement, max_wait=600):
    """Like brief.sql, but follows result chunks (large results come in several pieces)."""
    resp = brief._call("POST", "/api/2.0/sql/statements", {
        "warehouse_id": brief._db_config()[2], "statement": statement, "wait_timeout": "50s",
        "on_wait_timeout": "CONTINUE", "format": "JSON_ARRAY", "disposition": "INLINE"})
    deadline = time.monotonic() + max_wait
    while resp["status"]["state"] in ("PENDING", "RUNNING"):
        if time.monotonic() > deadline:
            raise TimeoutError("Databricks statement still running")
        time.sleep(5)
        resp = brief._call("GET", f"/api/2.0/sql/statements/{resp['statement_id']}")
    if resp["status"]["state"] != "SUCCEEDED":
        raise RuntimeError(f"Databricks statement {resp['status']['state']}: {resp['status'].get('error')}")
    cols = [c["name"] for c in resp["manifest"]["schema"]["columns"]]
    result = resp.get("result") or {}
    rows = list(result.get("data_array") or [])
    link = result.get("next_chunk_internal_link")
    while link:
        chunk = brief._call("GET", link)
        rows += chunk.get("data_array") or []
        link = chunk.get("next_chunk_internal_link")
    return [dict(zip(cols, r)) for r in rows]


def load_data():
    symbols = [r["symbol"] for r in sql_all(UNIVERSE_SQL)]
    symbols = sorted(set(symbols) | {"NIFTYBEES"})
    quoted = ",".join("'" + s.replace("'", "") + "'" for s in symbols)
    data = {}
    f = lambda v: float(v) if v not in (None, "") else None  # noqa: E731
    for r in sql_all(BARS_SQL.format(symbols=quoted)):
        data.setdefault(r["symbol"], []).append(
            (r["d"], f(r["open"]), f(r["high"]), f(r["low"]), f(r["close"]), f(r["prev_close"]), f(r["volume"])))
    return data


def inr(x, sign=False):
    import engine

    return engine.inr(x, sign=sign)


def message(results):
    s0 = results["both"]["summary"]
    lines = [f"[MOCK] Swing backtest: Rs 60,000 pool, {s0['start']} to {s0['end']}",
             f"Rules: daily-chart pullbacks and breakouts, bought at the next open, automatic exits "
             f"(stop, 2.5x target, trailing, 20-day limit), costs included.",
             f"NIFTYBEES (just holding the index) over the same days: {s0.get('benchmark_return_pct', 'n/a')}%, "
             f"deepest fall {s0.get('benchmark_max_drawdown_pct', 'n/a')}%.", ""]
    for key, name in NAMES.items():
        s = results[key]["summary"]
        lines.append(f"{name}: {s['return_pct']:+.1f}% ({inr(s['final_equity'] - 60_000, sign=True)}), "
                     f"deepest fall {s['max_drawdown_pct']}%, {s['trades']} trades, "
                     f"{s['win_rate_pct']}% won, avg {s['avg_r']}R, {s['losing_months']} of {s['months']} months lost money")
    best = max(VARIANTS, key=lambda k: results[k]["summary"]["return_pct"] - results[k]["summary"]["max_drawdown_pct"])
    b = results[best]["summary"]
    bench = s0.get("benchmark_return_pct")
    lines += ["", "In plain words:"]
    lines.append(f"- Best balance of gain and risk: {NAMES[best].lower()} ({b['return_pct']:+.1f}%, deepest fall "
                 f"{b['max_drawdown_pct']}%).")
    if bench is not None:
        lines.append("- It beat simply holding the index." if b["return_pct"] > bench else
                     "- It did NOT beat simply holding the index: as it stands, the swing pool isn't worth running.")
    if b["trades"] < 30:
        lines.append(f"- Only {b['trades']} trades: too few to trust; treat this as a rough first look.")
    lines.append("- Past results don't promise future ones; the shadow week checks the rules on live data.")
    return "\n".join(lines)


def main(argv):
    send = "--no-send" not in argv
    t0 = time.monotonic()
    data = load_data()
    print(f"Loaded {len(data)} symbols, {sum(len(v) for v in data.values())} bars in {time.monotonic() - t0:.0f}s")
    results = {k: swing.run(data, **v) for k, v in VARIANTS.items()}
    for k, r in results.items():
        print(k, json.dumps(r["summary"], indent=1))
    text = message(results)
    print(text)
    day = market.now_ist().strftime("%Y-%m-%d")
    storage.put_json(f"backtests/swing/{day}.json",
                     {k: {"summary": r["summary"], "config": r["config"], "trades": r["trades"]}
                      for k, r in results.items()})
    if send:
        import telegram_bot as tg

        tg.send(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
