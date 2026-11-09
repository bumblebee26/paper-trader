"""Swing-pool backtest on NSE daily prices (pure Python, no pandas).

Rules (system plan, phase 3), checked on each day's close, bought at the next open:
  pullback  close above its 50-day average, 200-day average rising (vs 20 days ago), the last two
            days' low touched the 20-day average (within 1%), today closed above it and up on the day
  breakout  close above the highest close of the previous 55 days on >= 1.5x average volume,
            and above the 50-day average
  rs filter (variant) the stock beat NIFTYBEES over the last 63 trading days
Entry: next day's open, skipped if it gaps more than 2% above the signal close.
Exits (all automatic, as decided): close below the stop (2x average daily range under entry),
hard intraday stop at twice that distance, target at 2.5x the risk, trailing exit once a close has
been 1x risk in profit and a later close falls below the 20-day average, time stop after 20 days.
Pool: Rs 60,000, risk 1% of equity per trade, max 25% in one stock, max 5 holdings.
Costs: 0.12% a side + Rs 16 per sell (depository) + 0.05% slippage each way.

Prices are adjusted for splits and bonuses by chaining close / previous close (NSE's previous
close is adjusted on the ex-date). Known limits: no sector cap (the lakehouse has no sector map),
no results-date setup (news history starts Nov 2026), universe = the 250 most traded stocks in the
first 60 sessions (avoids hindsight; still includes stocks that later fell out of favour).
"""
import math
from collections import defaultdict

DEFAULTS = {
    "capital": 60_000.0, "risk_pct": 1.0, "max_position_pct": 25.0, "max_holdings": 5,
    "cost_pct": 0.12, "dp_charge": 16.0, "slippage_pct": 0.05, "gap_skip_pct": 2.0,
    "atr_mult": 2.0, "target_r": 2.5, "trail_after_r": 1.0, "max_days": 20,
    "setups": ("pullback", "breakout"), "rs_filter": False,
    "market_filter": False,  # only buy while NIFTYBEES closes above its 200-day average
    "start": None, "end": None,  # simulate only these dates (ISO strings); indicators still use all history
}


def adjust(rows):
    """rows: [(date, o, h, l, c, prev_close, volume)] sorted by date -> adjusted bars (dicts)."""
    out, adj_prev, raw_prev = [], None, None
    for d, o, h, low, c, pc, v in rows:
        if not c or c <= 0:
            continue
        if adj_prev is None:
            adj_c = c
        else:
            base = pc if pc and pc > 0 else raw_prev
            ret = c / base if base else 1.0
            if ret < 0.2 or ret > 5:  # bad print, not a corporate action
                ret = c / raw_prev
            adj_c = adj_prev * ret
        f = adj_c / c
        out.append({"date": d, "open": (o or c) * f, "high": (h or c) * f, "low": (low or c) * f,
                    "close": adj_c, "volume": v or 0.0})
        adj_prev, raw_prev = adj_c, c
    return out


def indicators(bars):
    """Adds sma20/50/200, sma200_20ago, avgvol20, atr14, hi55 (prior 55 closes), ret63 per bar."""
    closes = [b["close"] for b in bars]
    vols = [b["volume"] for b in bars]
    trs = []
    for i, b in enumerate(bars):
        pc = bars[i - 1]["close"] if i else b["close"]
        trs.append(max(b["high"] - b["low"], abs(b["high"] - pc), abs(b["low"] - pc)))

    def sma(xs, n, i):
        return sum(xs[i - n + 1:i + 1]) / n if i >= n - 1 else None

    for i, b in enumerate(bars):
        b["sma20"], b["sma50"], b["sma200"] = sma(closes, 20, i), sma(closes, 50, i), sma(closes, 200, i)
        b["sma200_20ago"] = bars[i - 20]["sma200"] if i >= 20 else None
        b["avgvol20"] = sma(vols, 20, i - 1) if i >= 20 else None  # previous 20 days
        b["atr14"] = sma(trs, 14, i)
        b["hi55"] = max(closes[i - 55:i]) if i >= 55 else None
        b["ret63"] = closes[i] / closes[i - 63] - 1 if i >= 63 else None
    return bars


