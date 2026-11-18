"""Shadow models for the intraday pool: the data they learn from and the trades they would have made.

Pure Python (no network, no ML library), so it is unit-tested directly. models/walkforward.py trains and scores;
jobs/model_shadow.py fetches data, runs both and reports.

Samples come from the trader's own setups (trader/scanner.py rules, replayed bar by bar on 5-minute candles for
every stock in the tradable universe): ORB long/short, VWAP reclaim/rejection, PDH/PDL break, volume >= 1.2x the
previous 20 candles, entry at the signal candle's close between ENTRY_START and ENTRY_END, the scanner's stop, a
2R target, the 15:10 square-off, the engine's costs and slippage, 1R = ONE_R_RS. Stop first when a candle touches
both (conservative). A signal whose costs would exceed the engine's MAX_COST_SHARE_PCT of its risk is dropped,
as the risk gate would block it.

Every feature is computed from candles that had closed at the decision time: nothing from later in the day.
"""
import math

import config
import exits
import fees
import stretch

ONE_R_RS = 400.0                 # ~1% of the intraday pool; results are reported in R, so the size cancels out
BALANCE_RS = 40000.0
SETUPS = ("ORB long", "ORB short", "VWAP reclaim", "VWAP rejection", "PDH break", "PDL break")
VOL_HORIZON = 12                 # 5-minute candles = the next 60 minutes, for the volatility model


def _hhmm(b):
    return b["time"][11:16]


def by_day(bars):
    days = {}
    for b in bars:
        days.setdefault(b["time"][:10], []).append(b)
    return days


def _vwap(bars):
    out, pv, vol = [], 0.0, 0.0
    for b in bars:
        pv += (b["high"] + b["low"] + b["close"]) / 3 * b["volume"]
        vol += b["volume"]
        out.append(pv / vol if vol else None)
    return out


def _pct(a, b):
    return (a / b - 1) * 100 if b else 0.0


# ------------------------------------------------------------------ signals --
def signals(bars, nifty=None):
    """Every scanner setup in `bars` (5-minute candles, several days, oldest first), with its features.
    nifty: ^NSEI 5-minute candles over the same days (optional; market features are 0 without it)."""
    out = []
    days = by_day(bars)
    order = sorted(days)
    nifty_days = by_day(nifty or [])
    flat = []   # (day, index in day) for the previous-20-candles volume average across days
    for d in order:
        flat += [(d, i) for i in range(len(days[d]))]
    pos = {k: n for n, k in enumerate(flat)}
    all_bars = [days[d][i] for d, i in flat]
    for di, d in enumerate(order):
        if di == 0:
            continue
        t, prev = days[d], days[order[di - 1]]
        pdh, pdl, prev_close = max(b["high"] for b in prev), min(b["low"] for b in prev), prev[-1]["close"]
        prev_ranges = [_pct(max(b["high"] for b in days[x]), min(b["low"] for b in days[x]))
                       for x in order[max(0, di - 5):di]]
        vw = _vwap(t)
        orb = [b for b in t if _hhmm(b) < "09:30"]
        or_hi = max(b["high"] for b in orb) if len(orb) >= 3 else None
        or_lo = min(b["low"] for b in orb) if len(orb) >= 3 else None
        nd = nifty_days.get(d, [])
        fired_today = 0
        for i in range(1, len(t)):
            cur, before = t[i], t[i - 1]
            hm = _hhmm(cur)
            if hm < config.ENTRY_START or hm >= config.ENTRY_END:
                continue
            k = pos[(d, i)]
            vols = [b["volume"] for b in all_bars[max(0, k - 20):k]]
            avg = sum(vols) / len(vols) if vols else 0
            vol_x = cur["volume"] / avg if avg else None
            volume_ok = vol_x is not None and vol_x >= 1.2
            above, below = vw[i] is not None and cur["close"] > vw[i], vw[i] is not None and cur["close"] < vw[i]
            found = []
            if or_hi is not None and hm >= "09:30":
                if before["close"] <= or_hi < cur["close"] and volume_ok and above:
                    found.append(("ORB long", "BUY", max(or_lo, cur["low"])))
                if before["close"] >= or_lo > cur["close"] and volume_ok and below:
                    found.append(("ORB short", "SELL", min(or_hi, cur["high"])))
            if vw[i] is not None and vw[i - 1] is not None:
                if before["close"] < vw[i - 1] and cur["close"] > vw[i]:
                    found.append(("VWAP reclaim", "BUY", cur["low"]))
                if before["close"] > vw[i - 1] and cur["close"] < vw[i]:
                    found.append(("VWAP rejection", "SELL", cur["high"]))
            if volume_ok:
                if before["close"] <= pdh < cur["close"]:
                    found.append(("PDH break", "BUY", cur["low"]))
                if before["close"] >= pdl > cur["close"]:
                    found.append(("PDL break", "SELL", cur["high"]))
            for setup, side, stop in found:
                entry = cur["close"]
                per_share = abs(entry - stop)
                if per_share <= 0:
                    continue
                feats = features(t, i, vw, side, entry, per_share, setup, prev_close, prev_ranges, or_hi, or_lo,
                                 vol_x, nd, fired_today)
                out.append({"date": d, "time": hm, "setup": setup, "side": side, "entry": entry, "stop": stop,
                            "target": entry + (2 if side == "BUY" else -2) * per_share, "i": i, "features": feats,
                            "pdh": pdh, "pdl": pdl,
                            "stretched": is_stretched(setup, side, t, i, vw[i], prev_ranges)})
                fired_today += 1
    return out


