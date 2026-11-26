"""Council shadow (25 Nov 2026): after the close, judge every proposal of the day a second way. Paper only.

For each proposal, engine.council_keep saved the exact facts the live reviewer saw (council/<day>.json). After the
close this job asks, from those frozen facts:
  - a Tape reader (one Sonnet call: momentum, volume, how far the price has run, time left);
  - the base rate: this setup's past win rate from earlier days' results, pulled toward the overall rate when
    there are few trades (code, no model call; only days before today, so nothing from the future leaks in);
  - the control: the live reviewer's own prompt run 3 times and averaged, to tell a real council edge from
    plain averaging.
Council = the live reviewer + Tape reader + base rate: YES % is the middle one; it would take the trade when at
least two of them are at 50% or more.

Every proposal (placed, skipped, rejected, expired, approved-but-not-filled) is scored on the same paper fill:
the next minute's open plus slippage, then stop, target or the 15:10 square-off. So no method is helped by a
cleaner fill. Results: council/results.csv (one row per proposal, all days), council/scorecard.json and a short
Telegram summary. Scoring, fixed in advance (docs/DECISIONS.md section 18): Brier score on YES % (lower is
better), rupees if only its YES trades were taken, hit rate, and rupees on the proposals where the council and
the live reviewer disagree. Nothing here changes a live decision.

  python jobs/council_shadow.py [--day YYYY-MM-DD] [--no-send]
"""
import argparse
import os
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "trader"), os.path.join(HERE, "..")]

import config  # noqa: E402
import engine  # noqa: E402
import market  # noqa: E402
import reviewer  # noqa: E402
import storage  # noqa: E402
from http_client import log  # noqa: E402

RESULTS = "council/results.csv"
SCORECARD = "council/scorecard.json"
COLUMNS = ["day", "proposal_id", "symbol", "setup", "side", "hour", "decision", "real_take", "outcome_rs",
           "outcome_r", "win", "reviewer_yes", "tape_yes", "base_yes", "council_yes", "council_take", "control_yes",
           "tape_why"]
SHRINK = 10          # base rate: weight of the overall rate, in trades
CONTROL_RUNS = 3
TAPE_PROMPT = """You read the tape for a MOCK intraday trade on the Indian stock market (dummy money). Judge only
momentum, volume and how far the price has already run, and the time left in the day, from the facts below.
Don't invent news or prices.

FACTS
{facts}

Reply with JSON only. Keep "why" terse, at most 15 words, plain words.
{{"yes_pct": <0-100; 50 = coin flip>, "verdict": "YES" | "NO" | "UNSURE", "why": "<at most 15 words>"}}"""


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def base_rate(history, setup):
    """Shrunk win rate (%) of this setup over earlier days' results, or None with no history at all."""
    rows = [r for r in history if num(r.get("win")) is not None]
    if not rows:
        return None
    overall = sum(num(r["win"]) for r in rows) / len(rows)
    mine = [num(r["win"]) for r in rows if r.get("setup") == setup]
    return round((sum(mine) + SHRINK * overall) / (len(mine) + SHRINK) * 100, 1)


def council(reviewer_yes, tape_yes, base_yes):
    """(YES %, take?) from the members that answered; None with fewer than two."""
    ys = [y for y in (reviewer_yes, tape_yes, base_yes) if y is not None]
    if len(ys) < 2:
        return None, None
    return statistics.median(ys), sum(y >= 50 for y in ys) * 2 > len(ys)


def judge_one(pid, saved, prop, history, ask=reviewer.ask, outcome=engine._hypothetical):
    """One results row for a proposal (saved facts + the day's proposal record)."""
    facts = saved.get("facts") or ""
    p = {**saved, **{k: prop.get(k) for k in ("status", "user_reply") if prop}}
    pnl = outcome({"symbol": p["symbol"], "side": p["side"], "stop": p["stop"], "target": p["target"],
                   "qty": p["qty"], "sent_at": p["sent_at"]}, slip_pct=config.SLIPPAGE_PCT)
    risk = num(p.get("risk_rs")) or 0
    with ThreadPoolExecutor(max_workers=1 + CONTROL_RUNS) as ex:
        tape = ex.submit(ask, TAPE_PROMPT.format(facts=facts)) if facts else None
        ctrl = [ex.submit(ask, reviewer.PROMPT.format(facts=facts)) for _ in range(CONTROL_RUNS)] if facts else []
        tape = tape.result() if tape else None
        ctrl = [c.result() for c in ctrl]
    rev_yes = (p.get("reviewer") or {}).get("yes_pct")
    tape_yes = tape["yes_pct"] if tape else None
    base_yes = base_rate(history, p["setup"])
    c_yes, c_take = council(rev_yes, tape_yes, base_yes)
    ctrl_ys = [c["yes_pct"] for c in ctrl if c]
    decision = p.get("user_reply") or p.get("status") or ""
    return {"day": p["sent_at"][:10], "proposal_id": pid, "symbol": p["symbol"], "setup": p["setup"],
            "side": p["side"], "hour": p["sent_at"][11:13], "decision": decision,
            "real_take": int(decision in ("YES", "AUTO")), "outcome_rs": pnl,
            "outcome_r": round(pnl / risk, 2) if risk else "", "win": int(pnl > 0),
            "reviewer_yes": "" if rev_yes is None else rev_yes, "tape_yes": "" if tape_yes is None else tape_yes,
            "base_yes": "" if base_yes is None else base_yes, "council_yes": "" if c_yes is None else c_yes,
            "council_take": "" if c_take is None else int(c_take),
            "control_yes": round(sum(ctrl_ys) / len(ctrl_ys), 1) if ctrl_ys else "",
            "tape_why": (tape or {}).get("why", "")}