def signals(bars, i, bench_ret63, cfg):
    """Setups that fire on bar i's close."""
    b = bars[i]
    if None in (b["sma20"], b["sma50"], b["sma200"], b["sma200_20ago"], b["avgvol20"], b["atr14"], b["hi55"]):
        return []
    if cfg["rs_filter"] and (b["ret63"] is None or bench_ret63 is None or b["ret63"] <= bench_ret63):
        return []
    out = []
    p1, p2 = bars[i - 1], bars[i - 2]
    if ("pullback" in cfg["setups"] and b["close"] > b["sma50"] and b["sma200"] > b["sma200_20ago"]
            and min(p1["low"], p2["low"]) <= b["sma20"] * 1.01 and b["close"] > b["sma20"]
            and b["close"] > p1["close"]):
        out.append(("pullback", b["ret63"] or 0.0))
    if ("breakout" in cfg["setups"] and b["close"] > b["hi55"] and b["avgvol20"] > 0
            and b["volume"] >= 1.5 * b["avgvol20"] and b["close"] > b["sma50"]):
        out.append(("breakout", b["volume"] / b["avgvol20"]))
    return out


def run(data, bench="NIFTYBEES", **overrides):
    """data: {symbol: [(date, o, h, l, c, prev_close, volume)]}. Returns trades, equity curve, summary."""
    cfg = {**DEFAULTS, **overrides}
    series = {s: indicators(adjust(rows)) for s, rows in data.items()}
    series = {s: b for s, b in series.items() if len(b) > 60}
    idx = {s: {b["date"]: i for i, b in enumerate(bars)} for s, bars in series.items()}
    dates = sorted({b["date"] for bars in series.values() for b in bars})
    # Start once the 200-day average exists, so the strategy and NIFTYBEES cover the same days.
    warmup = cfg.get("warmup_days", 210)
    dates = dates[warmup:] if len(dates) > warmup + 20 else dates
    if cfg["start"]:
        dates = [d for d in dates if str(d) >= cfg["start"]]
    if cfg["end"]:
        dates = [d for d in dates if str(d) <= cfg["end"]]
    bench_bars = series.get(bench)
    slip = cfg["slippage_pct"] / 100

    cash, holdings, trades, curve = cfg["capital"], {}, [], []
    pending = []  # (symbol, setup, signal_close, atr) to buy at the next open

    def sell(sym, price, reason, d):
        nonlocal cash
        h = holdings.pop(sym)
        fill = price * (1 - slip)
        value = h["qty"] * fill
        fees = value * cfg["cost_pct"] / 100 + cfg["dp_charge"]
        cash += value - fees
        pnl = value - fees - h["cost"]
        trades.append({"symbol": sym, "setup": h["setup"], "entry_date": h["entry_date"], "exit_date": d,
                       "entry": round(h["entry"], 2), "exit": round(fill, 2), "qty": h["qty"],
                       "pnl": round(pnl, 2), "r": round(pnl / h["risk_rs"], 2) if h["risk_rs"] else 0.0,
                       "days": h["days"], "reason": reason})

    for d in dates:
        # 1) buy yesterday's signals at today's open
        for sym, setup, sig_close, atr in pending:
            i = idx[sym].get(d)
            if i is None or sym in holdings or len(holdings) >= cfg["max_holdings"]:
                continue
            b = series[sym][i]
            if b["open"] > sig_close * (1 + cfg["gap_skip_pct"] / 100):
                continue
            entry = b["open"] * (1 + slip)
            risk_per_share = cfg["atr_mult"] * atr
            risk_per_share = min(max(risk_per_share, entry * 0.02), entry * 0.10)
            equity = cash + sum(h["qty"] * series[s][idx[s][h["last_date"]]]["close"] for s, h in holdings.items())
            qty = math.floor(min(equity * cfg["risk_pct"] / 100 / risk_per_share,
                                 equity * cfg["max_position_pct"] / 100 / entry))
            fees = qty * entry * cfg["cost_pct"] / 100
            while qty > 0 and qty * entry + fees > cash:
                qty -= 1
                fees = qty * entry * cfg["cost_pct"] / 100
            if qty <= 0:
                continue
            cash -= qty * entry + fees
            holdings[sym] = {"setup": setup, "entry": entry, "qty": qty, "cost": qty * entry + fees,
                             "stop": entry - risk_per_share, "hard": entry - 2 * risk_per_share,
                             "target": entry + cfg["target_r"] * risk_per_share, "risk_rs": qty * risk_per_share,
                             "entry_date": d, "days": 0, "best_close": entry, "last_date": d}
        pending = []

        # 2) manage holdings on today's bar
        for sym in list(holdings):
            i = idx[sym].get(d)
            if i is None:
                continue
            h, b = holdings[sym], series[sym][i]
            h["days"] += 1
            h["last_date"] = d
            if b["low"] <= h["hard"]:
                sell(sym, min(b["open"], h["hard"]), "hard_stop", d)
            elif b["high"] >= h["target"]:
                sell(sym, max(b["open"], h["target"]), "target", d)
            elif b["close"] < h["stop"]:
                sell(sym, b["close"], "stop_close", d)
            elif (h["best_close"] >= h["entry"] + cfg["trail_after_r"] * (h["entry"] - h["stop"])
                  and b["sma20"] and b["close"] < b["sma20"]):
                sell(sym, b["close"], "trailing", d)
            elif h["days"] >= cfg["max_days"]:
                sell(sym, b["close"], "time", d)
            else:
                h["best_close"] = max(h["best_close"], b["close"])

        # 3) today's signals, best first
        bench_ret = None
        if bench_bars and d in idx[bench]:
            bench_ret = bench_bars[idx[bench][d]]["ret63"]
        cands = []
        market_ok = True
        if cfg["market_filter"]:
            bb = bench_bars[idx[bench][d]] if bench_bars and d in idx[bench] else None
            market_ok = bool(bb and bb["sma200"] and bb["close"] > bb["sma200"])
        for sym, bars in (series.items() if market_ok else ()):
            if sym == bench or sym in holdings:
                continue
            i = idx[sym].get(d)
            if i is None or i < 2:
                continue
            for setup, score in signals(bars, i, bench_ret, cfg):
                cands.append((score, sym, setup, bars[i]["close"], bars[i]["atr14"]))
        cands.sort(reverse=True)
        seen = set()
        for _, sym, setup, close, atr in cands:
            if sym not in seen:
                seen.add(sym)
                pending.append((sym, setup, close, atr))

        equity = cash + sum(h["qty"] * series[s][idx[s][h["last_date"]]]["close"] for s, h in holdings.items())
        curve.append((d, round(equity, 2)))

    # close what's left at the last price (marked, not sold)
    return {"config": {k: v for k, v in cfg.items() if k != "setups"} | {"setups": list(cfg["setups"])},
            "trades": trades, "curve": curve, "open_at_end": len(holdings),
            "summary": summarize(trades, curve, cfg["capital"], bench_bars)}