def is_stretched(setup, side, t, i, vwap, prev_ranges):
    """The background rule "stretched" (trader/stretch.py) at candle i's close: breakouts only."""
    if setup not in stretch.BREAKOUT_SETUPS:
        return False
    usual = sum(prev_ranges) / len(prev_ranges) if prev_ranges else None
    seen = t[:i + 1]
    return stretch.check(side, t[i]["close"], min(b["low"] for b in seen), max(b["high"] for b in seen), vwap,
                         usual)[0]


def bar_features(t, i, vw, prev_close, prev_ranges, or_hi, or_lo, nd):
    """Features of a stock at the close of candle i (side-free): used by every model."""
    cur = t[i]
    day_hi, day_lo = max(b["high"] for b in t[:i + 1]), min(b["low"] for b in t[:i + 1])
    rng6 = [(b["high"] - b["low"]) / b["close"] * 100 for b in t[max(0, i - 5):i + 1]]
    tr = [max(b["high"], t[j - 1]["close"]) - min(b["low"], t[j - 1]["close"]) for j, b in enumerate(t[:i + 1]) if j]
    atr5 = (sum(tr[-14:]) / len(tr[-14:]) / cur["close"] * 100) if tr else rng6[-1]
    hm = _hhmm(cur)
    n_now = [b for b in nd if _hhmm(b) <= hm]
    f = {
        "minute": (int(hm[:2]) - 9) * 60 + int(hm[3:5]) - 15,
        "gap_pct": _pct(t[0]["open"], prev_close),
        "day_ret_pct": _pct(cur["close"], t[0]["open"]),
        "day_range_pct": _pct(day_hi, day_lo),
        "pos_in_range": (cur["close"] - day_lo) / (day_hi - day_lo) if day_hi > day_lo else 0.5,
        "vwap_dist_pct": _pct(cur["close"], vw[i]) if vw[i] else 0.0,
        "ret_30m_pct": _pct(cur["close"], t[max(0, i - 6)]["close"]),
        "range6_pct": sum(rng6) / len(rng6),
        "atr5_pct": atr5,
        "prev_range_pct": prev_ranges[-1] if prev_ranges else 0.0,
        "avg5_range_pct": sum(prev_ranges) / len(prev_ranges) if prev_ranges else 0.0,
        "or_width_pct": _pct(or_hi, or_lo) if or_hi and or_lo else 0.0,
        "nifty_day_pct": _pct(n_now[-1]["close"], n_now[0]["open"]) if n_now else 0.0,
        "nifty_30m_pct": _pct(n_now[-1]["close"], n_now[max(0, len(n_now) - 7)]["close"]) if n_now else 0.0,
        "nifty_range_pct": _pct(max(b["high"] for b in n_now), min(b["low"] for b in n_now)) if n_now else 0.0,
    }
    return f


