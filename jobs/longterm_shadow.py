"""Long-term pool, shadow mode (system plan, phase 4d): every Saturday, what the pool would do this week, under two
rule sets: the weekly quality picks (below) and, from 21 Nov, momentum (momentum_month: the Nifty 200's top 15 by
volatility-adjusted 6- and 12-month momentum, rebalanced monthly).

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


REAL_POOL = "long_term"   # auto mode (pool_auto.py): the real pool follows the same rules; off until the 18 Dec review


def week(today, cands, nifty, pool=POOL):
    """One week of the quality rules on `pool` (the shadow, or the real pool in auto mode). cands: rows of
    longterm_candidates; nifty: [(date, close)] newest first."""
    num = lambda v: float(v) if v not in (None, "") else None  # noqa: E731
    p = pools.load(pool)
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
        target = pools.capital(pool) * float(c["target_weight_pct"] or 9) / 100
        qty = math.floor(target / (px * (1 + config.SLIPPAGE_PCT / 100) * (1 + config.DELIVERY_COST_PCT / 100)))
        out = pools.buy(p, sym, qty, px, reason="weekly pick", industry=c.get("industry"))
        if out["status"] == "bought":
            moves.append(f"BUY {sym} ({c.get('company') or sym}, {c.get('industry') or 'industry n/a'}): {qty} shares "
                         f"for about {inr(qty * out['fill'])}. Why: {c.get('reasons')}")
        else:
            moves.append(f"SKIP {sym}: {out['reason']}.")
    pools.save(p)

    eq = pools.equity(p)
    ret = (eq / pools.capital(pool) - 1) * 100
    nret = (n_close / meta["start_nifty"] - 1) * 100 if n_close and meta.get("start_nifty") else None
    snap = {"date": today, "week": meta["weeks"], "equity": round(eq, 2), "return_pct": round(ret, 2),
            "nifty_return_pct": round(nret, 2) if nret is not None else None, "nifty_below_high_pct": dip,
            "moves": moves, "holdings": {s: {"qty": h["qty"], "last_price": h.get("last_price")} for s, h in p["holdings"].items()},
            "picks": [c["symbol"] for c in picks], "pool": pool}
    folder = "longterm" if pool == POOL else "longterm_auto"
    storage.put_json(f"shadow/{folder}/{today}.json", snap)
    storage.put_json(f"shadow/{folder}/latest.json", snap)   # for /shadow
    return snap


# ------------------------------------------------------------------ momentum (from 21 Nov, paper) --
MOM_POOL = "long_term_mom_shadow"
MOM_CONC_POOL = "long_term_mom_conc_shadow"
MOM_TOP, MOM_KEEP = 15, 30         # buy the top 15; keep a holding while it stays in the top 30
# pool -> (label, buy the top N, keep while in the top M, snapshot folder). The concentrated copy (from 18 Nov) holds 5,
# about a fifth of the pool each, no sector cap, to see whether concentrating pays more than spreading across 15.
MOM_VARIANTS = {
    MOM_POOL: ("top 15", MOM_TOP, MOM_KEEP, "longterm_mom"),
    MOM_CONC_POOL: ("top 5, concentrated", 5, 10, "longterm_mom_conc"),
}
MOM_CASH_PCT = 2.0                 # left in cash so costs never overdraw the pool
MOM_SQL = """
SELECT symbol, company, industry, CAST(lt_rank AS INT) AS lt_rank, lt_score, close, ret_6m_pct, ret_12m_pct,
       vol_1y_pct, CAST(trade_date AS STRING) AS d
