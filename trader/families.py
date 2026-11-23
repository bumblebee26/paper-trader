"""Families and the scoreboard (from 24 Nov, Phase 3 of the 23 Nov review).

Every paper variant keeps running under its own stored name; this module only groups them into families and reads
them honestly, since the 18 Nov ledger reset:

  - each family is compared with its baseline (exits: what live trades do; paper entries and filters: no trade);
  - a "luck line": the best per-trade result the family's variants would show by chance if none of them were better
    than the baseline (a bootstrap that resamples whole trading days, so variants replaying the same trades stay
    correlated, as they are);
  - a plain verdict: "too early" under MIN_TRADES, then "could be luck" or "beating its baseline".

Nothing here changes a trade. The variant ledger (docs/VARIANTS.csv) lists every variant ever tried, its family, lane
and kill rule; the pass bar is in docs/DECISIONS.md.
"""
import random
from collections import defaultdict
from datetime import date, timedelta

import config
import exits
import storage

MIN_TRADES = 100           # below this a family's verdict is "too early"
PROMOTE_TRADES = 200       # a variant needs this many (and to beat the luck line) before a review may use it live
BOOTSTRAP_RUNS = 2000
LUCK_QUANTILE = 0.95
REAL_MONEY_TRADES = 200    # docs/SYSTEM_PLAN.md: the real-money bar

# family -> (baseline key, {variant key: label}); exit variants come from exit_variants rows, entries from
# orb_shadow rows, filters from proposals' shadow-block flags, score from the suggestion on each proposal.
EXIT_FAMILIES = {
    "Exit: stop placement": ["wide_1_5", "wide_2", "close_stop", "no_stop"],
    "Exit: at +1R": ["breakeven", "half_and_run", "trailing", "trailing_early"],
    "Exit: target": ["target_1_5r", "level_target"],
    "Exit: early exit": ["current", "early_any"],
    "Exit: time stop": ["time_stop"],
}
ENTRY_FAMILIES = {
    "Entry: opening range": ["orb5_2r", "orb5_1_5r", "orb5_level", "orb5_eod", "orb15_2r", "orb15_1_5r",
                             "orb15_level", "orb15_eod", "orb5_retest", "orb15_retest"],
    "Entry: opening auction": ["open_2r", "open_eod", "fade_2r", "fade_eod"],
}
FILTER_FAMILIES = {
    "Filter: market against the trade": ["nifty_against_0.3", "against_mood", "sell_uptrend"],
    "Filter: late entry": ["after_14h"],
    "Filter: price position": ["vwap_wrong_side", "stretched"],
}
SCORE_FAMILY = "Score: auto bar and sizing"
SCORE_VARIANTS = ["bar50", "bar60", "bar65", "bar70", "sized"]   # baseline: the live 55% bar

ENTRY_WORDS = {"orb5_2r": "5-min range, 2R", "orb5_1_5r": "5-min range, 1.5R", "orb5_level": "5-min range, level",
               "orb5_eod": "5-min range, to 15:10", "orb15_2r": "15-min range, 2R", "orb15_1_5r": "15-min range, 1.5R",
               "orb15_level": "15-min range, level", "orb15_eod": "15-min range, to 15:10",
               "orb5_retest": "5-min retest", "orb15_retest": "15-min retest", "open_2r": "09:15 buy, 2R",
               "open_eod": "09:15 buy, to 15:10", "fade_2r": "gap fade, 2R", "fade_eod": "gap fade, to 15:10"}
FILTER_WORDS = {"nifty_against_0.3": "Nifty against by 0.3%", "against_mood": "against the mood",
                "sell_uptrend": "no SELL in an uptrend", "after_14h": "nothing after 14:00",
                "vwap_wrong_side": "wrong side of VWAP", "stretched": "skip stretched breakouts"}
SCORE_WORDS = {"bar50": "bar 50%", "bar60": "bar 60%", "bar65": "bar 65%", "bar70": "bar 70%",
               "sized": "sized by the score"}


def label(key):
    return (exits.RULE_WORDS.get(key) or ENTRY_WORDS.get(key) or FILTER_WORDS.get(key) or SCORE_WORDS.get(key)
            or key)


def journal_days(today: date):
    """[(day, rows)] for every day with a journal since the ledger reset."""
    out, d = [], date.fromisoformat(config.LEDGER_START)
    while d <= today:
        rows = storage.read_rows(f"journal/{d:%Y-%m-%d}.csv")
        if rows:
            out.append((d.isoformat(), rows))
        d += timedelta(days=1)
    return out


def _outcomes(rows):
    """{proposal_id: Rs} for proposals with a known outcome (placed: net of both fees; skipped: would-have)."""
    entry_fee = {r["proposal_id"]: float(r.get("fees_rs") or 0) for r in rows
                 if r["event"] == "filled" and r.get("proposal_id")}
    out = {r["proposal_id"]: float(r.get("pnl_rs") or 0) for r in rows
           if r["event"] == "passed_outcome" and r.get("proposal_id")}
    out.update({r["proposal_id"]: float(r.get("pnl_rs") or 0) - entry_fee.get(r["proposal_id"], 0)
                for r in rows if r["event"] in ("exited", "squared_off") and r.get("proposal_id")})
    return out


