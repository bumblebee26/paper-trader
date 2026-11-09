"""Long-term pool, shadow mode (system plan, phase 4d): every Saturday, what the pool would do this week.

  python jobs/longterm_shadow.py [--no-send]

Reads this week's picks from stocks.gold.longterm_candidates (refreshed after Saturday's fundamentals),
then, in a SHADOW ledger (state/pools/long_term_shadow.json, never part of the real portfolio):
  buy    the highest-ranked pick not yet held, about 9% of the Rs 1 lakh pool each; two picks when
         NIFTYBEES is 5% or more below its 52-week high (buying faster on dips), so the pool fills
         over roughly 6 to 10 weeks
  sell   a holding that no longer qualifies (dropped out of the eligible list: quality fell, profit
         fell two quarters in a row, or it became loss-making)
  trim   a holding above 18% of the pool, back to 15%
Fills use Friday's close plus slippage and delivery costs. The Telegram report explains each move in
plain words and compares the shadow pool with NIFTYBEES since the first shadow week.
Runs in GitHub Actions (workflow "Long-term shadow", Saturdays 10:15 IST). No Claude.
"""
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "trader"), os.path.join(HERE, "..", "backtest")]

import config  # noqa: E402
import market  # noqa: E402
import pools  # noqa: E402
import storage  # noqa: E402

POOL = "long_term_shadow"
CANDIDATES_SQL = """
SELECT CAST(rank AS INT) AS rank, is_pick, symbol, company, industry, score, target_weight_pct, reasons,
       close, CAST(price_date AS STRING) AS price_date
FROM stocks.gold.longterm_candidates ORDER BY rank
"""
NIFTY_SQL = """
SELECT CAST(trade_date AS STRING) AS d, close FROM stocks.silver.nse_eq_daily
WHERE symbol = 'NIFTYBEES' AND series = 'EQ' ORDER BY trade_date DESC LIMIT 252
"""
TRIM_ABOVE_PCT, TRIM_TO_PCT, DIP_PCT = 18.0, 15.0, 5.0


def sql(statement):
    import run_swing

    return run_swing.sql_all(statement)


def inr(x, sign=False):
    import engine

    return engine.inr(x, sign=sign)


def week(today, cands, nifty):
    """One shadow week. cands: rows of longterm_candidates; nifty: [(date, close)] newest first."""
    num = lambda v: float(v) if v not in (None, "") else None  # noqa: E731
    p = pools.load(POOL)
    meta = p.setdefault("meta", {})
    price = {c["symbol"]: num(c["close"]) for c in cands if num(c["close"])}
    picks = [c for c in cands if str(c["is_pick"]).lower() == "true"]
    eligible = {c["symbol"] for c in cands}
    n_close = num(nifty[0]["close"]) if nifty else None
    high = max((num(r["close"]) for r in nifty), default=None)
    dip = round((1 - n_close / high) * 100, 1) if n_close and high else 0.0
    if "start_date" not in meta:
        meta.update(start_date=today, start_nifty=n_close, weeks=0)
    meta["weeks"] = meta.get("weeks", 0) + 1
    for sym, h in p["holdings"].items():
        if price.get(sym):
            h["last_price"] = price[sym]
    moves = []

    # 1) sell what no longer qualifies
    for sym in list(p["holdings"]):
        if sym not in eligible and price.get(sym, p["holdings"][sym].get("last_price")):
            out = pools.sell(p, sym, price=price.get(sym) or p["holdings"][sym]["last_price"],
                             reason="no longer qualifies")
            moves.append(f"SELL {sym}: it no longer passes the quality rules "
                         f"({inr(out['pnl_rs'], sign=True)} on it).")
    # 2) trim anything that grew too big
    eq = pools.equity(p)
    for sym, h in list(p["holdings"].items()):
        value = h["qty"] * h["last_price"]
        if eq and value / eq * 100 > TRIM_ABOVE_PCT:
            qty = math.floor((value - eq * TRIM_TO_PCT / 100) / h["last_price"])
            if qty > 0:
                pools.sell(p, sym, qty=qty, price=h["last_price"], reason="trim to 15%")
                moves.append(f"TRIM {sym}: it grew to {value / eq * 100:.0f}% of the pool; sell {qty} shares to "
                             "bring it back to 15%.")
    # 3) buy the next picks
    n_buy = 2 if dip >= DIP_PCT else 1
    for c in [c for c in picks if c["symbol"] not in p["holdings"]][:n_buy]:
        sym, px = c["symbol"], price.get(c["symbol"])
        if not px:
            continue
        target = pools.capital(POOL) * float(c["target_weight_pct"] or 9) / 100
        qty = math.floor(target / (px * (1 + config.SLIPPAGE_PCT / 100) * (1 + config.DELIVERY_COST_PCT / 100)))
        out = pools.buy(p, sym, qty, px, reason="weekly pick", industry=c.get("industry"))
        if out["status"] == "bought":
            moves.append(f"BUY {sym} ({c.get('company') or sym}, {c.get('industry') or 'industry n/a'}): {qty} shares "
                         f"for about {inr(qty * out['fill'])}. Why: {c.get('reasons')}")
        else:
            moves.append(f"SKIP {sym}: {out['reason']}.")
    pools.save(p)

    eq = pools.equity(p)
    ret = (eq / pools.capital(POOL) - 1) * 100
    nret = (n_close / meta["start_nifty"] - 1) * 100 if n_close and meta.get("start_nifty") else None
    snap = {"date": today, "week": meta["weeks"], "equity": round(eq, 2), "return_pct": round(ret, 2),
            "nifty_return_pct": round(nret, 2) if nret is not None else None, "nifty_below_high_pct": dip,
            "moves": moves, "holdings": {s: {"qty": h["qty"], "last_price": h.get("last_price")} for s, h in p["holdings"].items()},
            "picks": [c["symbol"] for c in picks]}
    storage.put_json(f"shadow/longterm/{today}.json", snap)
    return snap


def message(s):
    lines = [f"[MOCK] Long-term pool, shadow week {s['week']} (nothing real is bought)", ""]
    lines += ["This week I would:"] + [f"- {m}" for m in s["moves"]] if s["moves"] else ["Nothing to do this week."]
    held = ", ".join(s["holdings"]) or "nothing yet"
    lines += ["", f"Shadow pool now: {inr(s['equity'])} ({s['return_pct']:+.2f}%), holding {held}."]
    if s["nifty_return_pct"] is not None:
        lines.append(f"NIFTYBEES since the first shadow week: {s['nifty_return_pct']:+.2f}%.")
    lines.append(f"Nifty is {s['nifty_below_high_pct']}% below its 52-week high"
                 + (": buying two picks this week." if s["nifty_below_high_pct"] >= DIP_PCT else "."))
    lines.append("This week's 10 picks: " + ", ".join(s["picks"]) + ". Full list and reasons: dashboard, Company quality page.")
    return "\n".join(lines)


def main(argv):
    today = market.now_ist().strftime("%Y-%m-%d")
    s = week(today, sql(CANDIDATES_SQL), sql(NIFTY_SQL))
    text = message(s)
    print(json.dumps(s, indent=1))
    print(text)
    if "--no-send" not in argv:
        import telegram_bot as tg

        tg.send(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
