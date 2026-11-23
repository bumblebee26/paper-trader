"""Friday weekly report (18:00 IST via the guardian): the week in plain words.

Built only from the journals, so it can be re-run for any week:
  results (P&L, win rate, fees), what worked (setups, sectors), your calls (taken vs skipped),
  how the suggestion and the reviewer did against what actually happened, what the shadow
  rules would have changed, and all pools.
"""
import re
from collections import defaultdict
from datetime import date, timedelta

import config
import exits
import market
import pools
import sectors
import storage
import telegram_bot as tg
from http_client import log

SUGGESTED = re.compile(r"suggested (YES|NO|UNSURE) (\d+)%")
REVIEWER = re.compile(r"reviewer (YES|NO|UNSURE) (\d+)%")
SHADOW = re.compile(r"shadow-block ([\w.,\-]+)")
BAR_LADDER = (50, 55, 60, 65, 70)


def scored_proposals(rows):
    """[(suggestion %, outcome Rs)] for every proposal in rows with a known outcome: placed (the real result,
    entry fee included) or skipped (what it would have made)."""
    entry_fee = {r["proposal_id"]: float(r.get("fees_rs") or 0) for r in rows
                 if r["event"] == "filled" and r.get("proposal_id")}
    taken = {r["proposal_id"]: float(r.get("pnl_rs") or 0) - entry_fee.get(r["proposal_id"], 0)
             for r in rows if r["event"] in ("exited", "squared_off") and r.get("proposal_id")}
    passed = {r["proposal_id"]: float(r.get("pnl_rs") or 0) for r in rows
              if r["event"] == "passed_outcome" and r.get("proposal_id")}
    out = []
    for r in rows:
        if r["event"] != "proposed" or not r.get("proposal_id"):
            continue
        m = SUGGESTED.search(r.get("note") or "")
        outcome = taken.get(r["proposal_id"], passed.get(r["proposal_id"]))
        if m and outcome is not None:
            out.append((int(m.group(2)), outcome))
    return out


def size_for_score(pct):
    """Score-sized trades on paper: 0.5x risk at 55-59%, 1x at 60-69%, 1.5x at 70%+; None below 55%."""
    if pct < 55:
        return None
    return 0.5 if pct < 60 else 1.0 if pct < 70 else 1.5


def selector_week(rows):
    """The selector's daily scores this week (selector rows from the day report)."""
    days = {r["event_id"]: exits.parse_note(r.get("note")) for r in rows if r["event"] == "selector"}
    fams = {}
    for f in ("exit", "bar", "entry"):
        fams[f] = {"rs": round(sum(d.get(f"{f}_rs") or 0 for d in days.values()), 2),
                   "fixed": round(sum(d.get(f"{f}_fixed_rs") or 0 for d in days.values()), 2)}
    return {"days": len(days), "families": fams}


def fill_check(rows):
    """Paper fills against the real 1-minute candle of the fill minute (fill_check rows from the day report):
    how far the paper fill was from that minute's close, against the trader (positive = paper was worse)."""
    checks = {r["event_id"]: r for r in rows if r["event"] == "fill_check"}
    vals = [exits.parse_note(r.get("note")) for r in checks.values()]
    worse = [v["worse_bps"] for v in vals if v.get("worse_bps") is not None]
    inside = [v for v in vals if v.get("inside") is not None]
    return {"fills": len(worse), "avg_worse_bps": round(sum(worse) / len(worse), 1) if worse else None,
            "inside_pct": round(100 * sum(v["inside"] for v in inside) / len(inside)) if inside else None}


def swing_hold_safe(today):
    try:
        import swing_hold
        return swing_hold.analyse(today)
    except Exception as err:  # noqa: BLE001 - a paper study never breaks the weekly report
        log.warning("swing holding-period study failed: %s", err)
        return {}


