"""Second opinion on a proposal: live market checks in plain words plus a suggested YES/NO split.

The split is a checklist score, not a win probability: it starts at 50 and moves up or down for each
check (Nifty, Sensex, the stock's sector index, how far the price has already moved, time left,
reward vs risk, the pre-market bias). If Claude gives its own view (ai_yes_pct), the two are averaged.
The user always makes the final call.
"""
import market

SECTOR_INDEX = {
    **{s: ("^NSEBANK", "Bank Nifty", "banks") for s in (
        "HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "INDUSINDBK", "BANKBARODA", "PNB",
        "FEDERALBNK", "IDFCFIRSTB", "AUBANK", "BANDHANBNK", "CANBK")},
    **{s: ("^CNXIT", "Nifty IT", "IT companies") for s in (
        "INFY", "TCS", "WIPRO", "HCLTECH", "TECHM", "LTIM", "MPHASIS", "PERSISTENT", "COFORGE")},
}
FLAT_PCT = 0.15  # index moves smaller than this count as "flat"
OK, WARN, INFO = "✅", "⚠️", "➖"


def index_move(symbol):
    """% change from today's first 5-min open to the latest close, or None if unavailable."""
    try:
        bars = market.candles(symbol, "5m", 1)
    except Exception:  # noqa: BLE001 - a missing index never blocks a proposal
        return None
    today = market.now_ist().strftime("%Y-%m-%d")
    bars = [b for b in bars if b["time"].startswith(today)]
    if not bars or not bars[0]["open"]:
        return None
    return round((bars[-1]["close"] - bars[0]["open"]) / bars[0]["open"] * 100, 2)


def _direction(pct):
    if abs(pct) < FLAT_PCT:
        return "flat", f"is flat ({pct:+.2f}%) since the open"
    return ("up" if pct > 0 else "down"), f"is {'up' if pct > 0 else 'down'} {abs(pct):.2f}% since the open"


def _index_check(name, pct, side, weight):
    if pct is None:
        return INFO, f"{name}: couldn't check right now.", 0
    trend, words = _direction(pct)
    want = "up" if side == "BUY" else "down"
    if trend == "flat":
        return INFO, f"{name} {words}: no help either way.", 0
    if trend == want:
        return OK, f"{name} {words}, the same direction as this {side}.", weight
    return WARN, f"{name} {words}, against this {side}.", -weight


def assess(state, symbol, side, entry, stop, target, rr, ai_yes_pct=None):
    """Return {"yes_pct", "verdict", "lines"} for the Telegram message."""
    checks = []  # (icon, text, points)
    checks.append(_index_check("Nifty 50", index_move("^NSEI"), side, 15))
    checks.append(_index_check("Sensex", index_move("^BSESN"), side, 5))
    if symbol in SECTOR_INDEX:
        idx, name, group = SECTOR_INDEX[symbol]
        icon, text, pts = _index_check(name, index_move(idx), side, 10)
        checks.append((icon, text.replace(name, f"{name} ({group})", 1), pts))

    # How far the price has already moved since the setup appeared.
    try:
        live, _ = market.last_price(symbol)
    except Exception:  # noqa: BLE001
        live = None
    if live:
        sign = 1 if side == "BUY" else -1
        to_target = abs(target - entry)
        to_stop = abs(entry - stop)
        moved = (live - entry) * sign
        if to_target and moved > 0.4 * to_target:
            checks.append((WARN, f"The price (Rs {live:,.2f}) has already moved {moved / to_target:.0%} of the "
                                 "way to the target: you'd be entering late, with less left to gain.", -15))
        elif to_stop and -moved > 0.5 * to_stop:
            checks.append((WARN, f"The price (Rs {live:,.2f}) has already slipped over half-way toward the "
                                 "stop-loss: the idea is weakening.", -10))
        else:
            checks.append((OK, f"The price is still close to the entry (Rs {live:,.2f}).", 5))
    else:
        checks.append((INFO, "Live price: couldn't check right now.", 0))

    # Time left before the 15:10 square-off.
    now = market.now_ist()
    mins_left = (15 * 60 + 10) - (now.hour * 60 + now.minute)
    left = f"{mins_left // 60}h {mins_left % 60:02d}m" if mins_left >= 60 else f"{max(mins_left, 0)} min"
    if mins_left < 70:
        checks.append((WARN, f"Only {left} left before the 15:10 close: less time for the target.", -10))
    else:
        checks.append((OK, f"{left} left before the 15:10 close.", 0))

    # Reward vs risk.
    if rr >= 2:
        checks.append((OK, f"Possible gain is {rr:g}x the possible loss.", 5))
    else:
        checks.append((WARN, f"Possible gain is only {rr:g}x the possible loss (2x is the aim).", -5))

    # Pre-market bias from the lakehouse brief.
    bias = ((state.get("plan") or {}).get("bias") or "").lower()
    if "gap-down" in bias or "gap-up" in bias:
        favours = "SELL" if "gap-down" in bias else "BUY"
        if side == favours:
            checks.append((OK, f"The morning plan expected a {bias}, which fits a {side}.", 5))
        else:
            checks.append((WARN, f"The morning plan expected a {bias}, which goes against a {side}.", -5))

    score = 50 + sum(p for _, _, p in checks)
    if ai_yes_pct is not None:
        score = (score + max(0, min(100, float(ai_yes_pct)))) / 2
    yes = int(round(max(5, min(95, score))))
    if yes >= 60:
        verdict = "YES"
    elif yes <= 40:
        verdict = "NO"
    else:
        verdict = "UNSURE"
    lines = [
        f"My suggestion: {verdict}, {yes}% for and {100 - yes}% against. You decide.",
        "(A score from the checks below, not the chance of profit.)",
    ]
    market_lines = ["Market check:", *[f"{icon} {text}" for icon, text, _ in checks]]
    return {"yes_pct": yes, "verdict": verdict, "lines": lines, "market_lines": market_lines}
