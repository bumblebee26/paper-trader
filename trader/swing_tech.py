"""Swing pool, shadow strategy 2: the lakehouse's technical scanner, traded on paper next to NIFTYBEES.

A second shadow ledger (state/pools/swing_tech_shadow.json, never part of the portfolio totals), run beside the
news-breakout one (swing_shadow.py) so the two ideas can be compared on the same days with the same costs:

  signal  a strict pick in stocks.gold.technical_indicators (Nifty 200: above its 20/50/200-day averages, RSI
          50-65, MACD above its signal, ADX above 20 and rising, volume 1.5x+, reward 2x+ the risk; not extended)
  entry   the next trading day only, at the scanner's level: a "breakout-only" pick when the day trades through
          its trigger (fill at the trigger, or the open if it gaps above it); a "buy-on-dip" pick when the day
          dips into its entry zone (fill at the top of the zone, or the open if it opens inside it). Otherwise
          no trade: the signal lapses, nothing is chased.
  stop    the scanner's stop; target the scanner's T2; otherwise sold at the close after 15 trading days
  size    1% of the pool at risk per trade, at most 20% of the pool in one stock, at most 5 holdings
Daily candles decide exits, stop first when a day touches both (conservative). Delivery costs and slippage as in
the real pools. Runs with swing_shadow in the guardian at 15:50 IST (action "swing_shadow"); no Claude.
"""
import math

import config
import market
import pools
import storage
from http_client import log
from swing_shadow import _previous_trading_day, _rs, _today_bar, _trading_days_between

POOL = "swing_tech_shadow"
SIGNALS_SQL = """
SELECT CAST(trade_date AS STRING) AS d, symbol, close, setup, stance, entry_low, entry_high, trigger, stop,
       target1, target2, rr
FROM stocks.gold.technical_indicators
WHERE strict AND trade_date >= date_sub(current_date(), 7)
ORDER BY trade_date DESC, score DESC
"""
RISK_PCT = 1.0
MAX_POSITION_PCT = 20.0
HOLD_DAYS = 15
BENCHMARK = "NIFTYBEES"


def entry_price(sig, bar):
    """Where the scanner's plan fills on this day's candle, or None when it doesn't (nothing is chased)."""
    o, h, low = float(bar["open"]), float(bar["high"]), float(bar["low"])
    trig = sig.get("trigger")
    if sig.get("stance") == "breakout-only" and trig is not None:
        trig = float(trig)
        return max(o, trig) if h >= trig else None
    lo, hi = sig.get("entry_low"), sig.get("entry_high")
    if lo is None or hi is None:
        return None
    lo, hi = float(lo), float(hi)
    if o > hi:                                 # opened above the zone: only a dip back into it fills
        return hi if low <= hi else None
    if o >= lo:
        return o                               # opened inside the zone
    return None                                # opened below the zone: the setup is broken