def max_drawdown(values):
    peak, worst = -math.inf, 0.0
    for v in values:
        peak = max(peak, v)
        worst = max(worst, (peak - v) / peak if peak > 0 else 0.0)
    return round(worst * 100, 2)


def summarize(trades, curve, capital, bench_bars=None):
    if not curve:
        return {}
    start, end = curve[0][0], curve[-1][0]
    final = curve[-1][1]
    wins = [t for t in trades if t["pnl"] > 0]
    by_setup, by_reason, by_month = defaultdict(list), defaultdict(int), defaultdict(float)
    for t in trades:
        by_setup[t["setup"]].append(t)
        by_reason[t["reason"]] += 1
        by_month[str(t["exit_date"])[:7]] += t["pnl"]
    out = {
        "start": str(start), "end": str(end), "final_equity": final,
        "return_pct": round((final / capital - 1) * 100, 2),
        "max_drawdown_pct": max_drawdown([v for _, v in curve]),
        "trades": len(trades), "win_rate_pct": round(len(wins) / len(trades) * 100, 1) if trades else None,
        "avg_r": round(sum(t["r"] for t in trades) / len(trades), 2) if trades else None,
        "avg_days": round(sum(t["days"] for t in trades) / len(trades), 1) if trades else None,
        "fees_note": "P&L is after costs",
        "by_setup": {s: {"trades": len(ts), "win_rate_pct": round(sum(t["pnl"] > 0 for t in ts) / len(ts) * 100, 1),
                         "pnl": round(sum(t["pnl"] for t in ts), 2),
                         "avg_r": round(sum(t["r"] for t in ts) / len(ts), 2)} for s, ts in by_setup.items()},
        "exits": dict(by_reason),
        "by_month": {m: round(v, 2) for m, v in sorted(by_month.items())},
        "losing_months": sum(1 for v in by_month.values() if v < 0), "months": len(by_month),
    }
    if bench_bars:
        b = {x["date"]: x["close"] for x in bench_bars}
        if start in b and end in b:
            out["benchmark_return_pct"] = round((b[end] / b[start] - 1) * 100, 2)
            path = [b[d] for d, _ in curve if d in b]
            out["benchmark_max_drawdown_pct"] = max_drawdown(path)
    return out
