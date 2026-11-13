"""Swing pool, shadow mode: results- and news-driven breakouts, traded on paper next to NIFTYBEES.

The real swing pool (Rs 60,000) stays in NIFTYBEES because the original swing rules failed their backtest. This
shadow ledger (state/pools/swing_shadow.json, never part of the portfolio totals) tests a new idea instead:

  signal  stocks.gold.swing_signals (lakehouse): an eligible Nifty 200 stock that, within 2 days of an NSE filing
          (results, an order, a merger...), closed above its 20-day high on 2x its usual volume, up 2% or more
          and above its 50-day average. Eligible = main-board EQ series, close >= Rs 50, Rs 50 crore+ a day.
  entry   the next trading day's open (the signal is known after the 19:30 bhavcopy, so no look-ahead)
  stop    the 5-day low at the signal, at most 8% below; skipped if the open is already below it
  target  2x the risk above the entry; otherwise sold at the close after 10 trading days
  size    1% of the shadow pool at risk per trade, at most 20% of the pool in one stock, at most 5 holdings
Daily candles decide exits, stop first when a day touches both (conservative). Delivery costs and slippage as
in the real pools.

Runs in the guardian Lambda at 15:50 IST on trading days (action "swing_shadow"); no Claude. Results: /shadow on
Telegram, the Friday weekly report, and the lakehouse (pool journal exported with the portfolio snapshot).
"""
import math

import config
import market
import pools
import storage
from http_client import log

POOL = "swing_shadow"
SIGNALS_SQL = """
SELECT CAST(trade_date AS STRING) AS d, symbol, company, industry, close, return_pct, volume_ratio, stop,
       news_category, news_title
FROM stocks.gold.swing_signals
WHERE trade_date >= date_sub(current_date(), 7)
ORDER BY trade_date DESC, volume_ratio DESC
"""
RISK_PCT = 1.0
MAX_POSITION_PCT = 20.0
HOLD_DAYS = 10
RR = 2.0
BENCHMARK = "NIFTYBEES"


def _today_bar(symbol, today):
    try:
        bars = market.candles(symbol, "1d", 5)
    except Exception as err:  # noqa: BLE001 - one bad quote skips that stock today
        log.warning("daily candle for %s unavailable: %s", symbol, err)
        return None
    return next((b for b in bars if b["time"].startswith(today)), None)


def _trading_days_between(start, today):
    from datetime import date, timedelta

    d, end, n = date.fromisoformat(start), date.fromisoformat(today), 0
    while d < end:
        d += timedelta(days=1)
        if market.is_trading_day(d):
            n += 1
    return n


def _previous_trading_day(today):
    from datetime import date, timedelta

    d = date.fromisoformat(today) - timedelta(days=1)
    while not market.is_trading_day(d):
        d -= timedelta(days=1)
    return d.isoformat()


