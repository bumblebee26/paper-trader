"""Chart patterns on daily and weekly bars (from 24 Nov 2026; the friend's suggestion, information only).

Plain, fixed rules on completed bars only (never today's), so the same chart always gives the same answer:
  swing points     a bar whose high (low) is the highest (lowest) of the K bars either side
  double bottom    two swing lows within TOL of each other, at least GAP bars apart, with a swing high between them
                   at least DEPTH above them (the neckline). Confirmed once a close is above the neckline;
                   measured target = neckline + (neckline - lows). Fails below the lower low.
  double top       the mirror image (target = neckline - (highs - neckline)).
  bull / bear flag a pole (a move of at least POLE in at most POLE_BARS bars) then 3-15 quiet bars that give back
                   less than half the pole; target = the flag's edge + the pole's height. Daily bars only.
Each proposal shows the patterns that bear on its direction ("double bottom (weekly), neckline 1,240 broken ->
target 1,310, 2.1R") and the journal note keeps pattern=...; nothing is given to the trading agent or the reviewer
and nothing is blocked. A pattern becomes a rule only after a backtest on the 2019-2026 history beats its
baseline after costs (docs/DECISIONS.md section 14, VARIANTS chart_patterns).
"""
from datetime import date

K = 3            # bars either side for a swing point
TOL = 0.03       # two lows (highs) within 3%
GAP = 8          # at least 8 bars apart
DEPTH = 0.05     # neckline at least 5% away
LOOKBACK = 90    # bars searched
POLE, POLE_BARS = 0.08, 10
FLAG_MIN, FLAG_MAX = 3, 15


def completed(bars, today: str):
    return [b for b in bars or [] if str(b["time"])[:10] < today]


def weekly(bars):
    """Daily bars -> weekly bars (ISO weeks), oldest first."""
    out = {}
    for b in bars:
        k = date.fromisoformat(str(b["time"])[:10]).isocalendar()[:2]
        w = out.get(k)
        if w is None:
            out[k] = {"time": str(b["time"])[:10], "open": b["open"], "high": b["high"], "low": b["low"],
                      "close": b["close"]}
        else:
            w.update(high=max(w["high"], b["high"]), low=min(w["low"], b["low"]), close=b["close"])
    return [out[k] for k in sorted(out)]


def _swings(bars, kind):
    v = [b[kind] for b in bars]
    best = max if kind == "high" else min
    return [i for i in range(K, len(v) - K) if v[i] == best(v[i - K:i + K + 1])]


def _double(bars, frame, bottom=True):
    bars = bars[-LOOKBACK:]
    if len(bars) < 2 * K + GAP + 2:
        return None
    lows, highs = _swings(bars, "low"), _swings(bars, "high")
    ext, mid, key, other = (lows, highs, "low", "high") if bottom else (highs, lows, "high", "low")
    for j in range(len(ext) - 1, 0, -1):            # newest second touch first
        b2 = ext[j]
        for i in range(j - 1, -1, -1):
            b1 = ext[i]
            if b2 - b1 < GAP:
                continue
            p1, p2 = bars[b1][key], bars[b2][key]
            if abs(p1 - p2) / min(p1, p2) > TOL:
                continue
            between = [m for m in mid if b1 < m < b2]
            if not between:
                continue
            neck = (max if bottom else min)(bars[m][other] for m in between)
            base = (p1 + p2) / 2
            if (neck / base - 1 if bottom else 1 - neck / base) < DEPTH:
                continue
            after = bars[b2 + 1:]
            if bottom and any(b["low"] < min(p1, p2) for b in after):
                return None                           # failed: broke below the lows
            if not bottom and any(b["high"] > max(p1, p2) for b in after):
                return None
            broke = any((b["close"] > neck) if bottom else (b["close"] < neck) for b in after)
            height = abs(neck - base)
            return {"name": "double bottom" if bottom else "double top", "frame": frame,
                    "side": "BUY" if bottom else "SELL", "status": "neckline broken" if broke else "forming",
                    "level": round(neck, 2), "target": round(neck + height if bottom else neck - height, 2),
                    "fails": round(min(p1, p2) if bottom else max(p1, p2), 2)}
    return None


def _flag(bars, bull=True):
    bars = bars[-(POLE_BARS + FLAG_MAX + 1):]
    n = len(bars)
    for f in range(FLAG_MIN, FLAG_MAX + 1):           # the shortest flag that fits
        start = n - f                                  # flag = bars[start:]
        if start < 1:
            break
        flag = bars[start:]
        for p in range(max(0, start - POLE_BARS), start):
            lo, hi = (bars[p]["low"], bars[start - 1]["high"]) if bull else (bars[p]["high"], bars[start - 1]["low"])
            move = (hi / lo - 1) if bull else (1 - hi / lo)
            if move < POLE:
                continue
            pole = abs(hi - lo)
            if bull:
                if min(b["low"] for b in flag) < hi - pole / 2 or max(b["high"] for b in flag) > hi:
                    continue
                edge = max(b["high"] for b in flag)
            else:
                if max(b["high"] for b in flag) > hi + pole / 2 or min(b["low"] for b in flag) < hi:
                    continue
                edge = min(b["low"] for b in flag)
            return {"name": "bull flag" if bull else "bear flag", "frame": "daily", "side": "BUY" if bull else "SELL",
                    "status": "forming", "level": round(edge, 2),
                    "target": round(edge + pole if bull else edge - pole, 2),
                    "fails": round(hi - pole / 2 if bull else hi + pole / 2, 2)}
    return None


def find(bars, today: str) -> list:
    """Every pattern on the completed daily and weekly bars (at most one of each kind per frame)."""
    d = completed(bars, today)
    w = weekly(d)
    if w and date.fromisoformat(w[-1]["time"]).isocalendar()[:2] == date.fromisoformat(today).isocalendar()[:2]:
        w = w[:-1]                                     # this week isn't finished
    found = [_double(d, "daily", True), _double(d, "daily", False), _double(w, "weekly", True),
             _double(w, "weekly", False), _flag(d, True), _flag(d, False)]
    return [p for p in found if p]


def for_trade(pats, side, entry, stop) -> list:
    """The patterns that bear on this trade: same direction (supports it) or opposite (against it)."""
    risk = abs(entry - stop) or None
    out = []
    for p in pats:
        r = round(abs(p["target"] - entry) / risk, 1) if risk else None
        out.append({**p, "with": p["side"] == side.upper(), "target_r": r})
    return sorted(out, key=lambda p: not p["with"])


def line(pats, side, entry, stop) -> str:
    """'Pattern: double bottom (weekly), neckline 1,240 broken -> target 1,310 (2.1R); against: bear flag ...'."""
    rel = for_trade(pats, side, entry, stop)
    if not rel:
        return ""

    def one(p):
        what = "neckline" if p["name"].startswith("double") else "flag edge"
        r = f", {p['target_r']:g}R" if p.get("target_r") is not None else ""
        return (f"{'' if p['with'] else 'against: '}{p['name']} ({p['frame']}), {what} {p['level']:,.2f} "
                f"{p['status']} -> target {p['target']:,.2f}{r}")
    return "Pattern: " + "; ".join(one(p) for p in rel[:2]) + " (information only)"


def note(pats) -> str:
    """Journal note fragment: 'pattern=double_bottom_weekly:forming,bull_flag_daily:forming' or 'pattern=-'."""
    if not pats:
        return "pattern=-"
    return "pattern=" + ",".join(f"{p['name'].replace(' ', '_')}_{p['frame']}:{p['status'].split()[0]}"
                                 for p in pats)