def daily(today, signals):
    p = pools.load(POOL)
    meta = p.setdefault("meta", {})
    moves = []
    bench = _today_bar(BENCHMARK, today)
    meta.setdefault("start_date", today)
    if "start_bench" not in meta and bench:
        meta["start_bench"] = bench["open"]

    for sym in list(p["holdings"]):
        h = p["holdings"][sym]
        bar = _today_bar(sym, today)
        if not bar:
            continue
        h["last_price"] = bar["close"]
        if bar["low"] <= h["stop"]:
            price, why = min(bar["open"], h["stop"]), "stop"
        elif bar["high"] >= h["target"]:
            price, why = max(bar["open"], h["target"]), "target (T2)"
        elif _trading_days_between(h["opened"], today) >= HOLD_DAYS:
            price, why = bar["close"], f"{HOLD_DAYS} trading days"
        else:
            continue
        out = pools.sell(p, sym, price=price, reason=why)
        moves.append(f"SELL {sym} at Rs {out['fill']:,.2f} ({why}): {_rs(out['pnl_rs'])}")

    prev = _previous_trading_day(today)
    recent = set(meta.get("recent", {}))
    for s in [s for s in signals if s["d"] == prev]:
        sym = s["symbol"]
        if sym in p["holdings"] or sym in recent or s.get("stop") is None or s.get("target2") is None:
            continue
        bar = _today_bar(sym, today)
        if not bar:
            continue
        entry = entry_price(s, bar)
        stop, target = float(s["stop"]), float(s["target2"])
        if entry is None:
            moves.append(f"NO ENTRY {sym}: the day never reached its {s.get('stance')} level (not chased)")
            continue
        if entry <= stop:
            moves.append(f"SKIP {sym}: entry Rs {entry:,.2f} is already at or below its stop Rs {stop:,.2f}")
            continue
        eq = pools.equity(p)
        per_share = entry - stop
        qty = math.floor(min(eq * RISK_PCT / 100 / per_share, eq * MAX_POSITION_PCT / 100 / entry))
        if qty < 1:
            moves.append(f"SKIP {sym}: one share (Rs {entry:,.0f}) is too big for the pool's limits")
            continue
        out = pools.buy(p, sym, qty, entry, reason=f"scanner: {s.get('setup')}", stop=round(stop, 2),
                        target=round(target, 2), signal_date=prev)
        if out["status"] != "bought":
            moves.append(f"SKIP {sym}: {out['reason']}")
            continue
        recent.add(sym)
        moves.append(f"BUY {qty} {sym} at Rs {out['fill']:,.2f} (stop {stop:,.2f}, T2 {target:,.2f}). Why: scanner "
                     f"strict pick, {s.get('setup')}, R:R {float(s.get('rr') or 0):.1f}")
        h = p["holdings"][sym]
        h["last_price"] = bar["close"]
        if bar["low"] <= h["stop"]:
            out = pools.sell(p, sym, price=h["stop"], reason="stop (same day)")
            moves.append(f"SELL {sym} the same day at its stop: {_rs(out['pnl_rs'])}")
        elif bar["high"] >= h["target"]:
            out = pools.sell(p, sym, price=h["target"], reason="target (same day)")
            moves.append(f"SELL {sym} the same day at its T2: {_rs(out['pnl_rs'])}")
    meta["recent"] = {s: d for s, d in {**meta.get("recent", {}), **{s: today for s in recent}}.items()
                      if _trading_days_between(d, today) < HOLD_DAYS}
    pools.save(p)

    import swing_shadow

    snap = swing_shadow.summary(p, bench)
    snap.update(date=today, moves=moves)
    storage.put_json(f"shadow/swing_tech/{today}.json", snap)
    return snap


def summary():
    import swing_shadow

    return swing_shadow.summary(pools.load(POOL))


def lines(s):
    if not s.get("start_date"):
        return ["Swing (technical scanner): starts with the first strict pick; nothing yet."]
    return [f"Swing (technical scanner) since {s['start_date']}: {_rs(s['equity'] - config.SWING_CAPITAL)} "
            f"({s['return_pct']:+.2f}%)"
            + (f" vs NIFTYBEES {s['nifty_return_pct']:+.2f}%" if s.get("nifty_return_pct") is not None else "")
            + f"; holding {', '.join(s['holdings']) or 'nothing'}."]


def run(send=True):
    import brief
    import telegram_bot as tg

    today = market.now_ist().strftime("%Y-%m-%d")
    if not market.is_trading_day(market.now_ist()):
        return {"skipped": "market closed today"}
    try:
        signals = brief.sql(SIGNALS_SQL)
    except Exception as err:  # noqa: BLE001 - exits still run without new signals
        log.warning("scanner signals unavailable: %s", err)
        signals = []
    snap = daily(today, signals)
    if send and snap["moves"]:
        tg.send("\n".join(["[MOCK] Swing shadow, technical scanner (nothing real is bought):",
                           *[f"- {m}" for m in snap["moves"]], *lines(snap)]))
    return snap
