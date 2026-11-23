"""Swing pool, shadow strategy 3 (from 18 Nov): momentum + news on the Nifty 200, more risk on purpose.

A third swing shadow ledger (state/pools/swing_mom_shadow.json, never part of the portfolio totals), run beside the
news-breakout and scanner shadows so all three can be compared on the same days with the same costs:

  signal  the top of stocks.gold.momentum_ranks (lakehouse) on the last NSE day: swing_eligible stocks (uptrend,
          1-month return above 0, RSI under 80) ranked by swing_score (1- and 3-month strength, near the 52-week
          high, a fresh positive filing, a strict scanner pick; a regulatory or pledge filing counts against)
  entry   the next trading day's open, best-ranked first, while the pool has room (6 holdings)
  stop    2 x ATR(14) under the entry, then a trailing stop 2 x ATR under the highest close since entry
  exit    the trailing stop, or the close after 20 trading days
  size    2% of the pool at risk per trade (double the other shadows), at most 1/6 of the pool in one stock

A concentrated copy (state/pools/swing_mom_conc_shadow.json, from 18 Nov) runs the same signals, entries and exits
with 3 holdings, up to a third of the pool in one stock, 3% at risk per trade and no sector cap, so the December
review can see whether concentrating on the top ranks pays more than spreading across six.
From 24 Nov the REAL swing pool follows the same rules in auto mode (pool "swing", pool_auto.py): same signals,
entries, stops and exits, 6 stocks; its idle cash stays in NIFTYBEES, which is sold at the day's open to pay for a buy.
A stock already in the pool without these rules' stop (e.g. a buy you placed yourself) is taken over: stop 2 x ATR
under its cost, trailing from then on, out after 20 trading days from when it was bought.
Daily candles decide exits. Delivery costs and slippage as in the real pools. Runs with the other swing shadows in
the guardian at 15:50 IST (action "swing_shadow"); no Claude.
"""
import math

import config
import market
import pools
import storage
from http_client import log
from swing_shadow import _previous_trading_day, _rs, _today_bar, _trading_days_between

POOL = "swing_mom_shadow"
CONC_POOL = "swing_mom_conc_shadow"
# pool -> (label, % of the pool at risk per trade, most of the pool in one stock, snapshot folder)
REAL_POOL = "swing"
REAL_STOCKS = 6                                               # the real pool's stocks (NIFTYBEES not counted)
VARIANTS = {
    POOL: ("momentum + news", 2.0, 16.5, "swing_mom"),          # 6 holdings must fit in one pool
    REAL_POOL: ("momentum + news (real pool, auto)", 2.0, 16.5, "swing_auto"),
    CONC_POOL: ("momentum + news, concentrated (top 3)", 3.0, 32.5, "swing_mom_conc"),
}
SIGNALS_SQL = """
SELECT CAST(trade_date AS STRING) AS d, symbol, company, swing_rank, swing_score, atr14, close, ret_1m_pct,
       ret_3m_pct, pct_of_52w_high, news_pos_5d, latest_good_news, scanner_strict
FROM stocks.gold.momentum_ranks
WHERE swing_eligible AND swing_rank <= 12
ORDER BY swing_rank
"""
RISK_PCT, MAX_POSITION_PCT = VARIANTS[POOL][1], VARIANTS[POOL][2]
ATR_X = 2.0
HOLD_DAYS = 20


def _atr(sym, today, n=14):
    """ATR(14) from daily candles up to today (Yahoo); None if unavailable."""
    bars = [b for b in market.daily(sym, "3mo") if b["time"][:10] <= today and b.get("close")]
    if len(bars) < n + 1:
        return None
    trs = [max(b["high"] - b["low"], abs(b["high"] - a["close"]), abs(b["low"] - a["close"]))
           for a, b in zip(bars[-n - 1:-1], bars[-n:])]
    return round(sum(trs) / n, 2)