def day_evidence(rows):
    """{family: {key: (Rs vs baseline, trades)}} for one day."""
    import weekly   # SUGGESTED / SHADOW patterns and the score sizing live there

    ev = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
    for r in {r["event_id"]: r for r in rows if r["event"] == "exit_variants"}.values():
        res = exits.parse_note(r.get("note"))
        base = res.get(exits.LIVE_BASELINE, res.get("current"))
        if base is None:
            continue
        for fam, keys in EXIT_FAMILIES.items():
            for k in keys:
                if res.get(k) is not None:
                    ev[fam][k][0] += res[k] - base
                    ev[fam][k][1] += 1
    for r in {r["event_id"]: r for r in rows if r["event"] == "orb_shadow"}.values():
        res = exits.parse_note(r.get("note"))
        for fam, keys in ENTRY_FAMILIES.items():
            for k in keys:
                if res.get(k) is not None:
                    ev[fam][k][0] += res[k]
                    ev[fam][k][1] += 1
    outcome = _outcomes(rows)
    for r in rows:
        if r["event"] != "proposed" or r.get("proposal_id") not in outcome:
            continue
        o, note = outcome[r["proposal_id"]], r.get("note") or ""
        sh = weekly.SHADOW.search(note)
        flags = set(sh.group(1).split(",")) if sh else set()
        for fam, keys in FILTER_FAMILIES.items():
            for k in keys:
                if k in flags:                       # the filter would have skipped it: its effect is -outcome
                    ev[fam][k][0] -= o
                    ev[fam][k][1] += 1
        m = weekly.SUGGESTED.search(note)
        if m:
            pct = int(m.group(2))
            live = o if pct > config.AUTO_MIN_YES_PCT else 0.0
            for b in (50, 60, 65, 70):
                ev[SCORE_FAMILY][f"bar{b}"][0] += (o if pct > b else 0.0) - live
                ev[SCORE_FAMILY][f"bar{b}"][1] += 1
            mult = weekly.size_for_score(pct)
            ev[SCORE_FAMILY]["sized"][0] += (mult or 0) * o - live
            ev[SCORE_FAMILY]["sized"][1] += 1
    return ev


def luck_line(daily, seed=7):
    """daily: {key: [(Rs vs baseline, trades) per day]} for one family, days aligned. Returns (best key, best Rs per
    trade, luck line Rs per trade): the luck line is the LUCK_QUANTILE of the best per-trade mean across the family
    when every variant is centred on zero, resampling whole days."""
    keys = [k for k, v in daily.items() if sum(n for _, n in v)]
    if not keys:
        return None, None, None
    mean = {k: sum(x for x, _ in daily[k]) / sum(n for _, n in daily[k]) for k in keys}
    best = max(keys, key=lambda k: mean[k])
    days = len(daily[keys[0]])
    centred = {k: [(x - mean[k] * n, n) for x, n in daily[k]] for k in keys}
    rng, maxes = random.Random(seed), []
    for _ in range(BOOTSTRAP_RUNS):
        idx = [rng.randrange(days) for _ in range(days)]
        top = None
        for k in keys:
            n = sum(centred[k][i][1] for i in idx)
            if n:
                m = sum(centred[k][i][0] for i in idx) / n
                top = m if top is None else max(top, m)
        if top is not None:
            maxes.append(top)
    maxes.sort()
    line = maxes[min(len(maxes) - 1, int(LUCK_QUANTILE * len(maxes)))] if maxes else None
    return best, round(mean[best], 2), (round(line, 2) if line is not None else None)


def scoreboard(days):
    """[{family, best, label, trades, per_trade, luck, verdict}] from journal_days()."""
    per_day = [day_evidence(rows) for _, rows in days]
    families = {**{f: k for f, k in EXIT_FAMILIES.items()}, **ENTRY_FAMILIES, **FILTER_FAMILIES,
                SCORE_FAMILY: SCORE_VARIANTS}
    out = []
    for fam, keys in families.items():
        daily = {k: [tuple(ev[fam][k]) if k in ev.get(fam, {}) else (0.0, 0) for ev in per_day] for k in keys}
        best, per_trade, luck = luck_line(daily)
        if best is None:
            continue
        n = sum(t for _, t in daily[best])
        if n < MIN_TRADES:
            verdict = f"too early ({n}/{MIN_TRADES} trades)"
        elif per_trade is not None and luck is not None and per_trade > luck and per_trade > 0:
            verdict = "beating its baseline" + ("" if n >= PROMOTE_TRADES else f" (live use needs {PROMOTE_TRADES})")
        else:
            verdict = "could be luck"
        out.append({"family": fam, "best": best, "label": label(best), "trades": n, "per_trade": per_trade,
                    "luck": luck, "verdict": verdict})
    return out