def features(t, i, vw, side, entry, per_share, setup, prev_close, prev_ranges, or_hi, or_lo, vol_x, nd, fired):
    f = bar_features(t, i, vw, prev_close, prev_ranges, or_hi, or_lo, nd)
    d = 1 if side == "BUY" else -1
    f.update({
        "is_buy": 1 if side == "BUY" else 0,
        "vol_x": vol_x or 0.0,
        "risk_pct": per_share / entry * 100,
        "risk_atr": (per_share / entry * 100) / f["atr5_pct"] if f["atr5_pct"] else 0.0,
        # side-aligned: positive = the move so far / the market agrees with the trade
        "day_ret_side": f["day_ret_pct"] * d, "ret_30m_side": f["ret_30m_pct"] * d,
        "nifty_day_side": f["nifty_day_pct"] * d, "nifty_30m_side": f["nifty_30m_pct"] * d,
        "vwap_side": f["vwap_dist_pct"] * d, "gap_side": f["gap_pct"] * d,
        "signals_before_today": fired,
    })
    for s in SETUPS:
        f["setup_" + s.replace(" ", "_")] = 1 if setup == s else 0
    return f


def feature_names(sig):
    return sorted(sig["features"])


# ------------------------------------------------------------- simulation --
def qty_for(entry, per_share):
    cap = BALANCE_RS * config.MAX_POSITION_MARGIN_PCT / 100 * config.INTRADAY_LEVERAGE / entry
    return max(0, math.floor(min(ONE_R_RS / per_share, cap)))


def blocked_by_costs(entry, per_share, side):
    q = qty_for(entry, per_share)
    if q < 1:
        return True
    costs = fees.round_trip(entry * q, entry * q, side) + 2 * entry * q * config.SLIPPAGE_PCT / 100
    return config.MAX_COST_SHARE_PCT > 0 and costs / (q * per_share) * 100 > config.MAX_COST_SHARE_PCT


def _fill(price, side):
    f = config.SLIPPAGE_PCT / 100
    return price * (1 + f) if side == "BUY" else price * (1 - f)


def rule_r(rule, sig, day_bars, stop=None, target=None, qty=None):
    """Net result in R (1R = ONE_R_RS) of the signal under an exits.py rule, on that day's candles."""
    stop = sig["stop"] if stop is None else stop
    target = sig["target"] if target is None else target
    per_share = abs(sig["entry"] - stop)
    qty = qty or qty_for(sig["entry"], per_share)
    if qty < 1:
        return None
    fill = _fill(sig["entry"], sig["side"])
    if (fill - stop) * (1 if sig["side"] == "BUY" else -1) <= 0:
        return None
    level = sig.get("pdh") if sig["side"] == "BUY" else sig.get("pdl")   # for level_target
    net = exits.replay(rule, sig["side"], qty, fill, stop, target, f"{sig['date']} {sig['time']}:00", day_bars,
                       level=level)
    return None if net is None else net / ONE_R_RS


def path(sig, day_bars):
    """The trade under the current rules, candle by candle: [(index, close R, high-water R, low-water R, done)].
    Used for early-cut checkpoints and the let-run decision."""
    d = 1 if sig["side"] == "BUY" else -1
    risk = abs(sig["entry"] - sig["stop"])
    out, mfe, mae = [], 0.0, 0.0
    for j in range(sig["i"] + 1, len(day_bars)):
        b = day_bars[j]
        if _hhmm(b) >= config.SQUARE_OFF:
            break
        adverse = (b["low"] if d == 1 else b["high"])
        fav = (b["high"] if d == 1 else b["low"])
        mae = min(mae, (adverse - sig["entry"]) * d / risk)
        mfe = max(mfe, (fav - sig["entry"]) * d / risk)
        hit_stop = (adverse - sig["stop"]) * d <= 0
        hit_tgt = (fav - sig["target"]) * d >= 0
        out.append((j, (b["close"] - sig["entry"]) * d / risk, mfe, mae, "stop" if hit_stop else
                    "target" if hit_tgt else None))
        if hit_stop or hit_tgt:
            break
    return out


def exit_at_close_r(sig, day_bars, j):
    """Net R if the trade (current size) were closed at candle j's close."""
    per_share = abs(sig["entry"] - sig["stop"])
    qty = qty_for(sig["entry"], per_share)
    fill = _fill(sig["entry"], sig["side"])
    d = 1 if sig["side"] == "BUY" else -1
    px = exits._exit_fill(day_bars[j]["close"], sig["side"])
    net = (px - fill) * d * qty - fees.order(fill * qty, sig["side"]) - fees.order(
        px * qty, "SELL" if sig["side"] == "BUY" else "BUY")
    return net / ONE_R_RS