def _adopt(p, today, moves):
    """Real pool: put any stock without these rules' stop under them (a buy placed by hand before auto mode)."""
    for sym, h in p["holdings"].items():
        if sym in pools.INDEX_FUNDS or h.get("atr"):
            continue
        try:
            atr = _atr(sym, today)
        except Exception as err:  # noqa: BLE001 - try again tomorrow
            log.warning("ATR for %s unavailable: %s", sym, err)
            atr = None
        if not atr:
            continue
        h.update(atr=atr, stop=round(float(h["avg_cost"]) - ATR_X * atr, 2), origin="auto",
                 high_close=float(h.get("last_price") or h["avg_cost"]))
        h.pop("target", None)
        moves.append(f"TAKEN OVER {sym}: now managed by the auto rules (stop {h['stop']:,.2f}, trails 2x ATR, "
                     f"out after {HOLD_DAYS} trading days)")


def _fund(p, today, value, moves):
    """Real pool: sell enough NIFTYBEES at today's open to pay for a buy of `value`."""
    park = config.SWING_PARK_SYMBOL
    need = value * (1 + (config.DELIVERY_COST_PCT + config.SLIPPAGE_PCT) / 100) - p["cash"]
    h = p["holdings"].get(park) if park else None
    if need <= 0 or not h:
        return
    bar = _today_bar(park, today)
    px = bar["open"] if bar else (h.get("last_price") or h["avg_cost"])
    qty = min(h["qty"], math.ceil(need * 1.01 / (px * (1 - (config.DELIVERY_COST_PCT + config.SLIPPAGE_PCT) / 100))))
    out = pools.sell(p, park, qty=qty, price=px, reason="pays for a momentum buy")
    moves.append(f"SELL {out['qty']} {park} at Rs {out['fill']:,.2f} to pay for it")


