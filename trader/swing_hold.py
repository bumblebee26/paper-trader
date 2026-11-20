"""Swing holding period on paper (from 21 Nov): would selling sooner have done better?

For every buy in the swing shadow pools over the last LOOKBACK_DAYS, the same trade with a shorter maximum hold:
if it really closed within N trading days (stop, target or anything else), the result is the real one; otherwise
it is sold at the close of the Nth trading day after the buy (delivery costs and slippage as in the pools).
Trades still open and younger than N days are left out of that N. Daily candles from Yahoo; nothing is traded.
Read by the Friday weekly report and /shadow.
"""
from datetime import timedelta

import config
import market
import pools
import storage

POOLS = {"swing_shadow": "Swing shadow: news breakouts", "swing_tech_shadow": "Swing shadow: technical scanner",
         "swing_mom_shadow": "Swing shadow: momentum + news",
         "swing_mom_conc_shadow": "Swing shadow: momentum, concentrated"}
HOLDS = (5, 10)
LOOKBACK_DAYS = 45


def _trades(pool, today):
    """[(symbol, buy date, qty, buy fill + fees per share, sell date or None, real pnl or None)] oldest first."""
    rows = []
    for i in range(LOOKBACK_DAYS, -1, -1):
        rows += storage.read_rows(f"journal/pools/{pool}/{today - timedelta(days=i):%Y-%m-%d}.csv")
    open_buys, out = {}, []
    for r in rows:
        if r["event"] == "bought":
            qty, value, fees = int(float(r["qty"])), float(r["value_rs"]), float(r.get("fees_rs") or 0)
            open_buys[r["symbol"]] = {"sym": r["symbol"], "day": r["timestamp_ist"][:10], "qty": qty,
                                      "cost": value + fees, "sold": None, "pnl": None}
        elif r["event"] == "sold" and r["symbol"] in open_buys:
            t = open_buys.pop(r["symbol"])
            t.update(sold=r["timestamp_ist"][:10], pnl=float(r.get("pnl_rs") or 0))
            out.append(t)
    return out + list(open_buys.values())


def _days_after(day, bars):
    """Daily candles strictly after `day`, oldest first."""
    return [b for b in bars if b["time"][:10] > day]


def replay(trade, bars, n):
    """The trade with a maximum hold of n trading days: (pnl, decided?)."""
    after = _days_after(trade["day"], bars)
    if trade["sold"] and len([b for b in after if b["time"][:10] <= trade["sold"]]) <= n:
        return trade["pnl"], True                       # it really closed within n days
    if len(after) < n:
        return None, False                              # still too young to judge
    exit_px = round(after[n - 1]["close"] * (1 - config.SLIPPAGE_PCT / 100), 2)
    value = exit_px * trade["qty"]
    return round(value - pools.costs("SELL", value) - trade["cost"], 2), True


def analyse(today=None):
    today = today or market.now_ist().date()
    out, cache = {}, {}
    for pool, label in POOLS.items():
        res = {"label": label, "trades": 0, "actual": 0.0, **{f"hold_{n}": 0.0 for n in HOLDS}}
        for t in _trades(pool, today):
            if t["sym"] not in cache:
                try:
                    cache[t["sym"]] = market.daily(t["sym"], "3mo")
                except Exception:  # noqa: BLE001 - that stock is left out
                    cache[t["sym"]] = []
            bars = cache[t["sym"]]
            results = [replay(t, bars, n) for n in HOLDS]
            if t["pnl"] is None or not all(ok for _, ok in results):
                continue                                   # compare only trades closed and judged both ways
            res["trades"] += 1
            res["actual"] += t["pnl"]
            for n, (pnl, _) in zip(HOLDS, results):
                res[f"hold_{n}"] += pnl
        out[pool] = {k: round(v, 2) if isinstance(v, float) else v for k, v in res.items()}
    return out
