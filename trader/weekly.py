"""Friday weekly report (18:00 IST via the guardian): the week in plain words.

Built only from the journals, so it can be re-run for any week:
  results (P&L, win rate, fees), what worked (setups, sectors), your calls (taken vs skipped),
  how the suggestion and the reviewer did against what actually happened, what the shadow
  rules would have changed, and all pools.
"""
import re
from collections import defaultdict
from datetime import date, timedelta

import market
import pools
import sectors
import storage
import telegram_bot as tg

SUGGESTED = re.compile(r"suggested (YES|NO|UNSURE) (\d+)%")
REVIEWER = re.compile(r"reviewer (YES|NO|UNSURE) (\d+)%")
SHADOW = re.compile(r"shadow-block ([\w.,\-]+)")
SHADOW_WORDS = {
    "nifty_against_0.3": "no BUY when Nifty is down 0.3% (no SELL when up 0.3%)",
    "against_mood": "no trades against the market mood",
    "after_14h": "no new trades after 14:00",
}


def _inr(x, sign=False):
    import engine  # Indian digit grouping (Rs 1,00,000), same as every other message

    return engine.inr(x, sign=sign)


def _times(n):
    return "once" if n == 1 else f"{n} times"


def week_days(today: date):
    monday = today - timedelta(days=today.weekday())
    return [monday + timedelta(days=i) for i in range(5) if monday + timedelta(days=i) <= today]


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
    for pid, r in proposals.items():
        note = r.get("note") or ""
        outcome = taken.get(pid, passed.get(pid))
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

    return {
        "week_start": week_days(today)[0].isoformat(), "week_end": today.isoformat(),
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
    if w["shadow"]:
        lines += ["", "Rules being tested in the background (not used yet):"]
        for rule, s in sorted(w["shadow"].items()):
            effect = (f"would have avoided {s['trades']} trades worth {_inr(s['pnl'], sign=True)}"
                      + (": it would have helped." if s["pnl"] < 0 else ": it would have cost money."))
            lines.append(f"- {SHADOW_WORDS.get(rule, rule)}: {effect}")
    if w["cooloffs"] or w["loss_limit_days"]:
        lines.append(f"Brakes used: {w['cooloffs']} cool-off(s), loss limit on {w['loss_limit_days']} day(s).")

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


def run(intraday_state, send=True, today=None):
    today = today or market.now_ist().date()
    w = build(today)
    storage.put_json(f"reports/weekly/{today:%Y-%m-%d}.json", w)
    text = message(w, intraday_state)
    if send:
        tg.send(text)
    return {"week": w, "text": text}

