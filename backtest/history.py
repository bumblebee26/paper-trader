"""Phase 3 step 4 (24 Nov 2026): swing ideas on the lakehouse's NSE history, 2019-2026 (pure Python, no pandas).

Every idea is checked on each day's close and bought at the next open, with the same pool, costs and judging:
  momentum   the live swing auto rules, price-only (news history starts Nov 2026): swing_eligible = close above
             the 50-day average above the 200-day, 1-month return above 0, RSI under 80; ranked by swing_score =
             40 x percentile of the 1-month return + 30 x the 3-month one + 15 within 5% of the 52-week high (8
             within 10%) + 10 for a strict scanner pick. Top first, 6 holdings, 2% risk, at most 1/6 of the pool
             in one stock; stop 2 x ATR under the entry, trailing 2 x ATR under the highest close; out after 20 days.
  scanner    the six-filter strict pick: above the 20/50/200-day averages, RSI 50-65, MACD above its signal, ADX
             over 20 and rising, volume at least 1.5x its 20-day average (the scanner's R:R check needs its swing
             levels and is left out). Fixed exits.
  high52     a fresh 52-week-high close (above the previous 252 closes, the day before wasn't) above the 50-day
             average on at least 1.5x volume (the friend's suggestion B2, one objective rule). Fixed exits.
  dbl_bottom a double bottom's neckline broken today (trader/patterns.py rules, daily). Fixed exits.
  bull_flag  a close above a bull flag's edge (trader/patterns.py rules, daily). Fixed exits.
  rally_range the friend's B5 idea on daily bars: a rally of 5%+ within 5 days, then 3-10 days inside a range under
             3% of the price, then a close above the range on 1.5x volume. (The 10-20 minute version needs minute
             history the lakehouse doesn't have.) Fixed exits.
Fixed exits: stop 2 x ATR under the entry (2-10% of the price), target 2R, out after 20 days; 1% risk, 5 holdings,
at most 25% in one stock.
Pool Rs 60,000; delivery costs 0.12% a side + Rs 16 a sell + 0.05% slippage each way (as the swing backtest).
Judged on 2020-2024 (2019 warms up the 200-day and 252-day figures) and once on 2025-2026, which no rule was tuned
on. Verdict per period: "beats NIFTYBEES" only if it made more than holding NIFTYBEES over the same days AND the
lower end of its average R's 95% range (bootstrap over whole months) is above 0; "could be luck" if its average R
is above 0 but that lower end isn't; otherwise "no". Universe: the 250 most traded stocks of the first 60 sessions
of 2019 (no hindsight about which stocks did well later).
"""
import math
import os
import random
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "trader"), HERE]

import patterns  # noqa: E402
import swing  # noqa: E402

CAPITAL = 60_000.0
COST_PCT, DP, SLIP = 0.12, 16.0, 0.05
PERIODS = {"2020-2024": ("2020-01-01", "2024-12-31"), "2025-2026 (held back)": ("2025-01-01", "2026-12-31")}
FIXED = {"risk_pct": 1.0, "max_pos_pct": 25.0, "holdings": 5, "exit": "fixed"}
MOMENTUM = {"risk_pct": 2.0, "max_pos_pct": 100 / 6, "holdings": 6, "exit": "trail"}
NAMES = {"momentum": "Momentum (live swing auto rules, price only)", "scanner": "Scanner strict picks",
         "high52": "52-week-high breakout", "dbl_bottom": "Double bottom breakout",
         "bull_flag": "Bull flag breakout", "rally_range": "Rally, range, breakout (daily)"}