def gross_by_setup(days):
    """Real intraday trades: average R before and after costs, by setup and overall."""
    agg = defaultdict(lambda: {"trades": 0, "gross_r": 0.0, "net_r": 0.0})
    for _, rows in days:
        fills = {r["proposal_id"]: r for r in rows if r["event"] == "filled" and r.get("proposal_id")}
        for r in rows:
            if r["event"] not in ("exited", "squared_off") or r.get("proposal_id") not in fills:
                continue
            f = fills[r["proposal_id"]]
            risk = float(f.get("risk_rs") or 0)
            if risk <= 0:
                continue
            costs = float(f.get("fees_rs") or 0) + float(r.get("fees_rs") or 0)
            net = float(r.get("pnl_rs") or 0) - float(f.get("fees_rs") or 0)
            for k in (f.get("setup") or "?", "All setups"):
                a = agg[k]
                a["trades"] += 1
                a["gross_r"] += (net + costs) / risk
                a["net_r"] += net / risk
    return {k: {"trades": v["trades"], "gross_r": round(v["gross_r"] / v["trades"], 2),
                "net_r": round(v["net_r"] / v["trades"], 2)} for k, v in agg.items() if v["trades"]}


def claude_vs_rules(days):
    """Claude's proposals (placed or skipped, real outcome) against the plain opening-range breakouts on the same
    days: Rs per trade for each, so the review can see whether Claude's choice adds anything."""
    claude, rules = defaultdict(lambda: [0.0, 0]), defaultdict(lambda: [0.0, 0])
    for _, rows in days:
        o = _outcomes(rows)
        proposed = {r["proposal_id"] for r in rows if r["event"] == "proposed" and r.get("proposal_id")}
        taken = {r["proposal_id"] for r in rows if r["event"] in ("exited", "squared_off") and r.get("proposal_id")}
        for pid in proposed & set(o):
            for k in ("all proposals", "placed" if pid in taken else "skipped"):
                claude[k][0] += o[pid]
                claude[k][1] += 1
        for r in {r["event_id"]: r for r in rows if r["event"] == "orb_shadow"}.values():
            res = exits.parse_note(r.get("note"))
            for k in ("orb5_2r", "orb15_2r"):
                if res.get(k) is not None:
                    rules[k][0] += res[k]
                    rules[k][1] += 1
    fmt = lambda d: {k: {"trades": n, "per_trade": round(x / n, 2)} for k, (x, n) in d.items() if n}  # noqa: E731
    return {"claude": fmt(claude), "rules": fmt(rules)}


def real_money_pace(days, today: date):
    """Real intraday trades since the reset, the pace, and the rough date the real-money bar's trade count arrives."""
    trades = sum(1 for _, rows in days for r in rows if r["event"] in ("exited", "squared_off"))
    trading_days = sum(1 for _, rows in days if any(r["event"] in ("exited", "squared_off", "proposed")
                                                   for r in rows))
    if not trades or not trading_days:
        return {"trades": trades, "eta": None}
    left_days = max(0, REAL_MONEY_TRADES - trades) / (trades / trading_days)
    d, n = today, 0
    while n < left_days:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return {"trades": trades, "per_day": round(trades / trading_days, 1), "eta": d.isoformat()}


def lines(today: date):
    """The plain scoreboard for the weekly report and /shadow."""
    days = journal_days(today)
    if not days:
        return []
    out = ["Scoreboard since 18 Nov (each family's best variant against its baseline; the luck line is what the "
           "best would show by chance):"]
    for s in scoreboard(days):
        out.append(f"- {s['family']}: {s['label']} {s['per_trade']:+.0f} Rs/trade over {s['trades']} "
                   f"(luck line {s['luck']:+.0f}): {s['verdict']}")
    g = gross_by_setup(days).get("All setups")
    if g:
        out.append(f"- Real trades: {g['gross_r']:+.2f}R a trade before costs, {g['net_r']:+.2f}R after "
                   f"({g['trades']} trades): costs are {g['gross_r'] - g['net_r']:.2f}R a trade.")
    cv = claude_vs_rules(days)
    c, r = cv["claude"].get("all proposals"), cv["rules"].get("orb15_2r") or cv["rules"].get("orb5_2r")
    if c and r:
        out.append(f"- Is Claude adding value? Its proposals {c['per_trade']:+.0f} Rs/trade ({c['trades']}) vs the "
                   f"plain 15-min breakout {r['per_trade']:+.0f} ({r['trades']}) on the same days.")
    p = real_money_pace(days, today)
    if p.get("eta"):
        out.append(f"- Real money needs {REAL_MONEY_TRADES} trades with average R above 0: {p['trades']} so far "
                   f"({p['per_day']} a day), so about {date.fromisoformat(p['eta']):%b %Y} at the earliest.")
    return out