SHADOW_WORDS = {
    "nifty_against_0.3": "no BUY when Nifty is down 0.3% (no SELL when up 0.3%)",
    "against_mood": "no trades against the market mood",
    "after_14h": "no new trades after 14:00",
    "vwap_wrong_side": "no BUY below today's average price, VWAP (no SELL above it)",
    "lagging_sector": "no BUY on a stock weaker than its sector today (no SELL on a stronger one)",
    "sell_uptrend": "no SELL all day while the Nifty 50 is in an uptrend (above its 20/50/200-day averages)",
    "stretched": "no breakout once the price is already 70% of its usual day's range off today's low "
                 "(high for a SELL) or 1% past VWAP",
}


def _inr(x, sign=False):
    import engine  # Indian digit grouping (Rs 1,00,000), same as every other message

    return engine.inr(x, sign=sign)


def _times(n):
    return "once" if n == 1 else f"{n} times"


def week_days(today: date):
    """This week's weekdays up to today, from the ledger start (the 17 Nov 2026 reset) at the earliest."""
    monday = today - timedelta(days=today.weekday())
    start = date.fromisoformat(config.LEDGER_START)
    return [d for d in (monday + timedelta(days=i) for i in range(5)) if start <= d <= today]


def build(today: date):
    rows = []
    for d in week_days(today):
        rows += storage.read_rows(f"journal/{d:%Y-%m-%d}.csv")
    closed = [r for r in rows if r["event"] in ("exited", "squared_off")]
    # An exit's pnl_rs already has the exit fees; the entry fee is booked at the fill, so add it back
    # here to get each trade's full result (matches the change in the ledger balance).
    entry_fee = {r["proposal_id"]: float(r.get("fees_rs") or 0) for r in rows
                 if r["event"] == "filled" and r.get("proposal_id")}
    pnl_of = lambda r: (float(r.get("pnl_rs") or 0)  # noqa: E731
                        - (entry_fee.get(r.get("proposal_id"), 0) if r["event"] in ("exited", "squared_off") else 0))
    fees = sum(float(r.get("fees_rs") or 0) for r in rows if r["event"] in ("filled", "exited", "squared_off"))
    wins = [pnl_of(r) for r in closed if pnl_of(r) > 0]
    losses = [pnl_of(r) for r in closed if pnl_of(r) <= 0]

    proposals = {r["proposal_id"]: r for r in rows if r["event"] == "proposed" and r.get("proposal_id")}
    taken = {r["proposal_id"]: pnl_of(r) for r in closed if r.get("proposal_id")}
    passed = {r["proposal_id"]: pnl_of(r) for r in rows if r["event"] == "passed_outcome" and r.get("proposal_id")}

    by_setup, by_sector = defaultdict(float), defaultdict(float)
    for r in closed:
        by_setup[f"{r['setup']} {r['side']}"] += pnl_of(r)
        by_sector[sectors.sector(r["symbol"])] += pnl_of(r)

    # Suggestion, reviewer and shadow rules against the outcome of each proposal
    agree = against = 0
    against_outcome = 0.0
    reviewer_no = []
    shadow = defaultdict(list)
    by_source = defaultdict(lambda: {"proposals": 0, "outcomes": 0, "wins": 0, "pnl": 0.0})
    for pid, r in proposals.items():
        note = r.get("note") or ""
        outcome = taken.get(pid, passed.get(pid))
        src = by_source["news" if "source news" in note else "watchlist"]
        src["proposals"] += 1
        if outcome is not None:
            src["outcomes"] += 1
            src["wins"] += outcome > 0
            src["pnl"] += outcome
        you = "YES" if pid in taken else ("NO" if pid in passed else None)
        m = SUGGESTED.search(note)
        if m and you and m.group(1) != "UNSURE":
            if m.group(1) == you:
                agree += 1
            else:
                against += 1
                against_outcome += (outcome or 0) if you == "YES" else -(outcome or 0)
        rv = REVIEWER.search(note)
        if rv and rv.group(1) == "NO" and outcome is not None:
            reviewer_no.append(outcome)
        sh = SHADOW.search(note)
        if sh and pid in taken:
            for rule in sh.group(1).split(","):
                if rule and rule != "-":
                    shadow[rule].append(taken[pid])

    # Auto-bar ladder and score-sized trades (paper, from 21 Nov): every proposal with a known outcome (placed:
    # the real result; skipped: what it would have made), at each bar, and sized by its suggestion.
    scored = scored_proposals(rows)
    ladder = {b: {"trades": sum(1 for pct, _ in scored if pct > b),
                  "pnl": round(sum(o for pct, o in scored if pct > b), 2)} for b in BAR_LADDER}
    sized = [(size_for_score(pct), o) for pct, o in scored if size_for_score(pct)]
    score_sized = {"trades": len(sized), "pnl": round(sum(mult * o for mult, o in sized), 2)}

    # Exit rules on trial: one row per trade (fixed event_id; a re-run report repeats it, so keep the last)
    # Each rule is compared with the current rule on the trades it has a result for: rules added later (e.g.
    # target_1_5r from 19 Nov) are missing on older rows, and those rows still count for the other rules.
    variants = {r["event_id"]: r for r in rows if r["event"] == "exit_variants"}
    exit_totals, exit_trades, exit_actual = {k: 0.0 for k in exits.RULES}, 0, 0.0
    exit_counts, exit_base = {k: 0 for k in exits.RULES}, {k: 0.0 for k in exits.RULES}
    for r in variants.values():
        res = exits.parse_note(r.get("note"))
        if res.get("current") is None:
            continue
        exit_trades += 1
        exit_actual += float(r.get("pnl_rs") or 0)
        for k in exits.RULES:
            if res.get(k) is not None:
                exit_totals[k] += res[k]
                exit_counts[k] += 1
                exit_base[k] += res["current"]
    orb_rows = {r["event_id"]: r for r in rows if r["event"] == "orb_shadow"}
    orb = {"stocks": 0, "rs": defaultdict(float), "trades": defaultdict(int), "retests": defaultdict(int)}
    for r in orb_rows.values():
        orb["stocks"] += 1
        for k, v in exits.parse_note(r.get("note")).items():
            if v is not None:
                orb["rs"][k] += v
                if k.endswith("_2r"):
                    orb["trades"][k[:-3]] += 1
                elif k.endswith("_retest"):
                    orb["retests"][k[:-7]] += 1
    locks = {r["event_id"]: r for r in rows if r["event"] == "profit_lock_shadow"}
    lock_change = 0.0
    for r in locks.values():
        actual = float((r.get("note") or "").rsplit("actual ", 1)[-1] or 0)
        lock_change += float(r.get("pnl_rs") or 0) - actual

    return {
        "exit_rules": {"trades": exit_trades, "actual": round(exit_actual, 2),
                       "totals": {k: round(v, 2) for k, v in exit_totals.items()},
                       "counts": exit_counts, "base": {k: round(v, 2) for k, v in exit_base.items()}},
        "profit_lock": {"days": len(locks), "change": round(lock_change, 2)},
        "bar_ladder": ladder, "score_sized": score_sized, "selector": selector_week(rows),
        "fill_check": fill_check(rows),
        "swing_hold": swing_hold_safe(today),
        "orb_trial": {"stocks": orb["stocks"], "trades": dict(orb["trades"]), "retests": dict(orb["retests"]),
                      "rs": {k: round(v, 2) for k, v in orb["rs"].items()}},
        "weekly_brake": any(r["event"] == "weekly_brake" for r in rows),
        "week_start": (week_days(today) or [today])[0].isoformat(), "week_end": today.isoformat(),
        "trades": len(closed), "net_pnl": round(sum(pnl_of(r) for r in closed), 2), "fees": round(fees, 2),
        "wins": len(wins), "win_rate": round(len(wins) / len(closed) * 100) if closed else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "by_setup": dict(by_setup), "by_sector": dict(by_sector),
        "taken": len(taken), "taken_pnl": round(sum(taken.values()), 2),
        "passed": len(passed), "passed_pnl": round(sum(passed.values()), 2),
        "agree": agree, "against": against, "against_result": round(against_outcome, 2),
        "reviewer_no": len(reviewer_no), "reviewer_no_pnl": round(sum(reviewer_no), 2),
        "shadow": {k: {"trades": len(v), "pnl": round(sum(v), 2)} for k, v in shadow.items()},
        "by_source": {k: {**v, "pnl": round(v["pnl"], 2)} for k, v in by_source.items()},
        "cooloffs": sum(1 for r in rows if r["event"] == "cooloff"),
        "loss_limit_days": len({r["timestamp_ist"][:10] for r in rows if r["event"] == "loss_limit_hit"}),
    }


