"""Exit rules on trial (shadow mode): what each closed trade would have made under other exit rules.

Nothing here changes a real trade. After the close, each of today's trades is replayed on its 1-minute
candles under the current rule and four alternatives, with the same costs as real fills:

  current       fixed stop, fixed target (2R), 15:10 square-off: what the engine does today
  breakeven     once the trade is +1R, the stop moves to the entry price
  half_and_run  at +1R, sell half there and move the stop to entry; the rest runs to the target, stop or 15:10
  trailing      no fixed target; once +1R, the stop trails 1R behind the best price
  time_stop     the current rule, but exit at the 60-minute mark if the trade isn't +0.5R by then
  no_stop       no stop at all: target or the 15:10 square-off (what "never stop out" would have done)
  wide_1_5      stop 1.5x as far, with fewer shares so the rupee risk is the same; same target price
  wide_2        stop 2x as far, half the shares; same target price
  close_stop    the stop counts only when a 5-minute candle CLOSES beyond it (wicks ignored), with a hard
                stop at 2x the distance as a safety net (from 17 Nov)
  target_1_5r   the current rule with a nearer target: 1.5R instead of 2R (from 19 Nov)
  early_any     the current rule plus the trade's own early exit on ANY 5-minute close beyond its exit line
                (how the session used to act on "invalid if": TECHM on 20 Nov left on a close 0.06% past VWAP)
  early_strict  the current rule plus the early exit only on a real break: two 5-minute closes in a row
                beyond the line, or one close at least EARLY_BREAK_PCT beyond it. Live trades use this
                from 20 Nov (engine.monitor); both are replayed so the review can see if early exits pay
  level_target  the current rule, but the target moves to the previous day's high (low for a short) when that
                level sits between +0.5R and the planned target: the first resistance takes the profit (from 19 Nov)

Plus one rule for the whole day:

  profit lock   once the day is up 3R, no new trades if it falls back to half of its best

Comparisons are rule against rule on the same candles: "current" is replayed too, so candle-level
approximations (which of the stop and target came first inside one minute) affect every rule alike.
Within a candle the stop is checked first, then the target, then any stop move, which applies from the
next candle (conservative). Results are journaled for the daily and weekly reports and the 23 Nov review.
"""
import config
import fees

RULES = ("current", "breakeven", "half_and_run", "trailing", "time_stop", "no_stop", "wide_1_5", "wide_2",
         "close_stop", "target_1_5r", "level_target", "early_any", "early_strict")
RULE_WORDS = {
    "current": "fixed 2R target",
    "breakeven": "stop to entry at +1R",
    "half_and_run": "sell half at +1R",
    "trailing": "trailing stop",
    "time_stop": "60-minute time stop",
    "no_stop": "no stop",
    "wide_1_5": "stop 1.5x wider, same Rs risk",
    "wide_2": "stop 2x wider, same Rs risk",
    "close_stop": "stop on a 5-min close",
    "target_1_5r": "target 1.5R",
    "level_target": "target at yesterday's high/low",
    "early_any": "early exit on any close past the line",
    "early_strict": "early exit on a real break",
}
WIDE = {"wide_1_5": 1.5, "wide_2": 2.0}
HARD_STOP_X = 2.0     # close_stop's safety net, in multiples of the planned stop distance
SHORT_TARGET_R = 1.5  # target_1_5r
EARLY_BREAK_PCT = 0.2 # early_strict (and live): one 5-minute close this far past the exit line, or two in a row
LEVEL_MIN_R = 0.5     # level_target: a level nearer than this is ignored (costs would eat the profit)


def _last_of_5min(bars):
    """For each bar, whether it is the last one of its 5-minute bucket (so its close is the 5-minute close)."""
    keys = [b["time"][:14] + f"{int(b['time'][14:16]) // 5:02d}" for b in bars]
    return [i == len(keys) - 1 or keys[i + 1] != k for i, k in enumerate(keys)]
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


def early_break(side, line, closes, strict=True):
    """The trade's early exit: True when the completed 5-minute closes (oldest first, after entry) break its exit
    line (below it for a BUY, above it for a SELL). strict: two closes in a row past the line, or one at least
    EARLY_BREAK_PCT past it; not strict: any close past it."""
    if line is None or not closes:
        return False
    d = 1 if side == "BUY" else -1

    def past(c):
        return (line - c) * d > 0

    if not strict:
        return past(closes[-1])
    if past(closes[-1]) and (line - closes[-1]) * d / line * 100 >= EARLY_BREAK_PCT:
        return True
    return len(closes) >= 2 and past(closes[-1]) and past(closes[-2])


