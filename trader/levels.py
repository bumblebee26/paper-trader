"""Price levels a trade runs into (from 24 Nov 2026; the friend's suggestion B1, council verdict: show first, filter
only if a backtest proves it).

From completed daily bars only (never today's): the previous day's, week's and month's high and low and the
52-week high and low. For a proposal, the nearest level in the trade's direction and how far away it is in R (one R
= entry to stop): "room to the next level". It is shown in the Telegram proposal and written to the journal note
(room=..R) so it can be judged later, with the weekly RSI as information (B3); it is NOT given to the trading agent or the reviewer and blocks nothing. A
"skip if under 1R of room" filter is tested offline first and replaces the existing "stretched" rule only if it
beats it (docs/DECISIONS.md).
"""
from datetime import date

NAMES = {"pdh": "yesterday's high", "pdl": "yesterday's low", "pwh": "last week's high", "pwl": "last week's low",
         "pmh": "last month's high", "pml": "last month's low", "h52": "52-week high", "l52": "52-week low"}


def compute(bars, today: str) -> dict:
    """{key: price} from daily bars [{time 'YYYY-MM-DD ...', high, low}] before `today`."""
    done = [b for b in bars or [] if str(b["time"])[:10] < today]
    if not done:
        return {}
    t = date.fromisoformat(today)
    this_week = t.isocalendar()[:2]
    out = {"pdh": done[-1]["high"], "pdl": done[-1]["low"]}

    def span(pick):
        rows = [b for b in done if pick(date.fromisoformat(str(b["time"])[:10]))]
        return (max(b["high"] for b in rows), min(b["low"] for b in rows)) if rows else (None, None)

    # previous week = the latest completed ISO week before this one; previous month likewise
    weeks = sorted({date.fromisoformat(str(b["time"])[:10]).isocalendar()[:2] for b in done} - {this_week})
    if weeks:
        out["pwh"], out["pwl"] = span(lambda d: d.isocalendar()[:2] == weeks[-1])
    months = sorted({str(b["time"])[:7] for b in done} - {today[:7]})
    if months:
        out["pmh"], out["pml"] = span(lambda d: d.isoformat()[:7] == months[-1])
    year = done[-252:]
    out["h52"], out["l52"] = max(b["high"] for b in year), min(b["low"] for b in year)
    return {k: round(v, 2) for k, v in out.items() if v is not None}


def weekly_rsi(bars, today: str, n: int = 14):
    """RSI(14) on weekly closes (the last close of each completed ISO week before today's week); None if short.
    Information only (the friend's suggestion B3): the daily scanner already uses daily RSI and MACD."""
    this_week = date.fromisoformat(today).isocalendar()[:2]
    closes = {}
    for b in bars or []:
        d = date.fromisoformat(str(b["time"])[:10])
        if d.isocalendar()[:2] < this_week:
            closes[d.isocalendar()[:2]] = b["close"]
    c = [closes[k] for k in sorted(closes)]
    if len(c) <= n:
        return None
    gains = [max(c[i] - c[i - 1], 0) for i in range(1, len(c))]
    losses = [max(c[i - 1] - c[i], 0) for i in range(1, len(c))]
    g, lo = sum(gains[:n]) / n, sum(losses[:n]) / n
    for x, y in zip(gains[n:], losses[n:]):                 # Wilder's smoothing
        g, lo = (g * (n - 1) + x) / n, (lo * (n - 1) + y) / n
    return 100.0 if lo == 0 else round(100 - 100 / (1 + g / lo), 0)


def room(lv: dict, side: str, entry: float, stop: float):
    """The nearest level beyond the entry in the trade's direction: {key, price, r}, or None if there is none
    (a buy above the 52-week high, a sell below the 52-week low)."""
    risk = abs(entry - stop)
    if not lv or risk <= 0:
        return None
    buy = side.upper() == "BUY"
    ahead = [(k, p) for k, p in lv.items() if (p > entry if buy else p < entry)]
    if not ahead:
        return None
    k, p = min(ahead, key=lambda kp: abs(kp[1] - entry))
    return {"key": k, "price": p, "r": round(abs(p - entry) / risk, 2)}


def line(lv: dict, side: str, entry: float, stop: float, wrsi=None) -> str:
    """One plain line for the proposal, e.g. 'Room: last week's high 1,234.50 is 0.8R away (under 1R) · weekly
    RSI 64'."""
    if not lv:
        return ""
    r = room(lv, side, entry, stop)
    if r is None:
        text = ("Room: clear, above every recent high (52-week high broken)" if side.upper() == "BUY"
                else "Room: clear, below every recent low (52-week low broken)")
    else:
        text = f"Room: {NAMES[r['key']]} {r['price']:,.2f} is {r['r']:g}R away{' (under 1R)' if r['r'] < 1 else ''}"
    return text + (f" · weekly RSI {wrsi:g}" if wrsi is not None else "")


def note(lv: dict, side: str, entry: float, stop: float) -> str:
    """Journal note fragment: 'room=0.8R@pwh', 'room=clear' or 'room=-'."""
    if not lv:
        return "room=-"
    r = room(lv, side, entry, stop)
    return "room=clear" if r is None else f"room={r['r']:g}R@{r['key']}"