def message(w, intraday_state):
    lines = [f"[MOCK] Weekly report, {date.fromisoformat(w['week_start']):%d %b} to "
             f"{date.fromisoformat(w['week_end']):%d %b}", ""]
    if not w["trades"]:
        lines.append("No intraday trades closed this week.")
    else:
        lines += [
            f"Intraday: {w['trades']} trades, {_inr(w['net_pnl'], sign=True)} after fees of {_inr(w['fees'])}. "
            f"{w['wins']} won ({w['win_rate']}%).",
            f"Average win {_inr(w['avg_win'] or 0)}, average loss {_inr(abs(w['avg_loss'] or 0))}"
            + (" (wins bigger than losses: good)." if (w["avg_win"] or 0) > abs(w["avg_loss"] or 0)
               else " (losses bigger than wins: the stops or targets need a look)."),
        ]
        best = max(w["by_setup"].items(), key=lambda kv: kv[1])
        worst = min(w["by_setup"].items(), key=lambda kv: kv[1])
        lines += ["", "What worked and what didn't:"]
        if best[0] == worst[0]:
            lines.append(f"- Only one setup traded: {best[0]} ({_inr(best[1], sign=True)})")
        else:
            lines += [f"- Best setup: {best[0]} ({_inr(best[1], sign=True)})",
                      f"- Worst setup: {worst[0]} ({_inr(worst[1], sign=True)})"]
        if len(w["by_sector"]) > 1:
            bs = max(w["by_sector"].items(), key=lambda kv: kv[1])
            ws = min(w["by_sector"].items(), key=lambda kv: kv[1])
            lines.append(f"- Best sector: {bs[0]} ({_inr(bs[1], sign=True)}); worst: {ws[0]} ({_inr(ws[1], sign=True)})")

    lines += ["", "Your calls:"]
    lines.append(f"- You took {w['taken']} proposals ({_inr(w['taken_pnl'], sign=True)}) and skipped {w['passed']}"
                 + (f", which would have made {_inr(w['passed_pnl'], sign=True)}." if w["passed"] else "."))
    if w["passed"] and w["passed_pnl"] < 0:
        lines.append("  Skipping saved you money.")
    elif w["passed"] and w["passed_pnl"] > 0:
        lines.append("  The skipped ones would have made money: worth a look at why you passed.")
    if w["agree"] or w["against"]:
        verdict = ("going against it paid off" if w["against_result"] > 0 else
                   "going against it cost you" if w["against_result"] < 0 else "it made no difference")
        lines.append(f"- You followed the suggestion {_times(w['agree'])} and went against it {_times(w['against'])}"
                     + (f"; {verdict} ({_inr(w['against_result'], sign=True)})." if w["against"] else "."))
    if w["reviewer_no"]:
        lines.append(f"- The reviewer said NO {_times(w['reviewer_no'])}; those proposals made "
                     f"{_inr(w['reviewer_no_pnl'], sign=True)}"
                     + (" (the reviewer was right)." if w["reviewer_no_pnl"] < 0 else "."))
    import swing_shadow
    import swing_tech

    paper = (paper_lines(w) + [f"- {x}" for x in swing_shadow.lines(swing_shadow.summary())]
             + [f"- {x}" for x in swing_tech.lines(swing_tech.summary())])
    import swing_momentum

    paper += [f"- {x}" for x in swing_momentum.all_lines()]
    import model_report

    paper += [f"- {x}" for x in model_report.lines(days=5)]
    import families

    try:
        board = families.lines(date.fromisoformat(w["week_end"]))
    except Exception as err:  # noqa: BLE001 - the scoreboard must never stop the weekly report
        board = [f"Scoreboard unavailable ({err})."]
    if board:
        lines += ["", *board]
    lines += ["", "Tested on paper (not used for real trades yet; /shadow any time):", *paper]
    if w["cooloffs"] or w["loss_limit_days"] or w.get("weekly_brake"):
        lines.append(f"Brakes used: {w['cooloffs']} cool-off(s), loss limit on {w['loss_limit_days']} day(s)"
                     + (", weekly brake (half-size trades)." if w.get("weekly_brake") else "."))

    lines += ["", "All pools:"]
    intraday = intraday_state["balance"]
    lines.append(f"- Intraday {_inr(intraday)} (started {_inr(intraday_state['starting_capital'])}, "
                 f"{_inr(intraday - intraday_state['starting_capital'], sign=True)})")
    total, start = intraday, intraday_state["starting_capital"]
    for label, eq, cap, state in pools.summary_lines(intraday_state):
        lines.append(f"- {label} {_inr(eq)}: {state}")
        total, start = total + eq, start + cap
    lines.append(f"- Total {_inr(total)} of {_inr(start)} ({_inr(total - start, sign=True)})")
    return "\n".join(lines)


