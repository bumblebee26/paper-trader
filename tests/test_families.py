"""Families and the scoreboard (trader/families.py): grouping, baselines, the luck line and the plain verdicts."""
import csv
import io
import unittest
from datetime import date, datetime

from helpers import Env

import exits
import families
import storage

COLS = ["timestamp_ist", "event", "event_id", "proposal_id", "symbol", "setup", "pnl_rs", "fees_rs", "risk_rs", "note"]


def put_day(day, rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLS)
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in COLS})
    storage.backend().put(f"journal/{day}.csv", buf.getvalue())


def trade(day, i, net, fees=60.0, risk=400.0, setup="ORB long", note="suggested YES 62%", exits_note=None):
    pid = f"{day}-{i}"
    rows = [{"timestamp_ist": f"{day} 10:00:00", "event": "proposed", "event_id": f"p-{pid}", "proposal_id": pid,
             "note": note},
            {"timestamp_ist": f"{day} 10:01:00", "event": "filled", "event_id": f"f-{pid}", "proposal_id": pid,
             "setup": setup, "fees_rs": fees / 2, "risk_rs": risk},
            {"timestamp_ist": f"{day} 11:00:00", "event": "exited", "event_id": f"x-{pid}", "proposal_id": pid,
             "pnl_rs": net + fees / 2, "fees_rs": fees / 2}]
    if exits_note:
        rows.append({"timestamp_ist": f"{day} 15:20:00", "event": "exit_variants", "event_id": f"ev-{pid}",
                     "proposal_id": pid, "note": exits.note_text(exits_note)})
    return rows


class LuckLineTest(unittest.TestCase):
    def test_noise_stays_under_the_line_a_real_edge_clears_it(self):
        noise = {"a": [(x, 1) for x in (10, -10, 5, -5) * 10], "b": [(x, 1) for x in (-8, 8, 3, -3) * 10]}
        best, per_trade, luck = families.luck_line(noise)
        self.assertLessEqual(per_trade, luck)
        edge = {"a": [(x + 30, 1) for x in (10, -10, 5, -5) * 10], "b": noise["b"]}
        best, per_trade, luck = families.luck_line(edge)
        self.assertEqual(best, "a")
        self.assertGreater(per_trade, luck)


class ScoreboardTest(unittest.TestCase):
    def test_exits_against_the_live_rule_and_plain_verdicts(self):
        with Env(datetime(2026, 11, 24, 16, 0)):
            put_day("2026-11-20", trade("2026-11-20", 1, -100, exits_note={"current": -120.0, "early_strict": -100.0,
                                                                           "trailing": 50.0, "trailing_early": 20.0}))
            days = families.journal_days(date(2026, 11, 24))
            ev = families.day_evidence(days[0][1])
            self.assertEqual(ev["Exit: at +1R"]["trailing_early"], [120.0, 1])   # vs the live baseline (-100)
            self.assertEqual(ev["Exit: early exit"]["current"], [-20.0, 1])
            board = {s["family"]: s for s in families.scoreboard(days)}
            self.assertTrue(board["Exit: at +1R"]["verdict"].startswith("too early"))

    def test_filters_score_gross_and_claude_vs_rules(self):
        with Env(datetime(2026, 11, 24, 16, 0)):
            rows = trade("2026-11-20", 1, -200, note="suggested YES 58% | shadow-block after_14h,stretched")
            rows += trade("2026-11-20", 2, 300, note="suggested YES 72% | shadow-block -")
            rows.append({"timestamp_ist": "2026-11-20 15:20:00", "event": "orb_shadow", "event_id": "orb-1",
                         "note": exits.note_text({"orb15_2r": -50.0, "orb5_2r": 10.0})})
            put_day("2026-11-20", rows)
            days = families.journal_days(date(2026, 11, 24))
            ev = families.day_evidence(days[0][1])
            self.assertEqual(ev["Filter: late entry"]["after_14h"], [200.0, 1])       # skipping a -200 trade
            self.assertEqual(ev[families.SCORE_FAMILY]["bar60"], [200.0, 2])           # drops the 58% loser
            g = families.gross_by_setup(days)["All setups"]
            self.assertEqual(g["trades"], 2)
            self.assertAlmostEqual(g["gross_r"] - g["net_r"], 0.15, places=1)                 # Rs 60 costs on Rs 400 risk
            cv = families.claude_vs_rules(days)
            self.assertEqual(cv["claude"]["all proposals"], {"trades": 2, "per_trade": 50.0})
            self.assertEqual(cv["rules"]["orb15_2r"], {"trades": 1, "per_trade": -50.0})
            text = "\n".join(families.lines(date(2026, 11, 24)))
            self.assertIn("Is Claude adding value?", text)
            self.assertIn("Real money needs 200 trades", text)
            self.assertIn("too early", text)


class LedgerTest(unittest.TestCase):
    def test_every_family_variant_is_in_the_ledger(self):
        import os
        path = os.path.join(os.path.dirname(__file__), "..", "docs", "VARIANTS.csv")
        with open(path, encoding="utf-8") as f:
            ledger = {r["variant"]: r for r in csv.DictReader(f)}
        keys = [k for ks in list(families.EXIT_FAMILIES.values()) + list(families.ENTRY_FAMILIES.values())
                + list(families.FILTER_FAMILIES.values()) for k in ks] + families.SCORE_VARIANTS
        self.assertEqual([k for k in keys if k not in ledger], [])
        self.assertEqual([k for k in exits.RULES if k not in ledger], [])
        self.assertTrue(all(r["lane"] in ("exploring", "candidate", "dead", "baseline", "live") for r in ledger.values()))


if __name__ == "__main__":
    unittest.main()
