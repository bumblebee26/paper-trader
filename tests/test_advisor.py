"""Every proposal carries a market check and a suggested YES/NO split; the user still decides."""
import unittest
from datetime import datetime

from helpers import Env, minute_bars

import advisor
import engine


def trend(env, symbol, open_, close):
    env.bars[(symbol, "5m")] = minute_bars(datetime(2026, 11, 9, 9, 15), [(open_, open_, open_, open_),
                                                                           (close, close, close, close)])


class AdvisorTest(unittest.TestCase):
    def test_market_with_the_trade_suggests_yes(self):
        with Env() as env:
            trend(env, "^NSEI", 24000, 24150)      # +0.63%
            trend(env, "^BSESN", 79000, 79400)
            trend(env, "^NSEBANK", 52000, 52300)
            env.prices["HDFCBANK"] = 1601.0
            st = engine.load()
            r = engine.propose(st, "HDFCBANK", "BUY", "VWAP reclaim", 1600.0, 1592.0, 1616.0, ["a"], "x",
                               summary="Banks are leading the market up.", ai_yes_pct=70)
            msg = env.sent[-1]
            self.assertIn("My suggestion: YES", msg)
            self.assertIn("You decide.", msg)
            self.assertIn("Market check:", msg)
            self.assertIn("Nifty 50 is up 0.62% since the open, the same direction as this BUY", msg)
            self.assertIn("Sensex is up", msg)
            self.assertIn("Bank Nifty (banks) is up", msg)
            self.assertIn("still close to the entry", msg)
            self.assertIn("Banks are leading the market up.", msg)
            self.assertTrue(r["suggestion"].startswith("YES"))
            # 50 +15 +5 +10 +5 (near entry) +5 (rr 2) = 90, averaged with 70 -> 80
            self.assertEqual(st["proposals"][r["proposal_id"]]["suggested_yes_pct"], 80)

    def test_against_the_market_and_late_suggests_no(self):
        with Env() as env:
            env.at(14, 20)
            trend(env, "^NSEI", 24000, 23850)
            trend(env, "^BSESN", 79000, 78600)
            env.prices["RELIANCE"] = 1462.0        # 10 of 21 = 48%: late
            st = engine.load()
            st["plan"] = {"bias": "gap-down tendency"}
            r = engine.propose(st, "RELIANCE", "BUY", "ORB long", 1452.0, 1441.5, 1473.0, ["a"], "x")
            msg = env.sent[-1]
            self.assertIn("My suggestion: NO", msg)
            self.assertIn("against this BUY", msg)
            self.assertIn("entering late", msg)
            self.assertIn("Only 50 min left", msg)
            self.assertIn("goes against a BUY", msg)
            self.assertLessEqual(st["proposals"][r["proposal_id"]]["suggested_yes_pct"], 40)

    def test_missing_data_never_blocks(self):
        with Env() as env:
            st = engine.load()
            r = engine.propose(st, "TCS", "SELL", "VWAP rejection", 3000.0, 3010.0, 2980.0, ["a"], "x")
            self.assertEqual(r["status"], "pending")
            self.assertIn("Nifty 50: couldn't check right now.", env.sent[-1])
            self.assertIn("Nifty IT (IT companies): couldn't check right now.", env.sent[-1])

    def test_score_is_clamped(self):
        with Env():
            out = advisor.assess({}, "X", "BUY", 100, 99, 102, 2, ai_yes_pct=500)
            self.assertLessEqual(out["yes_pct"], 95)


if __name__ == "__main__":
    unittest.main()
