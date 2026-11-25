"""Council shadow (25 Nov): after-close judging of every proposal from the reviewer's frozen facts. Paper only."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "jobs"))

from helpers import Env  # noqa: E402

import council_shadow as cs  # noqa: E402
import storage  # noqa: E402


def saved(sent_at, setup="ORB long", rev=70, facts="Trade: BUY 10 INFY"):
    return {"sent_at": sent_at, "symbol": "INFY", "side": "BUY", "setup": setup, "entry": 100, "stop": 98,
            "target": 104, "qty": 10, "risk_rs": 20, "rr": 2, "facts": facts,
            "reviewer": {"yes_pct": rev, "verdict": "YES", "why": "x"}}


class CouncilShadow(unittest.TestCase):
    def test_base_rate_uses_only_history_and_shrinks(self):
        self.assertIsNone(cs.base_rate([], "ORB long"))
        hist = [{"setup": "ORB long", "win": "1"}] * 2 + [{"setup": "VWAP", "win": "0"}] * 8
        # overall 20%; ORB 2 of 2 pulled toward it: (2 + 10*0.2) / (2 + 10) = 33.3%
        self.assertEqual(cs.base_rate(hist, "ORB long"), 33.3)

    def test_council_needs_two_members_and_a_majority(self):
        self.assertEqual(cs.council(70, None, None), (None, None))
        self.assertEqual(cs.council(70, 40, 55), (55, True))
        self.assertEqual(cs.council(70, 40, 30), (40, False))

    def test_run_judges_each_proposal_once_on_the_same_fill_and_scores(self):
        prompts = []

        def ask(prompt):
            prompts.append(prompt)
            return {"yes_pct": 40 if "read the tape" in prompt else 60, "verdict": "NO", "why": "ran far"}

        fills = []

        def outcome(p, slip_pct=0.0):
            fills.append(slip_pct)
            return 40.0
        with Env():
            storage.put_json("council/2026-11-25.json", {"p1": saved("2026-11-25 10:05:00"),
                                                         "p2": saved("2026-11-25 11:05:00", rev=30)})
            storage.put_json(storage.LEDGER_KEY, {"proposals": {"p1": {"status": "approved", "user_reply": "AUTO"},
                                                                "p2": {"status": "rejected", "user_reply": "AUTO-NO"}}})
            new, card = cs.run("2026-11-25", send=False, ask=ask, outcome=outcome)
            self.assertEqual([r["proposal_id"] for r in new], ["p1", "p2"])
            self.assertTrue(all(s > 0 for s in fills))          # slippage paid on every proposal alike
            self.assertTrue(any("Trade: BUY 10 INFY" in p for p in prompts))   # frozen facts, not live ones
            p1 = new[0]
            self.assertEqual((p1["real_take"], p1["tape_yes"], p1["control_yes"], p1["base_yes"]), (1, 40, 60.0, ""))
            self.assertEqual((p1["council_yes"], p1["council_take"]), (55.0, 0))   # reviewer 70 + tape 40: 1 of 2
            self.assertEqual(card["proposals"], 2)
            self.assertEqual(card["real"]["rupees"], 40)
            self.assertIn("brier", card["reviewer"])
            again, _ = cs.run("2026-11-25", send=False, ask=ask, outcome=outcome)
            self.assertEqual(again, [])                          # never judged twice
            self.assertIn("Council test", cs.message(card, 2))

    def test_journal_appends_from_threads_lose_nothing(self):
        from concurrent.futures import ThreadPoolExecutor
        with Env():
            with ThreadPoolExecutor(8) as ex:
                list(ex.map(lambda i: storage.append_row("t.csv", ["i"], {"i": i}), range(40)))
            self.assertEqual(len(storage.read_rows("t.csv")), 40)


if __name__ == "__main__":
    unittest.main()
