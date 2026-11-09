"""The daily loss limit pauses new trades only while the day's loss is over it."""
import unittest
from unittest import mock

from helpers import Env

import config
import engine


def trade(env, st, symbol, entry, stop, target, exit_price, reason):
    env.prices[symbol] = entry
    r = engine.propose(st, symbol, "BUY", "ORB long", entry, stop, target, ["a"], "x")
    env.tap("yes", r["proposal_id"])
    engine.wait_decision(st, r["proposal_id"], 1)
    engine.execute(st, r["proposal_id"])
    return lambda: engine.close(st, symbol, exit_price, reason)


class LossLimitTest(unittest.TestCase):
    def setUp(self):  # the 2%-of-balance mode (Rs 2,000 on Rs 1 lakh)
        p = mock.patch.object(config, "DAILY_LOSS_LIMIT_RS", 0)
        p.start()
        self.addCleanup(p.stop)
        q = mock.patch.object(config, "MAX_POSITION_MARGIN_PCT", 100.0)  # full-size winner
        q.start()
        self.addCleanup(q.stop)

    def test_limit_blocks_then_lifts_after_a_winner(self):
        with Env() as env:
            st = engine.load()
            winner = trade(env, st, "TCS", 2470.0, 2463.0, 2484.0, 2484.0, "target")  # still open
            st["realized_today"] = -1900.0                                            # earlier losses
            trade(env, st, "RELIANCE", 1500.0, 1495.0, 1510.0, 1490.0, "stop_loss")()    # pushes it over
            self.assertTrue(st["loss_limit_hit"])
            self.assertTrue(any("Daily loss limit reached" in m for m in env.sent))
            self.assertIn("trading resumes by itself", env.sent[-1])
            blocked = engine.propose(st, "SBIN", "BUY", "ORB long", 800.0, 797.0, 806.0, ["a"], "x")
            self.assertEqual(blocked["status"], "blocked")
            self.assertIn("lifts by itself", blocked["reason"])

            winner()                                                                  # TCS hits target
            self.assertFalse(st["loss_limit_hit"])
            self.assertIn("Back under the daily loss limit", env.sent[-1])
            self.assertIn("New proposals are allowed again (8 of 10 trades left today)", env.sent[-1])
            env.prices["SBIN"] = 800.0
            again = engine.propose(st, "SBIN", "BUY", "ORB long", 800.0, 797.0, 806.0, ["a"], "x")
            self.assertEqual(again["status"], "pending")

    def test_no_repeat_messages(self):
        with Env() as env:
            st = engine.load()
            engine.check_loss_limit(st)
            self.assertEqual(env.sent, [])
            st["realized_today"] = -2500.0
            engine.check_loss_limit(st)
            engine.check_loss_limit(st)
            self.assertEqual(sum("Daily loss limit reached" in m for m in env.sent), 1)


if __name__ == "__main__":
    unittest.main()