def scorecard(rows):
    """Each method on every judged proposal: Brier on YES % (0 best, 0.25 = coin flip), rupees if only its YES trades
    were taken, hit rate; plus the council vs the live reviewer where they disagree."""
    def method(take, yes=None):
        judged = [r for r in rows if take(r) is not None]
        if not judged:
            return {"n": 0}
        out = {"n": len(judged),
               "rupees": round(sum(num(r["outcome_rs"]) for r in judged if take(r)), 0),
               "hit_pct": round(sum(bool(take(r)) == bool(int(r["win"])) for r in judged) / len(judged) * 100, 1)}
        ys = [(num(yes(r)), int(r["win"])) for r in judged if yes and num(yes(r)) is not None]
        if ys:
            out["brier"] = round(sum((y / 100 - w) ** 2 for y, w in ys) / len(ys), 3)
        return out

    def yes_take(col):
        return lambda r: None if num(r[col]) is None else num(r[col]) >= 50
    card = {"proposals": len(rows),
            "real": method(lambda r: bool(int(r["real_take"]))),
            "reviewer": method(yes_take("reviewer_yes"), lambda r: r["reviewer_yes"]),
            "control": method(yes_take("control_yes"), lambda r: r["control_yes"]),
            "council": method(lambda r: None if r["council_take"] == "" else bool(int(r["council_take"])),
                              lambda r: r["council_yes"]),
            "tape": method(yes_take("tape_yes"), lambda r: r["tape_yes"]),
            "base": method(yes_take("base_yes"), lambda r: r["base_yes"])}
    dis = [r for r in rows if r["council_take"] != "" and num(r["reviewer_yes"]) is not None
           and bool(int(r["council_take"])) != (num(r["reviewer_yes"]) >= 50)]
    card["disagreements"] = {
        "n": len(dis),
        "council_rupees": round(sum(num(r["outcome_rs"]) for r in dis if int(r["council_take"])), 0),
        "reviewer_rupees": round(sum(num(r["outcome_rs"]) for r in dis if num(r["reviewer_yes"]) >= 50), 0)}
    return card


def message(card, today_n):
    def line(name, m):
        if not m.get("n"):
            return f"- {name}: no data"
        return (f"- {name}: {m['n']} judged, Rs {m['rupees']:+,.0f} on its YES trades, right {m['hit_pct']}%"
                + (f", Brier {m['brier']}" if "brier" in m else ""))
    d = card["disagreements"]
    return "\n".join([
        f"[MOCK] Council test (paper, after the close): {today_n} proposal(s) today, {card['proposals']} so far.",
        "All judged on the same paper fill. Brier: lower is better (0.25 = coin flip).",
        line("Real decisions", card["real"]), line("Live reviewer", card["reviewer"]),
        line("Council (reviewer + tape + base rate)", card["council"]),
        line("Control (reviewer x3 averaged)", card["control"]),
        f"Where the council and the reviewer disagree: {d['n']} proposals; council Rs {d['council_rupees']:+,.0f} vs "
        f"reviewer Rs {d['reviewer_rupees']:+,.0f}.",
        "Nothing live changes; judged at a review after 30+ disagreements."])


def run(day, send=True, ask=reviewer.ask, outcome=engine._hypothetical):
    saved = storage.get_json(f"council/{day}.json", {}) or {}
    props = (storage.get_json(storage.LEDGER_KEY, {}) or {}).get("proposals") or {}
    history = storage.read_rows(RESULTS)
    done = {r["proposal_id"] for r in history}
    earlier = [r for r in history if r["day"] < day]          # base rate: past days only
    new = []
    for pid, s in sorted(saved.items(), key=lambda kv: kv[1].get("sent_at", "")):
        if pid in done:
            continue
        try:
            row = judge_one(pid, s, props.get(pid) or {}, earlier, ask=ask, outcome=outcome)
        except Exception as err:  # noqa: BLE001 - one bad proposal never stops the rest
            log.warning("council: %s skipped: %s", pid, err)
            continue
        storage.append_row(RESULTS, COLUMNS, row)
        new.append(row)
    card = scorecard(storage.read_rows(RESULTS))
    card["as_of"] = day
    storage.put_json(SCORECARD, card)
    if send and new:
        import telegram_bot as tg

        tg.send(message(card, len(new)))
    return new, card


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=None)
    ap.add_argument("--no-send", action="store_true")
    a = ap.parse_args()
    d = a.day or market.last_session(market.now_ist()).strftime("%Y-%m-%d")   # right day even if run late
    new, card = run(d, send=not a.no_send)
    print(f"{len(new)} judged; scorecard: {card}")
