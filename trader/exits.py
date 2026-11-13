"""Exit rules on trial (shadow mode): what each closed trade would have made under other exit rules.

Nothing here changes a real trade. After the close, each of today's trades is replayed on its 1-minute
candles under the current rule and four alternatives, with the same costs as real fills:

  current       fixed stop, fixed target (2R), 15:10 square-off: what the engine does today
  breakeven     once the trade is +1R, the stop moves to the entry price
  half_and_run  at +1R, sell half there and move the stop to entry; the rest runs to the target, stop or 15:10
  trailing      no fixed target; once +1R, the stop trails 1R behind the best price
  time_stop     the current rule, but exit at the 60-minute mark if the trade isn't +0.5R by then

Plus one rule for the whole day:

  profit lock   once the day is up 3R, no new trades if it falls back to half of its best

Comparisons are rule against rule on the same candles: "current" is replayed too, so candle-level
approximations (which of the stop and target came first inside one minute) affect every rule alike.
Within a candle the stop is checked first, then the target, then any stop move, which applies from the
next candle (conservative). Results are journaled for the daily and weekly reports and the 23 Nov review.
"""
import config
import fees

RULES = ("current", "breakeven", "half_and_run", "trailing", "time_stop")
RULE_WORDS = {
    "current": "current rules",
    "breakeven": "stop to entry at +1R",
    "half_and_run": "sell half at +1R",
    "trailing": "trailing stop",
    "time_stop": "60-minute time stop",
}
TIME_STOP_MIN = 60
TIME_STOP_R = 0.5
LOCK_ARM_R = 3.0
LOCK_GIVEBACK = 0.5


def _minutes(hhmm_a, hhmm_b):
    ha, ma = int(hhmm_a[:2]), int(hhmm_a[3:5])
    hb, mb = int(hhmm_b[:2]), int(hhmm_b[3:5])
    return (hb * 60 + mb) - (ha * 60 + ma)


def _exit_fill(price, side):
    f = config.SLIPPAGE_PCT / 100
    return round(price * (1 - f) if side == "BUY" else price * (1 + f), 2)


def replay(rule, side, qty, fill, stop, target, opened_at, bars):
    """Net P&L of one trade under `rule` (every order's charges from fees.py, exit slippage; the fill already
    has entry slippage). rule 'eod': fixed stop, no target, held to the square-off. bars: 1-minute candles {time 'YYYY-MM-DD HH:MM', open, high, low, close}."""
    d = 1 if side == "BUY" else -1
    r = (fill - stop) * d
    if r <= 0 or qty <= 0:
        return None
    open_hhmm = opened_at[11:16]
    cur_stop = stop
    tgt = None if rule in ("trailing", "eod") else target
    remaining, pnl = qty, 0.0
    charges = fees.order(fill * qty, side)   # entry order
    best = fill
    one_r_price = fill + d * r
    reached_1r = False
    time_checked = False

    def leave(n, price):
        nonlocal remaining, pnl, charges
        px = _exit_fill(price, side)
        pnl += (px - fill) * n * d
        remaining -= n
        charges += fees.order(px * n, "SELL" if side == "BUY" else "BUY")

    last_close = None
    for b in bars:
        if b["time"] <= opened_at[:16]:
            continue
        hhmm = b["time"][11:16]
        last_close = b["close"]
        if hhmm >= config.SQUARE_OFF:
            leave(remaining, b["open"])
            break
        adverse = b["low"] if d == 1 else b["high"]
        favorable = b["high"] if d == 1 else b["low"]
        if (adverse - cur_stop) * d <= 0:
            leave(remaining, min(b["open"], cur_stop) if d == 1 else max(b["open"], cur_stop))
            break
        if tgt is not None and (favorable - tgt) * d >= 0:
            leave(remaining, max(b["open"], tgt) if d == 1 else min(b["open"], tgt))
            break
        if rule == "time_stop" and not time_checked and _minutes(open_hhmm, hhmm) >= TIME_STOP_MIN:
            time_checked = True
            if (b["close"] - (fill + d * TIME_STOP_R * r)) * d < 0:
                leave(remaining, b["close"])
                break
        hit_1r = (favorable - one_r_price) * d >= 0
        if rule == "half_and_run" and hit_1r and not reached_1r and remaining > 1:
            half = remaining // 2
            leave(half, max(b["open"], one_r_price) if d == 1 else min(b["open"], one_r_price))
            cur_stop = fill
        if rule == "breakeven" and hit_1r:
            cur_stop = fill if (fill - cur_stop) * d > 0 else cur_stop
        best = max(best, favorable) if d == 1 else min(best, favorable)
        if rule == "trailing" and (hit_1r or reached_1r):
            trail = best - d * r
            if (trail - cur_stop) * d > 0:
                cur_stop = trail
        reached_1r = reached_1r or hit_1r
    if remaining > 0 and last_close is not None:
        leave(remaining, last_close)
    if remaining > 0:
        return None
    return round(pnl - charges, 2)


def replay_all(trade, bars):
    """{rule: net P&L} for one trade; trade has side, qty, fill_price, stop, target, opened_at."""
    out = {}
    for rule in RULES:
        out[rule] = replay(rule, trade["side"], int(trade["qty"]), float(trade["fill_price"]),
                           float(trade["stop"]), float(trade["target"]), trade["opened_at"], bars)
    return out


def profit_lock(events, one_r):
    """events: today's trades in time order as (fill_time, exit_time, net_pnl). Replays the day with the
    profit lock: armed once the day reaches +3R, then no new trades once it falls to half of its best.
    Returns None if the lock never armed, else {skipped, day_with_lock, day_actual}."""
    timeline = []
    for i, (fill_t, exit_t, net) in enumerate(events):
        timeline.append((fill_t, 0, i))   # fills sort before exits at the same second
        timeline.append((exit_t, 1, i))
    timeline.sort()
    cum = peak = 0.0
    armed = locked = False
    skipped = set()
    for _, kind, i in timeline:
        if kind == 0:
            if locked:
                skipped.add(i)
            continue
        if i in skipped:
            continue
        cum += events[i][2]
        peak = max(peak, cum)
        armed = armed or peak >= LOCK_ARM_R * one_r
        if armed and cum <= peak * LOCK_GIVEBACK:
            locked = True
    if not armed:
        return None
    actual = sum(e[2] for e in events)
    return {"skipped": len(skipped), "day_actual": round(actual, 2),
            "day_with_lock": round(actual - sum(events[i][2] for i in skipped), 2)}


def note_text(results):
    return ";".join(f"{k}={'' if v is None else v}" for k, v in results.items())


def parse_note(note):
    out = {}
    for part in (note or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = float(v) if v not in ("", "None") else None
    return out
