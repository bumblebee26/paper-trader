"""Is a breakout entry already stretched? (background rule on trial, from 19 Nov; paper only)

A breakout always buys a new high of the day (sells a new low), so on a day that opened low and rallied, the
opening-range breakout buys after most of the move. Prompted by PRESTIGE on 18 Nov. Stretched means either:

  - the price has already moved more than STRETCH_RANGE_SHARE of the stock's usual daily range off today's low
    (off today's high for a SELL), the usual range being the average high-low range of its last 5 sessions; or
  - the price is more than STRETCH_VWAP_PCT beyond today's average price (VWAP), above it for a BUY.

Used by the live checks (trader/advisor.py: shadow block "stretched", recorded on the proposal and judged in the
weekly report; an information line only, no points) and by the nightly walk-forward backtest (models/intraday.py:
rule "skip_stretched"). Pure function: no imports, no network.
"""

STRETCH_RANGE_SHARE = 0.7
STRETCH_VWAP_PCT = 1.0
BREAKOUT_SETUPS = ("ORB long", "ORB short", "PDH break", "PDL break")


def check(side, price, day_low, day_high, vwap, usual_range_pct):
    """(stretched?, plain words). Missing inputs count as not stretched."""
    if not price:
        return False, ""
    d = 1 if side == "BUY" else -1
    words = []
    extreme = day_low if d == 1 else day_high
    off = (price - extreme) * d / price * 100 if extreme else None
    if off is not None and usual_range_pct:
        share = off / usual_range_pct
        if share > STRETCH_RANGE_SHARE:
            words.append(f"{off:.1f}% off today's {'low' if d == 1 else 'high'}, {share:.0%} of its usual "
                         f"day's range ({usual_range_pct:.1f}%)")
    beyond = (price / vwap - 1) * 100 * d if vwap else None
    if beyond is not None and beyond > STRETCH_VWAP_PCT:
        words.append(f"{beyond:.1f}% {'above' if d == 1 else 'below'} today's average price (VWAP)")
    return bool(words), "; ".join(words)


def usual_range_pct(daily_bars, today):
    """Average high-low range (% of the low) of the last 5 completed sessions before `today`, or None."""
    prev = [b for b in daily_bars or [] if b["time"][:10] < today and b.get("low")][-5:]
    if not prev:
        return None
    return sum((b["high"] / b["low"] - 1) * 100 for b in prev) / len(prev)
