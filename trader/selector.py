"""Selector (paper only, from 23 Nov): picks tomorrow's version of the intraday rules from recent shadow results.

Three families, each chosen once a day after the day report, for the NEXT trading day only (walk-forward: a pick
never sees the day it is scored on):
  exit   which exit rule to use on real trades      evidence: exit_variants rows (every real trade replayed)
  bar    which auto-mode bar (50/55/60/65/70%)        evidence: every proposal's outcome (placed or skipped)
  entry  which entry for breakouts                   evidence: the opening-range test rows (breakout, retest,
                                                      09:15 opening buy, gap fade)

Guardrails against picking luck:
  - a window of the last WINDOW_DAYS trading days with results;
  - a version needs at least MIN_N trades in the window to be picked;
  - each average is shrunk towards zero by PRIOR_N trades (small samples count for little);
  - it switches only when the challenger beats the current pick by SWITCH_RS per trade (bar: per day).
Scored each day: what yesterday's picks made today vs the fixed rules (real result, 60% bar, 5-minute breakout).
Nothing here places, sizes or closes a real trade.
"""
from datetime import timedelta

import exits
import market
import storage
import weekly

FAMILIES = ("exit", "bar", "entry")
BASELINE = {"exit": "actual", "bar": "60", "entry": "orb5_2r"}
ENTRY_VERSIONS = ("orb5_2r", "orb15_2r", "orb5_retest", "orb15_retest", "open_2r", "fade_2r")
WORDS = {"actual": "the live rules", "orb5_2r": "5-min breakout", "orb15_2r": "15-min breakout",
         "orb5_retest": "5-min retest", "orb15_retest": "15-min retest", "open_2r": "09:15 opening buy",
         "fade_2r": "gap fade short"}
WINDOW_DAYS = 20
MIN_N = 30
PRIOR_N = 30
SWITCH_RS = 40.0       # about 0.1R per trade
KEY = "state/selector.json"


def word(family, name):
    if family == "bar":
        return f"bar {name}%"
    return WORDS.get(name) or exits.RULE_WORDS.get(name, name)


def evidence(rows):
    """{family: {version: [Rs per trade]}} from one day's journal rows."""
    ev = {f: {} for f in FAMILIES}
    for r in {r["event_id"]: r for r in rows if r["event"] == "exit_variants"}.values():
        res = exits.parse_note(r.get("note"))
        if res.get("current") is None:
            continue
        ev["exit"].setdefault("actual", []).append(float(r.get("pnl_rs") or 0))
        for k, v in res.items():
            if v is not None and k in exits.RULES:
                ev["exit"].setdefault(k, []).append(v)
    for pct, outcome in weekly.scored_proposals(rows):
        for b in weekly.BAR_LADDER:
            ev["bar"].setdefault(str(b), [])
            if pct > b:
                ev["bar"][str(b)].append(outcome)
    for r in {r["event_id"]: r for r in rows if r["event"] == "orb_shadow"}.values():
        for k, v in exits.parse_note(r.get("note")).items():
            if v is not None and k in ENTRY_VERSIONS:
                ev["entry"].setdefault(k, []).append(v)
    return ev


def score(family, results, days):
    """(score, n): shrunk average per trade; for the bar, the average per day (a lower bar trades more)."""
    n = len(results)
    if family == "bar":
        return (sum(results) / days if days else 0.0), n
    return (sum(results) / (n + PRIOR_N) if n else 0.0), n


def choose(family, window, current):
    """Tomorrow's pick for one family from the window's evidence: (pick, why)."""
    days = len(window)
    pooled = {}
    for ev in window:
        for version, vals in ev[family].items():
            pooled.setdefault(version, []).extend(vals)
    if not pooled:
        return current, "no results yet"
    scores = {v: score(family, vals, days) for v, vals in pooled.items()}
    total_n = sum(len(v) for v in pooled.values()) if family == "bar" else None
    eligible = {v: s for v, (s, n) in scores.items()
                if (total_n if family == "bar" else n) >= MIN_N}
    if not eligible:
        return current, f"too few trades yet (needs {MIN_N})"
    best = max(eligible, key=eligible.get)
    cur = eligible.get(current, scores.get(current, (0.0, 0))[0])
    if best != current and eligible[best] - cur > SWITCH_RS:
        return best, f"{word(family, best)} beat {word(family, current)} by Rs {eligible[best] - cur:,.0f} " \
                     f"{'a day' if family == 'bar' else 'a trade'} over {days} days"
    return current, "no version clearly better"


def run(state=None, today=None):
    """After the day report: score yesterday's picks on today, journal it, pick tomorrow's. Returns a summary."""
    import engine  # late: engine imports this module

    today = today or market.now_ist().date()
    st = storage.get_json(KEY) or {"picks": dict(BASELINE), "since": today.isoformat()}
    if not storage.read_rows(f"journal/{today:%Y-%m-%d}.csv"):
        return {}                                        # no trading day today: nothing to score or pick
    window = []
    for i in range(45):
        d = today - timedelta(days=i)
        rows = storage.read_rows(f"journal/{d:%Y-%m-%d}.csv")
        if rows:
            window.append(evidence(rows))
        if len(window) >= WINDOW_DAYS:
            break
    today_ev = window[0]
    made = {}
    for f in FAMILIES:
        pick = st["picks"].get(f, BASELINE[f])
        made[f] = (round(sum(today_ev[f].get(pick, [])), 2), round(sum(today_ev[f].get(BASELINE[f], [])), 2))
    picks, why = {}, {}
    for f in FAMILIES:
        picks[f], why[f] = choose(f, window, st["picks"].get(f, BASELINE[f]))
    note = ";".join(f"{f}_rs={made[f][0]};{f}_fixed_rs={made[f][1]}" for f in FAMILIES)
    engine.journal("selector", event_id=f"selector-{today:%Y-%m-%d}", note=note,
                   reasons=" | ".join(f"{f}: {st['picks'].get(f, BASELINE[f])} -> {picks[f]} ({why[f]})"
                                      for f in FAMILIES))
    storage.put_json(KEY, {"picks": picks, "since": st.get("since"), "updated": today.isoformat(), "why": why})
    return {"selector_made": made, "selector_picks": picks, "selector_why": why}


def lines(summary) -> list:
    if not summary.get("selector_picks"):
        return []
    m, p = summary["selector_made"], summary["selector_picks"]
    today = "; ".join(f"{f} {m[f][0]:+,.0f} vs fixed {m[f][1]:+,.0f}" for f in FAMILIES)
    nxt = ", ".join(word(f, p[f]) for f in FAMILIES)
    return [f"Selector (paper): today's picks made {today} Rs. Tomorrow: {nxt}."]