def paper_lines(w) -> list:
    """Everything tested on paper this week, one line each (weekly report and /shadow)."""
    lines = []
    for rule, s in sorted(w["shadow"].items()):
        effect = (f"would have avoided {s['trades']} trades worth {_inr(s['pnl'], sign=True)}"
                  + (": it would have helped." if s["pnl"] < 0 else ": it would have cost money."))
        lines.append(f"- Background rule, {SHADOW_WORDS.get(rule, rule)}: {effect}")
    ex = w.get("exit_rules") or {}
    if ex.get("trades"):
        base = ex["totals"]["current"]
        parts = []
        for k in exits.RULES[1:]:
            n = (ex.get("counts") or {}).get(k, ex["trades"])
            if not n:
                continue
            diff = ex["totals"][k] - (ex.get("base") or {}).get(k, base)
            parts.append(f"{exits.RULE_WORDS[k]} {_inr(diff, sign=True)}" + (f" ({n} trades)" if n < ex["trades"] else ""))
        lines.append(f"- Exit rules on trial ({ex['trades']} trades; fixed 2R target {_inr(base, sign=True)}): "
                     + ", ".join(parts) + ". Decided after 15+ trades; one week is mostly noise.")
    lock = w.get("profit_lock") or {}
    if lock.get("days"):
        lines.append(f"- Profit lock: armed on {lock['days']} day(s); it would have changed the result by "
                     f"{_inr(lock['change'], sign=True)}.")
    o = w.get("orb_trial") or {}
    if o.get("trades"):
        rs, n = o["rs"], o["trades"]
        def nearer(v):  # the 1.5R and yesterday's-level targets (from 19 Nov; missing on older rows)
            if f"{v}_1_5r" not in rs:
                return ""
            return (f" / {_inr(rs[f'{v}_1_5r'], sign=True)} 1.5R / {_inr(rs.get(f'{v}_level', 0), sign=True)} "
                    "at yesterday's high-low")
        lines.append(f"- Opening-range test on stocks in play: 5-min range {n.get('orb5', 0)} trades, "
                     f"{_inr(rs.get('orb5_2r', 0), sign=True)} with a 2R target{nearer('orb5')} / "
                     f"{_inr(rs.get('orb5_eod', 0), sign=True)} held to 15:10; 15-min range {n.get('orb15', 0)} "
                     f"trades, {_inr(rs.get('orb15_2r', 0), sign=True)}{nearer('orb15')} / "
                     f"{_inr(rs.get('orb15_eod', 0), sign=True)}.")
        if (o.get("trades") or {}).get("open"):
            lines.append(f"- Opening buy at 09:15 on the plan's news and scanner picks: {o['trades']['open']} trades, "
                         f"{_inr(rs.get('open_2r', 0), sign=True)} with a 2R target / "
                         f"{_inr(rs.get('open_eod', 0), sign=True)} held to 15:10.")
        if (o.get("trades") or {}).get("fade"):
            lines.append(f"- Gap fade (short a 3%+ gap-up that breaks its first 15 minutes' low): "
                         f"{o['trades']['fade']} trades, {_inr(rs.get('fade_2r', 0), sign=True)} with a 2R target / "
                         f"{_inr(rs.get('fade_eod', 0), sign=True)} held to 15:10.")
        rt = o.get("retests") or {}
        if rt:
            lines.append(f"- Opening-range test, waiting for the retest instead of buying the breakout: 5-min range "
                         f"{rt.get('orb5', 0)} filled, {_inr(rs.get('orb5_retest', 0), sign=True)}; 15-min range "
                         f"{rt.get('orb15', 0)} filled, {_inr(rs.get('orb15_retest', 0), sign=True)} (vs "
                         f"{_inr(rs.get('orb5_2r', 0), sign=True)} / {_inr(rs.get('orb15_2r', 0), sign=True)} "
                         "buying every breakout).")
    sel = w.get("selector") or {}
    if sel.get("days"):
        lines.append("- Selector (picks tomorrow's version from the last 20 days): " + "; ".join(
            f"{f} {_inr(v['rs'], sign=True)} vs fixed {_inr(v['fixed'], sign=True)}"
            for f, v in sel["families"].items()) + f" over {sel['days']} days.")
    lad = w.get("bar_ladder") or {}
    if any(v["trades"] for v in lad.values()):
        parts = [f"over {b}%: {v['trades']} trades {_inr(v['pnl'], sign=True)}" for b, v in sorted(lad.items(),
                 key=lambda kv: int(kv[0]))]
        lines.append(f"- Auto bar ladder (every proposal, placed or skipped): {'; '.join(parts)}.")
    ss = w.get("score_sized") or {}
    if ss.get("trades"):
        lines.append(f"- Sized by the suggestion (0.5x at 55-59%, 1x at 60-69%, 1.5x at 70%+): {ss['trades']} trades, "
                     f"{_inr(ss['pnl'], sign=True)}.")
    fc = w.get("fill_check") or {}
    if fc.get("fills"):
        lines.append(f"- Paper fills vs the real minute: {fc['fills']} fills, on average {fc['avg_worse_bps']} bps "
                     f"worse than that minute's close (positive = paper is the stricter one); "
                     f"{fc['inside_pct']}% inside the minute's high-low.")
    sh = w.get("swing_hold") or {}
    for pool, v in sorted(sh.items()):
        if v.get("trades"):
            lines.append(f"- {v['label']}: {v['trades']} trades, actual {_inr(v['actual'], sign=True)}; sold after "
                         f"5 days {_inr(v['hold_5'], sign=True)}, after 10 days {_inr(v['hold_10'], sign=True)}.")
    if w.get("passed"):
        lines.append(f"- Skipped proposals ({w['passed']}) would have made {_inr(w['passed_pnl'], sign=True)}"
                     + (": skipping saved money." if w["passed_pnl"] < 0 else "."))
    src = w.get("by_source") or {}
    if src.get("news"):
        def part(k):
            s = src.get(k) or {"proposals": 0, "outcomes": 0, "wins": 0, "pnl": 0.0}
            won = f", won {s['wins']} of {s['outcomes']}" if s["outcomes"] else ""
            return f"{k} picks {s['proposals']}{won}, {_inr(s['pnl'], sign=True)}"
        lines.append(f"- News vs watchlist (taken or would-have): {part('news')}; {part('watchlist')}.")
    return lines


def run(intraday_state, send=True, today=None):
    today = today or market.now_ist().date()
    w = build(today)
    storage.put_json(f"reports/weekly/{today:%Y-%m-%d}.json", w)
    text = message(w, intraday_state)
    if send:
        tg.send(text)
    return {"week": w, "text": text}