# ------------------------------------------------------------------ indicators --
def more_indicators(bars):
    """Adds rsi14, macd/macd_sig, adx14 (+ 5 days ago), hi252 (previous 252 closes), high52w, ret21."""
    closes = [b["close"] for b in bars]
    n = len(bars)
    # RSI(14), Wilder
    g = lo = None
    for i in range(1, n):
        ch = closes[i] - closes[i - 1]
        up, dn = max(ch, 0), max(-ch, 0)
        if i < 14:
            g, lo = (g or 0) + up / 14, (lo or 0) + dn / 14
            bars[i]["rsi14"] = None
            continue
        g, lo = (g * 13 + up) / 14, (lo * 13 + dn) / 14
        bars[i]["rsi14"] = 100.0 if lo == 0 else 100 - 100 / (1 + g / lo)
    bars[0]["rsi14"] = None
    # MACD 12/26/9
    e12 = e26 = sig = None
    for i, c in enumerate(closes):
        e12 = c if e12 is None else e12 + 2 / 13 * (c - e12)
        e26 = c if e26 is None else e26 + 2 / 27 * (c - e26)
        m = e12 - e26
        sig = m if sig is None else sig + 2 / 10 * (m - sig)
        bars[i]["macd"], bars[i]["macd_sig"] = (m, sig) if i >= 35 else (None, None)
    # ADX(14), Wilder
    tr_s = pdm_s = ndm_s = adx = None
    dxs = []
    for i in range(n):
        bars[i]["adx14"] = None
        if i == 0:
            continue
        b, p = bars[i], bars[i - 1]
        tr = max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"]))
        upm, dnm = b["high"] - p["high"], p["low"] - b["low"]
        pdm = upm if upm > dnm and upm > 0 else 0.0
        ndm = dnm if dnm > upm and dnm > 0 else 0.0
        if tr_s is None:
            tr_s, pdm_s, ndm_s = tr, pdm, ndm
        else:
            tr_s, pdm_s, ndm_s = tr_s - tr_s / 14 + tr, pdm_s - pdm_s / 14 + pdm, ndm_s - ndm_s / 14 + ndm
        if i < 14 or tr_s <= 0:
            continue
        pdi, ndi = 100 * pdm_s / tr_s, 100 * ndm_s / tr_s
        dx = 100 * abs(pdi - ndi) / (pdi + ndi) if pdi + ndi else 0.0
        dxs.append(dx)
        if len(dxs) == 14:
            adx = sum(dxs) / 14
        elif len(dxs) > 14:
            adx = (adx * 13 + dx) / 14
        bars[i]["adx14"] = adx
    highs = [b["high"] for b in bars]
    for i, b in enumerate(bars):
        b["adx14_5ago"] = bars[i - 5]["adx14"] if i >= 5 else None
        b["hi252"] = max(closes[i - 252:i]) if i >= 252 else None
        b["high52w"] = max(highs[max(0, i - 251):i + 1])
        b["ret21"] = closes[i] / closes[i - 21] - 1 if i >= 21 else None
    return bars


def prepare(data):
    out = {}
    for s, rows in data.items():
        bars = swing.indicators(swing.adjust(rows))
        if len(bars) > 260:
            out[s] = more_indicators(bars)
    return out


# --------------------------------------------------------------------- signals --
def strict_scan(b):
    need = ("sma20", "sma50", "sma200", "rsi14", "macd", "macd_sig", "adx14", "adx14_5ago", "avgvol20")
    if any(b.get(k) is None for k in need) or not b["avgvol20"]:
        return False
    return (b["close"] > b["sma20"] and b["close"] > b["sma50"] and b["close"] > b["sma200"]
            and 50 <= b["rsi14"] <= 65 and b["macd"] > b["macd_sig"] and b["adx14"] > 20
            and b["adx14"] > b["adx14_5ago"] and b["volume"] >= 1.5 * b["avgvol20"])


def high52(bars, i):
    b, p = bars[i], bars[i - 1]
    return (b["hi252"] is not None and p["hi252"] is not None and b["sma50"] and b["avgvol20"]
            and b["close"] > b["hi252"] and p["close"] <= p["hi252"] and b["close"] > b["sma50"]
            and b["volume"] >= 1.5 * b["avgvol20"])


def dbl_bottom(bars, i):
    if i < 40 or bars[i]["close"] <= bars[i - 1]["close"]:
        return False
    p = patterns._double(bars[max(0, i - patterns.LOOKBACK):i], "daily", True)   # the pattern up to yesterday
    return bool(p and p["status"] == "forming" and bars[i]["close"] > p["level"] >= bars[i - 1]["close"])


def bull_flag(bars, i):
    if i < 30:
        return False
    p = patterns._flag(bars[:i], True)
    return bool(p and bars[i]["close"] > p["level"] and bars[i]["avgvol20"]
                and bars[i]["volume"] >= 1.2 * bars[i]["avgvol20"])