def valid_exit_line(side, entry, stop, line):
    """An exit line only counts between the stop and the entry (it must trigger before the stop)."""
    if line is None:
        return None
    d = 1 if side == "BUY" else -1
    return float(line) if (entry - line) * d > 0 and (line - stop) * d > 0 else None


def level_target(side, fill, stop, target, level):
    """The previous day's high (BUY) or low (SELL) as the target when it sits between +LEVEL_MIN_R and the planned
    target; otherwise the planned target."""
    d = 1 if side == "BUY" else -1
    r = (fill - stop) * d
    if level is None or r <= 0:
        return target
    if (level - (fill + d * LEVEL_MIN_R * r)) * d >= 0 and (target - level) * d > 0:
        return level
    return target


def day_levels(daily_bars, today):
    """(high, low) of the last daily candle before `today` ('YYYY-MM-DD'), or (None, None)."""
    prev = [b for b in daily_bars or [] if b["time"][:10] < today]
    return (prev[-1]["high"], prev[-1]["low"]) if prev else (None, None)


def replay(rule, side, qty, fill, stop, target, opened_at, bars, level=None, exit_line=None):
    """Net P&L of one trade under `rule` (every order's charges from fees.py, exit slippage; the fill already
    has entry slippage). rule 'eod': fixed stop, no target, held to the square-off. bars: 1-minute candles {time 'YYYY-MM-DD HH:MM', open, high, low, close}.
    level: the previous day's high for a BUY (low for a SELL), used by level_target; without it that rule is the
    current one. exit_line: the trade's early-exit line (early_any / early_strict; without it they are current)."""
    d = 1 if side == "BUY" else -1
    r = (fill - stop) * d
    if r <= 0 or qty <= 0:
        return None
    if rule == "target_1_5r":
        nearer = fill + d * SHORT_TARGET_R * r
        target = nearer if (target - nearer) * d > 0 else target
        rule = "current"
    elif rule == "level_target":
        target = level_target(side, fill, stop, target, level)
        rule = "current"
    if rule in WIDE:   # wider stop, fewer shares: the rupee risk stays the same
        m = WIDE[rule]
        qty = max(1, int(qty / m))
        stop = fill - d * r * m
        r = r * m
        rule = "current"
    early = {"early_any": False, "early_strict": True}.get(rule)
    if early is not None:
        rule = "current"
        if exit_line is None:
            early = None
    hard_stop = fill - d * r * HARD_STOP_X if rule == "close_stop" else None
    closes_5m = _last_of_5min(bars) if rule == "close_stop" or early is not None else None
    seen_closes = []
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
    for i, b in enumerate(bars):
        if b["time"] <= opened_at[:16]:
            continue
        hhmm = b["time"][11:16]
        last_close = b["close"]
        if hhmm >= config.SQUARE_OFF:
            leave(remaining, b["open"])
            break
        adverse = b["low"] if d == 1 else b["high"]
        favorable = b["high"] if d == 1 else b["low"]
        if rule == "close_stop":
            if (adverse - hard_stop) * d <= 0:
                leave(remaining, min(b["open"], hard_stop) if d == 1 else max(b["open"], hard_stop))
                break
        elif rule != "no_stop" and (adverse - cur_stop) * d <= 0:
            leave(remaining, min(b["open"], cur_stop) if d == 1 else max(b["open"], cur_stop))
            break
        if tgt is not None and (favorable - tgt) * d >= 0:
            leave(remaining, max(b["open"], tgt) if d == 1 else min(b["open"], tgt))
            break
        if early is not None and closes_5m[i]:
            seen_closes.append(b["close"])
            if early_break(side, exit_line, seen_closes, strict=early):
                leave(remaining, b["close"])
                break
        if rule == "close_stop" and closes_5m[i] and (b["close"] - cur_stop) * d <= 0:
            leave(remaining, b["close"])
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


def replay_all(trade, bars, levels=(None, None)):
    """{rule: net P&L} for one trade; trade has side, qty, fill_price, stop, target, opened_at.
    levels: the previous day's (high, low)."""
    level = levels[0] if trade["side"] == "BUY" else levels[1]
    line = trade.get("exit_line")
    out = {}
    for rule in RULES:
        out[rule] = replay(rule, trade["side"], int(trade["qty"]), float(trade["fill_price"]),
                           float(trade["stop"]), float(trade["target"]), trade["opened_at"], bars, level=level,
                           exit_line=float(line) if line not in (None, "") else None)
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