def daily(today, signals, pool=POOL):
    _, risk_pct, max_position_pct, folder = VARIANTS[pool]
    p = pools.load(pool)
    meta = p.setdefault("meta", {})
    moves = []
    real = pool == REAL_POOL
    if real:
        _adopt(p, today, moves)
    bench = _today_bar("NIFTYBEES", today)
    meta.setdefault("start_date", today)
    if "start_bench" not in meta and bench:
        meta["start_bench"] = bench["open"]

    # 1) exits on today's candle: the trailing stop, then the 20-day limit; then raise the trail on today's close
    for sym in list(p["holdings"]):
        h = p["holdings"][sym]
        if sym in pools.INDEX_FUNDS or "stop" not in h or "atr" not in h:
            continue                                     # the NIFTYBEES park, or a stock not yet taken over
        bar = _today_bar(sym, today)
        if not bar:
            continue
        h["last_price"] = bar["close"]
        if bar["low"] <= h["stop"]:
            out = pools.sell(p, sym, price=min(bar["open"], h["stop"]), reason="trailing stop")
            moves.append(f"SELL {sym} at Rs {out['fill']:,.2f} (trailing stop): {_rs(out['pnl_rs'])}")
            continue
        if _trading_days_between(h["opened"], today) >= HOLD_DAYS:
            out = pools.sell(p, sym, price=bar["close"], reason=f"{HOLD_DAYS} trading days")
            moves.append(f"SELL {sym} at Rs {out['fill']:,.2f} ({HOLD_DAYS} trading days): {_rs(out['pnl_rs'])}")
            continue
        h["high_close"] = max(float(h.get("high_close") or 0), bar["close"])
        h["stop"] = round(max(h["stop"], h["high_close"] - ATR_X * float(h["atr"])), 2)

    # 2) entries: the previous NSE day's ranks, best first, at today's open
    prev = _previous_trading_day(today)
    recent = set(meta.get("recent", {}))
    for s in sorted([s for s in signals if s["d"] == prev], key=lambda s: int(s["swing_rank"])):
        stocks = [s for s in p["holdings"] if s not in pools.INDEX_FUNDS]
        if len(stocks) >= (REAL_STOCKS if real else pools.LIMITS[pool]["max_holdings"]):
            break
        sym = s["symbol"]
        if sym in p["holdings"] or sym in recent or not s.get("atr14"):
            continue
        bar = _today_bar(sym, today)
        if not bar:
            continue
        entry, atr = float(bar["open"]), float(s["atr14"])
        stop = entry - ATR_X * atr
        eq = pools.equity(p)
        qty = math.floor(min(eq * risk_pct / 100 / (entry - stop), eq * max_position_pct / 100 / entry))
        if qty < 1:
            moves.append(f"SKIP {sym}: one share (Rs {entry:,.0f}) is too big for the pool's limits")
            continue
        if real:
            _fund(p, today, qty * entry, moves)
        out = pools.buy(p, sym, qty, entry, reason=f"momentum rank {s['swing_rank']}", stop=round(stop, 2),
                        atr=round(atr, 2), high_close=entry, signal_date=prev)
        if out["status"] != "bought":
            moves.append(f"SKIP {sym}: {out['reason']}")
            continue
        recent.add(sym)
        why = [f"rank {s['swing_rank']}", f"1m {float(s['ret_1m_pct']):+.1f}%", f"3m {float(s['ret_3m_pct']):+.1f}%",
               f"{float(s['pct_of_52w_high']):.0f}% of its 52-week high"]
        if int(s.get("news_pos_5d") or 0):
            why.append(f"news: {(s.get('latest_good_news') or '')[:80]}")
        if str(s.get("scanner_strict")).lower() == "true":
            why.append("scanner strict pick")
        moves.append(f"BUY {qty} {sym} at Rs {out['fill']:,.2f} (stop {stop:,.2f}, trails 2x ATR). Why: "
                     + ", ".join(why))
        h = p["holdings"][sym]
        h["last_price"] = bar["close"]
        if bar["low"] <= h["stop"]:
            out = pools.sell(p, sym, price=h["stop"], reason="stop (same day)")
            moves.append(f"SELL {sym} the same day at its stop: {_rs(out['pnl_rs'])}")
        else:
            h["high_close"] = max(entry, bar["close"])
            h["stop"] = round(max(h["stop"], h["high_close"] - ATR_X * atr), 2)
    meta["recent"] = {s: d for s, d in {**meta.get("recent", {}), **{s: today for s in recent}}.items()
                      if _trading_days_between(d, today) < 5}
    pools.save(p)

    import swing_shadow

    snap = swing_shadow.summary(p, bench)
    snap.update(date=today, moves=moves, pool=pool)
    storage.put_json(f"shadow/{folder}/{today}.json", snap)
    return snap


def summary(pool=POOL):
    import swing_shadow

    return {**swing_shadow.summary(pools.load(pool)), "pool": pool}


def lines(s):
    label = VARIANTS[s.get("pool") or POOL][0]
    if not s.get("start_date"):
        return [f"Swing ({label}): starts with the next NSE day's ranks; nothing yet."]
    return [f"Swing ({label}) since {s['start_date']}: {_rs(s['equity'] - config.SWING_CAPITAL)} "
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
    except Exception as err:  # noqa: BLE001 - exits still run without new ranks
        log.warning("momentum ranks unavailable: %s", err)
        signals = []
    import pool_auto

    out = {}
    for pool, (label, *_rest) in VARIANTS.items():
        if pool == REAL_POOL and not pool_auto.on(REAL_POOL):
            continue
        snap = daily(today, signals, pool)
        out[pool] = snap
        if send and snap["moves"]:
            head = (f"[MOCK] Swing pool, auto mode ({label.split(' (')[0]} rules; dummy money):" if pool == REAL_POOL
                    else f"[MOCK] Swing shadow, {label} (nothing real is bought):")
            tg.send("\n".join([head, *[f"- {m}" for m in snap["moves"]], *lines(snap)]))
    return out


def all_lines():
    """One line per momentum shadow, for the reports (the real pool is in the pools section)."""
    return [x for pool in VARIANTS if pool != REAL_POOL for x in lines(summary(pool))]