FROM stocks.gold.momentum_ranks WHERE lt_rank IS NOT NULL ORDER BY lt_rank
"""


def momentum_month(today, ranks, nifty, pool=MOM_POOL):
    """Long-term momentum shadow: rebalanced on the first Saturday run of each month (and on the very first run);
    other weeks it is only valued. Top 15 by volatility-adjusted 6- and 12-month momentum (the Momentum 30 method),
    equal weights; a holding is kept while it stays in the top 30. Fills at Friday's close plus slippage and
    delivery costs."""
    label, top, keep, folder = MOM_VARIANTS[pool]
    num = lambda v: float(v) if v not in (None, "") else None  # noqa: E731
    p = pools.load(pool)
    meta = p.setdefault("meta", {})
    price = {r["symbol"]: num(r["close"]) for r in ranks if num(r["close"])}
    rank = {r["symbol"]: int(r["lt_rank"]) for r in ranks}
    n_close = num(nifty[0]["close"]) if nifty else None
    if "start_date" not in meta:
        meta.update(start_date=today, start_nifty=n_close)
    for sym, h in p["holdings"].items():
        if price.get(sym):
            h["last_price"] = price[sym]
    moves = []
    rebalance = ranks and (not meta.get("last_rebalance") or meta["last_rebalance"][:7] != today[:7])
    if rebalance:
        for sym in list(p["holdings"]):
            r = rank.get(sym)
            if r is None or r > keep:
                px = price.get(sym) or p["holdings"][sym].get("last_price")
                out = pools.sell(p, sym, price=px, reason=f"left the momentum top {keep}")
                moves.append(f"SELL {sym}: now {'unranked' if r is None else f'rank {r}'}, out of the top {keep} "
                             f"({inr(out['pnl_rs'], sign=True)} on it).")
        eq = pools.equity(p)
        target = eq * (1 - MOM_CASH_PCT / 100) / top
        for r in [r for r in ranks if int(r["lt_rank"]) <= top and r["symbol"] not in p["holdings"]]:
            sym, px = r["symbol"], price.get(r["symbol"])
            if not px or len(p["holdings"]) >= top:
                continue
            qty = math.floor(target / (px * (1 + config.SLIPPAGE_PCT / 100) * (1 + config.DELIVERY_COST_PCT / 100)))
            out = pools.buy(p, sym, qty, px, reason=f"momentum rank {r['lt_rank']}", industry=r.get("industry"))
            if out["status"] == "bought":
                moves.append(f"BUY {sym} ({r.get('company') or sym}): rank {r['lt_rank']}, 6m {float(r['ret_6m_pct']):+.0f}%, "
                             f"12m {float(r['ret_12m_pct']):+.0f}%, {qty} shares for about {inr(qty * out['fill'])}.")
            else:
                moves.append(f"SKIP {sym}: {out['reason']}.")
        meta["last_rebalance"] = today
    pools.save(p)
    eq = pools.equity(p)
    ret = (eq / pools.capital(pool) - 1) * 100
    nret = (n_close / meta["start_nifty"] - 1) * 100 if n_close and meta.get("start_nifty") else None
    snap = {"date": today, "pool": pool, "label": label, "start_date": meta["start_date"],
            "rebalanced": bool(rebalance), "equity": round(eq, 2),
            "return_pct": round(ret, 2), "nifty_return_pct": round(nret, 2) if nret is not None else None,
            "moves": moves, "holdings": {s: {"qty": h["qty"], "last_price": h.get("last_price"), "rank": rank.get(s)}
                                         for s, h in p["holdings"].items()}}
    storage.put_json(f"shadow/{folder}/{today}.json", snap)
    storage.put_json(f"shadow/{folder}/latest.json", snap)
    return snap


def momentum_message(s):
    head = "rebalanced this month" if s["rebalanced"] else "valued (rebalances on the first Saturday of the month)"
    lines = [f"[MOCK] Long-term momentum shadow ({s.get('label') or 'top 15'}), {head} (nothing real is bought):"]
    lines += [f"- {m}" for m in s["moves"]]
    lines.append(f"Pool {inr(s['equity'])} ({s['return_pct']:+.2f}%) since {s['start_date']}"
                 + (f"; NIFTYBEES {s['nifty_return_pct']:+.2f}%" if s["nifty_return_pct"] is not None else "")
                 + f"; holding {len(s['holdings'])} stocks.")
    return "\n".join(lines)


def message(s):
    real = s.get("pool") == REAL_POOL
    lines = [f"[MOCK] Long-term pool, auto mode week {s['week']} (quality picks; dummy money)" if real
             else f"[MOCK] Long-term pool, shadow week {s['week']} (nothing real is bought)", ""]
    lines += ([("This week:" if real else "This week I would:")] + [f"- {m}" for m in s["moves"]] if s["moves"]
              else ["Nothing to do this week."])
    held = ", ".join(s["holdings"]) or "nothing yet"
    lines += ["", f"{'Pool' if real else 'Shadow pool'} now: {inr(s['equity'])} ({s['return_pct']:+.2f}%), holding {held}."]
    if s["nifty_return_pct"] is not None:
        lines.append(f"NIFTYBEES since the first shadow week: {s['nifty_return_pct']:+.2f}%.")
    lines.append(f"Nifty is {s['nifty_below_high_pct']}% below its 52-week high"
                 + (": buying two picks this week." if s["nifty_below_high_pct"] >= DIP_PCT else "."))
    lines.append("This week's 10 picks: " + ", ".join(s["picks"]) + ". Full list and reasons: dashboard, Company quality page.")
    return "\n".join(lines)


def main(argv):
    today = market.now_ist().strftime("%Y-%m-%d")
    nifty = sql(NIFTY_SQL)
    import pool_auto

    cands = sql(CANDIDATES_SQL)
    s = week(today, cands, nifty)
    texts = [message(s)]
    print(json.dumps(s, indent=1))
    if pool_auto.on(REAL_POOL):
        if pools.breaker_on():
            texts.append("[MOCK] Long-term pool, auto mode: no buys this week (portfolio circuit breaker is on).")
        else:
            r = week(today, cands, nifty, pool=REAL_POOL)
            texts.append(message(r))
            print(json.dumps(r, indent=1))
    try:
        ranks = sql(MOM_SQL)
        for pool in MOM_VARIANTS:
            m = momentum_month(today, ranks, nifty, pool)
            texts.append(momentum_message(m))
            print(json.dumps(m, indent=1))
    except Exception as err:  # noqa: BLE001 - the quality shadow still reports
        print(f"momentum shadow skipped: {err}")
    for text in texts:
        print(text)
    if "--no-send" not in argv:
        import telegram_bot as tg

        for text in texts:
            tg.send(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