def rally_range(bars, i):
    b = bars[i]
    if i < 20 or not b["avgvol20"] or b["volume"] < 1.5 * b["avgvol20"]:
        return False
    for days in range(3, 11):                      # the range = the `days` bars before today
        rng = bars[i - days:i]
        top, bottom = max(x["high"] for x in rng), min(x["low"] for x in rng)
        peak = bars[i - days - 1]                  # the rally's last bar
        start = min(x["low"] for x in bars[i - days - 6:i - days - 1])
        if (peak["close"] / start - 1 >= 0.05 and (top - bottom) / peak["close"] <= 0.03
                and b["close"] > top and bars[i - 1]["close"] <= top):
            return True
    return False


SIGNALS = {"scanner": lambda bars, i: strict_scan(bars[i]), "high52": high52, "dbl_bottom": dbl_bottom,
           "bull_flag": bull_flag, "rally_range": rally_range}


def momentum_ranks(series, idx, d):
    """[(score, symbol)] best first, for the swing_eligible stocks on day d."""
    rows = []
    for s, bars in series.items():
        i = idx[s].get(d)
        if i is None:
            continue
        b = bars[i]
        if None in (b["sma50"], b["sma200"], b["ret21"], b["ret63"], b["rsi14"], b["atr14"]):
            continue
        rows.append((s, b))
    if len(rows) < 20:
        return []

    def pct(key):
        order = sorted(rows, key=lambda r: r[1][key])
        return {s: k / (len(order) - 1) for k, (s, _) in enumerate(order)}
    p1, p3 = pct("ret21"), pct("ret63")
    out = []
    for s, b in rows:
        if not (b["close"] > b["sma50"] > b["sma200"] and b["ret21"] > 0 and b["rsi14"] < 80):
            continue
        near = b["close"] / b["high52w"] * 100
        score = (40 * p1[s] + 30 * p3[s] + (15 if near >= 95 else 8 if near >= 90 else 0)
                 + (10 if strict_scan(b) else 0))
        out.append((score, s))
    out.sort(reverse=True)
    return out


# ---------------------------------------------------------------------- engine --
def run(series, strategy, start, end, bench="NIFTYBEES"):
    cfg = MOMENTUM if strategy == "momentum" else FIXED
    idx = {s: {b["date"]: i for i, b in enumerate(bars)} for s, bars in series.items()}
    dates = sorted({b["date"] for bars in series.values() for b in bars if start <= str(b["date"]) <= end})
    slip = SLIP / 100
    cash, held, trades, curve, pending = CAPITAL, {}, [], [], []

    def value():
        return cash + sum(h["qty"] * series[s][idx[s][h["last"]]]["close"] for s, h in held.items())

    def sell(s, price, why, d):
        nonlocal cash
        h = held.pop(s)
        fill = price * (1 - slip)
        fees = h["qty"] * fill * COST_PCT / 100 + DP
        cash += h["qty"] * fill - fees
        pnl = h["qty"] * fill - fees - h["cost"]
        trades.append({"symbol": s, "entry_date": h["date"], "exit_date": d, "pnl": round(pnl, 2),
                       "r": round(pnl / h["risk_rs"], 3) if h["risk_rs"] else 0.0, "days": h["days"], "why": why})

    for d in dates:
        for s, sig_close, atr in pending:                         # 1) yesterday's signals at today's open
            i = idx[s].get(d)
            if i is None or s in held or len(held) >= cfg["holdings"]:
                continue
            b = series[s][i]
            if cfg["exit"] == "fixed" and b["open"] > sig_close * 1.02:   # gapped 2%+ above the signal: skip
                continue
            entry = b["open"] * (1 + slip)
            rps = min(max(2 * atr, entry * 0.02), entry * 0.10)
            eq = value()
            qty = math.floor(min(eq * cfg["risk_pct"] / 100 / rps, eq * cfg["max_pos_pct"] / 100 / entry))
            while qty > 0 and qty * entry * (1 + COST_PCT / 100) > cash:
                qty -= 1
            if qty <= 0:
                continue
            cost = qty * entry * (1 + COST_PCT / 100)
            cash -= cost
            held[s] = {"qty": qty, "cost": cost, "entry": entry, "stop": entry - rps, "rps": rps,
                       "target": entry + 2 * rps, "risk_rs": qty * rps, "date": d, "last": d, "days": 0,
                       "best": entry}
        pending = []
        for s in list(held):                                      # 2) exits on today's bar
            i = idx[s].get(d)
            if i is None:
                continue
            h, b = held[s], series[s][i]
            h["days"] += 1
            h["last"] = d
            if b["low"] <= h["stop"]:
                sell(s, min(b["open"], h["stop"]), "stop", d)
            elif cfg["exit"] == "fixed" and b["high"] >= h["target"]:
                sell(s, max(b["open"], h["target"]), "target", d)
            elif h["days"] >= 20:
                sell(s, b["close"], "20 days", d)
            elif cfg["exit"] == "trail":
                h["best"] = max(h["best"], b["close"])
                h["stop"] = max(h["stop"], h["best"] - h["rps"])
        if strategy == "momentum":                                # 3) today's signals, best first
            cands = [s for _, s in momentum_ranks(series, idx, d) if s != bench and s not in held]
            cands = cands[:cfg["holdings"]]
        else:
            fn, scored = SIGNALS[strategy], []
            for s, bars in series.items():
                i = idx[s].get(d)
                if s == bench or s in held or i is None or i < 2 or bars[i]["atr14"] is None:
                    continue
                if fn(bars, i):
                    scored.append((bars[i]["ret63"] or 0.0, s))
            cands = [s for _, s in sorted(scored, reverse=True)]
        pending = [(s, series[s][idx[s][d]]["close"], series[s][idx[s][d]]["atr14"]) for s in cands]
        curve.append((d, value()))
    return trades, curve