def let_run_r(sig, day_bars, j):
    """Net R if, instead of booking the target at candle j, the trade keeps running: stop moved to +1R, then
    trailing 1R behind the best price, out at 15:10."""
    d = 1 if sig["side"] == "BUY" else -1
    risk = abs(sig["entry"] - sig["stop"])
    per_share = risk
    qty = qty_for(sig["entry"], per_share)
    fill = _fill(sig["entry"], sig["side"])
    stop = sig["entry"] + d * risk
    best = sig["target"]
    exit_px = None
    for b in day_bars[j + 1:]:
        if _hhmm(b) >= config.SQUARE_OFF:
            exit_px = b["open"]
            break
        adverse = b["low"] if d == 1 else b["high"]
        if (adverse - stop) * d <= 0:
            exit_px = min(b["open"], stop) if d == 1 else max(b["open"], stop)
            break
        fav = b["high"] if d == 1 else b["low"]
        best = max(best, fav) if d == 1 else min(best, fav)
        trail = best - d * risk
        if (trail - stop) * d > 0:
            stop = trail
    if exit_px is None:
        exit_px = day_bars[-1]["close"]
    px = exits._exit_fill(exit_px, sig["side"])
    net = (px - fill) * d * qty - fees.order(fill * qty, sig["side"]) - fees.order(
        px * qty, "SELL" if sig["side"] == "BUY" else "BUY")
    return net / ONE_R_RS


def checkpoint_features(sig, day_bars, j, close_r, mfe, mae, vw):
    """Features at candle j's close for the early-cut model (the trade is open and losing)."""
    d = 1 if sig["side"] == "BUY" else -1
    risk = abs(sig["entry"] - sig["stop"])
    k = j - sig["i"]
    last3 = [(day_bars[x]["close"] - day_bars[x - 1]["close"]) * d / risk for x in range(max(sig["i"] + 1, j - 2), j + 1)]
    vol_since = sum(b["volume"] for b in day_bars[sig["i"] + 1:j + 1]) / k
    vol_before = sum(b["volume"] for b in day_bars[max(0, sig["i"] - 11):sig["i"] + 1]) / min(12, sig["i"] + 1)
    f = {
        "open_r": close_r, "mfe_r": mfe, "mae_r": mae, "candles_held": k,
        "slope3_r": sum(last3) / len(last3), "vol_pace": vol_since / vol_before if vol_before else 1.0,
        "vwap_side": ((day_bars[j]["close"] / vw[j] - 1) * 100 * d) if vw[j] else 0.0,
        "minute": sig["features"]["minute"] + 5 * k, "is_buy": sig["features"]["is_buy"],
        "risk_atr": sig["features"]["risk_atr"], "nifty_day_side": sig["features"]["nifty_day_side"],
    }
    for s in SETUPS:
        f["setup_" + s.replace(" ", "_")] = sig["features"]["setup_" + s.replace(" ", "_")]
    return f


def label_signal(sig, day_bars):
    """Everything the models need about one signal: result under the current rules and the rule variants, the
    early-cut checkpoints (with what cutting would have given), the let-run point, and the forward range."""
    d = 1 if sig["side"] == "BUY" else -1
    res = {r: rule_r(r, sig, day_bars) for r in ("current", "no_stop", "wide_1_5", "wide_2", "close_stop",
                                                   "trailing", "half_and_run", "breakeven", "time_stop",
                                                   "target_1_5r", "level_target")}
    if res["current"] is None:
        return None
    vw = _vwap(day_bars)
    p = path(sig, day_bars)
    checkpoints, let_run = [], None
    for j, close_r, mfe, mae, done in p:
        if done == "target":
            let_run = {"features": {**{k: v for k, v in sig["features"].items()},
                                    "candles_to_target": j - sig["i"], "mae_r": mae},
                       "take_r": res["current"], "run_r": let_run_r(sig, day_bars, j)}
            break
        if done == "stop":
            break
        if close_r < 0:
            checkpoints.append({"features": checkpoint_features(sig, day_bars, j, close_r, mfe, mae, vw),
                                "cut_r": exit_at_close_r(sig, day_bars, j), "hold_r": res["current"], "j": j})
    nxt = day_bars[sig["i"] + 1:sig["i"] + 1 + VOL_HORIZON]
    fwd_range = _pct(max(b["high"] for b in nxt), min(b["low"] for b in nxt)) if nxt else None
    adverse = (max((sig["entry"] - b["low"]) * d if d == 1 else (b["high"] - sig["entry"]) for b in nxt)
               / sig["entry"] * 100) if nxt else None
    return {**sig, "results": res, "checkpoints": checkpoints, "let_run": let_run, "fwd_range_pct": fwd_range,
            "fwd_adverse_pct": adverse}