def daily(today, signals):
    """One trading day: exits on today's candle, then entries at today's open for yesterday's signals."""
    p = pools.load(POOL)
    meta = p.setdefault("meta", {})
    moves = []
    bench = _today_bar(BENCHMARK, today)
    meta.setdefault("start_date", today)
    if "start_bench" not in meta and bench:
        meta["start_bench"] = bench["open"]

    # 1) exits: stop first, then target, then the 10-day time limit
    for sym in list(p["holdings"]):
        h = p["holdings"][sym]
        bar = _today_bar(sym, today)
        if not bar:
            continue
        h["last_price"] = bar["close"]
        if bar["low"] <= h["stop"]:
            price, why = min(bar["open"], h["stop"]), "stop"
        elif bar["high"] >= h["target"]:
            price, why = max(bar["open"], h["target"]), "target"
        elif _trading_days_between(h["opened"], today) >= HOLD_DAYS:
            price, why = bar["close"], f"{HOLD_DAYS} trading days"
        else:
            continue
        out = pools.sell(p, sym, price=price, reason=why)
        moves.append(f"SELL {sym} at Rs {out['fill']:,.2f} ({why}): {_rs(out['pnl_rs'])}")

    # 2) entries: signals from the previous trading day, at today's open
    prev = _previous_trading_day(today)
    recent = set(meta.get("recent", {}))
    for s in [s for s in signals if s["d"] == prev]:
        sym = s["symbol"]
        if sym in p["holdings"] or sym in recent:
            continue
        bar = _today_bar(sym, today)
        if not bar:
            continue
        entry, stop = float(bar["open"]), float(s["stop"])
        if entry <= stop:
            moves.append(f"SKIP {sym}: opened at Rs {entry:,.2f}, already below its stop Rs {stop:,.2f}")
            continue
        eq = pools.equity(p)
        per_share = entry - stop
        qty = math.floor(min(eq * RISK_PCT / 100 / per_share, eq * MAX_POSITION_PCT / 100 / entry))
        if qty < 1:
            moves.append(f"SKIP {sym}: one share (Rs {entry:,.0f}) is too big for the pool's limits")
            continue
        out = pools.buy(p, sym, qty, entry, reason=f"news breakout: {s.get('news_category')}",
                        stop=round(stop, 2), target=round(entry + RR * per_share, 2), signal_date=prev,
                        news=(s.get("news_title") or "")[:160])
        if out["status"] != "bought":
            moves.append(f"SKIP {sym}: {out['reason']}")
            continue
        recent.add(sym)
        moves.append(f"BUY {qty} {sym} at Rs {out['fill']:,.2f} (stop {stop:,.2f}, target "
                     f"{entry + RR * per_share:,.2f}). Why: {s.get('news_category')} filing, closed "
                     f"{float(s['return_pct']):+.1f}% above its 20-day high on {float(s['volume_ratio']):.1f}x volume")
        # today's candle can already hit the stop or target after the open
        h = p["holdings"][sym]
        h["last_price"] = bar["close"]
        if bar["low"] <= h["stop"]:
            out = pools.sell(p, sym, price=h["stop"], reason="stop (same day)")
            moves.append(f"SELL {sym} the same day at its stop: {_rs(out['pnl_rs'])}")
        elif bar["high"] >= h["target"]:
            out = pools.sell(p, sym, price=h["target"], reason="target (same day)")
            moves.append(f"SELL {sym} the same day at its target: {_rs(out['pnl_rs'])}")
    # a stock bought in the last 10 days isn't bought again on a repeat signal
    meta["recent"] = {s: d for s, d in {**meta.get("recent", {}), **{s: today for s in recent}}.items()
                      if _trading_days_between(d, today) < HOLD_DAYS}
    pools.save(p)

    snap = summary(p, bench)
    snap.update(date=today, moves=moves)
    storage.put_json(f"shadow/swing/{today}.json", snap)
    return snap


def summary(p=None, bench=None):
    p = p or pools.load(POOL)
    meta = p.get("meta") or {}
    eq = pools.equity(p)
    if bench is None and meta.get("start_bench"):
        try:
            bench = {"close": market.last_price(BENCHMARK)[0]}
        except Exception:  # noqa: BLE001
            bench = None
    bench_ret = ((bench["close"] / meta["start_bench"] - 1) * 100
                 if bench and bench.get("close") and meta.get("start_bench") else None)
    return {"equity": round(eq, 2), "return_pct": round((eq / p["capital"] - 1) * 100, 2),
            "realized": round(p.get("realized", 0.0), 2), "start_date": meta.get("start_date"),
            "nifty_return_pct": round(bench_ret, 2) if bench_ret is not None else None,
            "holdings": {s: {"qty": h["qty"], "stop": h.get("stop"), "target": h.get("target"),
                             "opened": h.get("opened"), "last_price": h.get("last_price")}
                         for s, h in p["holdings"].items()}}


def lines(s):
    if not s.get("start_date"):
        return ["Swing (news breakouts): starts with the first signal; nothing yet."]
    out = [f"Swing (news breakouts) since {s['start_date']}: {_rs(s['equity'] - config.SWING_CAPITAL)} "
           f"({s['return_pct']:+.2f}%)"
           + (f" vs NIFTYBEES {s['nifty_return_pct']:+.2f}%" if s.get("nifty_return_pct") is not None else "")
           + f"; holding {', '.join(s['holdings']) or 'nothing'}."]
    return out


def run(send=True):
    import brief
    import telegram_bot as tg

    today = market.now_ist().strftime("%Y-%m-%d")
    if not market.is_trading_day(market.now_ist()):
        return {"skipped": "market closed today"}
    try:
        signals = brief.sql(SIGNALS_SQL)
    except Exception as err:  # noqa: BLE001 - exits still run without new signals
        log.warning("swing signals unavailable: %s", err)
        signals = []
    snap = daily(today, signals)
    if send and snap["moves"]:
        tg.send("\n".join(["[MOCK] Swing shadow (nothing real is bought):", *[f"- {m}" for m in snap["moves"]],
                           *lines(snap)]))
    return snap


def _rs(x):
    import engine

    return engine.inr(x, sign=True)