# --------------------------------------------------------------------- judging --
def luck_low(trades, seed=7, n=2000):
    """Lower end of the 95% range of the average R, resampling whole months of trades."""
    months = defaultdict(list)
    for t in trades:
        months[str(t["exit_date"])[:7]].append(t["r"])
    blocks = list(months.values())
    if len(blocks) < 3:
        return None
    rnd = random.Random(seed)
    avgs = []
    for _ in range(n):
        pick = [r for _ in blocks for r in rnd.choice(blocks)]
        avgs.append(sum(pick) / len(pick) if pick else 0.0)
    avgs.sort()
    return round(avgs[int(0.025 * n)], 3)


def judge(trades, curve, series, bench="NIFTYBEES"):
    if not curve:
        return {}
    final = curve[-1][1]
    bb = {b["date"]: b["close"] for b in series.get(bench, [])}
    days = [d for d, _ in curve if d in bb]
    bench_ret = round((bb[days[-1]] / bb[days[0]] - 1) * 100, 1) if len(days) > 1 else None
    avg_r = round(sum(t["r"] for t in trades) / len(trades), 3) if trades else None
    low = luck_low(trades)
    ret = round((final / CAPITAL - 1) * 100, 1)
    if avg_r is not None and avg_r > 0 and low is not None and low > 0 and bench_ret is not None and ret > bench_ret:
        verdict = "beats NIFTYBEES"
    elif avg_r is not None and avg_r > 0:
        verdict = "could be luck"
    else:
        verdict = "no"
    if len(trades) < 30:
        verdict += " (under 30 trades: too few)"
    return {"start": str(curve[0][0]), "end": str(curve[-1][0]), "return_pct": ret, "niftybees_pct": bench_ret,
            "max_drawdown_pct": swing.max_drawdown([v for _, v in curve]), "trades": len(trades),
            "win_rate_pct": round(sum(t["pnl"] > 0 for t in trades) / len(trades) * 100, 1) if trades else None,
            "avg_r": avg_r, "avg_r_low95": low, "avg_days": round(sum(t["days"] for t in trades) / len(trades), 1)
            if trades else None, "verdict": verdict}


def run_all(data, strategies=None):
    series = prepare(data)
    out = {}
    for name in strategies or NAMES:
        out[name] = {}
        for label, (a, b) in PERIODS.items():
            trades, curve = run(series, name, a, b)
            out[name][label] = judge(trades, curve, series)
    return out