def vol_stop_r(sig, day_bars, pred_range_pct, k, same_target=True):
    """The volatility-model variant: stop k x the predicted 60-minute range from the entry (never tighter than the
    scanner's stop), shares cut so the rupee risk is the same; target the same price, or 2R of the new stop."""
    d = 1 if sig["side"] == "BUY" else -1
    base = abs(sig["entry"] - sig["stop"])
    per_share = max(base, sig["entry"] * k * pred_range_pct / 100)
    stop = sig["entry"] - d * per_share
    target = sig["target"] if same_target else sig["entry"] + d * 2 * per_share
    return rule_r("current", sig, day_bars, stop=stop, target=target, qty=qty_for(sig["entry"], per_share))


def dataset(bars_by_symbol, nifty):
    """All labelled signals across the universe: [{symbol, date, ..., results, checkpoints, let_run}]."""
    out = []
    for sym, bars in bars_by_symbol.items():
        days = by_day(bars)
        for sig in signals(bars, nifty):
            if blocked_by_costs(sig["entry"], abs(sig["entry"] - sig["stop"]), sig["side"]):
                continue
            lab = label_signal(sig, days[sig["date"]])
            if lab:
                lab["symbol"] = sym
                out.append(lab)
    return out


def signal_from_trade(bars, nifty, trade):
    """A real trade as a labelled signal, so the models can score it like the backtest's signals.
    trade: {date, time 'HH:MM' (fill time), setup, side, entry, stop, target}. The signal candle is the last 5-minute
    candle that had closed at the fill time. None if the candles aren't there."""
    days = by_day(bars)
    order = sorted(days)
    d = trade["date"]
    if d not in days or order.index(d) == 0:
        return None
    t = days[d]
    hh, mm = int(trade["time"][:2]), int(trade["time"][3:5])
    closed_before = f"{hh:02d}:{mm // 5 * 5:02d}"             # candles starting before this have closed
    idx = [n for n, b in enumerate(t) if _hhmm(b) < closed_before]
    if len(idx) < 2:
        return None
    i = idx[-1]
    di = order.index(d)
    prev = days[order[di - 1]]
    prev_ranges = [_pct(max(b["high"] for b in days[x]), min(b["low"] for b in days[x]))
                   for x in order[max(0, di - 5):di]]
    vw = _vwap(t)
    orb = [b for b in t if _hhmm(b) < "09:30"]
    or_hi = max(b["high"] for b in orb) if len(orb) >= 3 else None
    or_lo = min(b["low"] for b in orb) if len(orb) >= 3 else None
    flat = [b for x in order[:di] for b in days[x]] + t[:i]
    vols = [b["volume"] for b in flat[-20:]]
    avg = sum(vols) / len(vols) if vols else 0
    vol_x = t[i]["volume"] / avg if avg else None
    entry, stop = float(trade["entry"]), float(trade["stop"])
    per_share = abs(entry - stop)
    if per_share <= 0:
        return None
    setup = trade["setup"] if trade["setup"] in SETUPS else ("ORB long" if trade["side"] == "BUY" else "ORB short")
    feats = features(t, i, vw, trade["side"], entry, per_share, setup, prev[-1]["close"], prev_ranges, or_hi, or_lo,
                     vol_x, by_day(nifty or []).get(d, []), 0)
    sig = {"date": d, "time": _hhmm(t[i]), "setup": setup, "side": trade["side"], "entry": entry, "stop": stop,
           "target": float(trade["target"]), "i": i, "features": feats,
           "pdh": max(b["high"] for b in prev), "pdl": min(b["low"] for b in prev),
           "stretched": is_stretched(setup, trade["side"], t, i, vw[i], prev_ranges)}
    return label_signal(sig, t)
