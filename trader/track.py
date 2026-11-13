"""The track record from the lakehouse (stocks.gold.track_record): how past proposals turned out, so each
decision can learn from results. Shown in every proposal and given to Claude each morning.

Outcomes cover every proposal, not just the ones traded: a taken trade counts its real result, a skipped one
what it would have made (passed_outcome). That measures the model's judgement on its own. Rows with fewer
than MIN_TRADES outcomes are shown as "too few to judge" and must not change a decision. Read once per
process (one query per session); [] if the lakehouse is unavailable.
"""
import brief
from http_client import log

MIN_TRADES = 8
QUERY = """
SELECT dimension, value, trades, wins, win_rate_pct, avg_outcome_rs, total_outcome_rs
FROM stocks.gold.track_record
"""
_cache = None


def reset_cache():
    global _cache
    _cache = None


def rows(runner=None):
    global _cache
    if _cache is None:
        try:
            _cache = (runner or brief.sql)(QUERY)
        except Exception as err:  # noqa: BLE001 - the record is advice; never block a proposal on it
            log.warning("track record unavailable: %s", err)
            _cache = []
    return _cache


def band(yes_pct) -> str:
    lo = int(max(0, min(99, float(yes_pct)))) // 10 * 10
    return f"{lo}-{lo + 9}%"


def lookup(dimension, value):
    for r in rows():
        if r["dimension"] == dimension and str(r["value"]) == str(value):
            return r
    return None


def describe(r, label) -> str:
    n = int(r["trades"] or 0)
    text = f"{label}: won {int(r['wins'] or 0)} of {n}, average {_rs(r['avg_outcome_rs'])}"
    return text + ("" if n >= MIN_TRADES else " (too few to judge)")


def lines_for(setup, yes_pct, mood=None, hour=None) -> list:
    """Track-record lines for one proposal (empty if there's no record yet)."""
    items = [("setup", setup, setup), ("yes_band", band(yes_pct), f"Suggestions of {band(yes_pct)}")]
    if mood:
        items.append(("mood", mood, f"Market mood '{mood}'"))
    if hour:
        items.append(("hour", hour, f"Proposals at {hour}:00-{hour}:59"))
    found = [describe(r, label) for dim, val, label in items if (r := lookup(dim, val))]
    if not found:
        return []
    return ["Track record (past proposals, incl. skipped ones):", *[f"- {t}" for t in found]]


def compact(setup, yes_pct, mood=None, hour=None) -> str:
    """One line for the compact message: 'Track: PDL break 1/4 · 50-59% 2/4 · choppy 4/14 (avg -Rs 99)'."""
    items = [("setup", setup, setup), ("yes_band", band(yes_pct), band(yes_pct))]
    if mood:
        items.append(("mood", mood, mood))
    if hour:
        items.append(("hour", hour, f"{hour}:00s"))
    parts = []
    for dim, val, label in items:
        r = lookup(dim, val)
        if r:
            n = int(r["trades"] or 0)
            parts.append(f"{label} {int(r['wins'] or 0)}/{n}"
                         + (f" (avg {_rs(r['avg_outcome_rs'])})" if n >= MIN_TRADES else ""))
    return ("Track: " + " · ".join(parts)) if parts else ""


def losing(setup, mood=None, hour=None) -> list:
    """Groups this proposal belongs to whose record is judged (MIN_TRADES+) and losing on average,
    e.g. ['choppy mood']. Auto mode raises its bar for these."""
    out = []
    for dim, val, label in (("setup", setup, f"{setup} setup"), ("mood", mood, f"{mood} mood"),
                            ("hour", hour, f"{hour}:00 hour")):
        r = lookup(dim, val) if val else None
        if r and int(r["trades"] or 0) >= MIN_TRADES and float(r["avg_outcome_rs"] or 0) < 0:
            out.append(label)
    return out


def for_claude() -> list:
    """The whole record for the morning context, each row marked judged (enough outcomes) or not."""
    return [{**r, "judged": int(r.get("trades") or 0) >= MIN_TRADES} for r in rows()]


def _rs(x) -> str:
    x = float(x or 0)
    return f"{'+' if x >= 0 else '-'}Rs {abs(x):,.0f}"
