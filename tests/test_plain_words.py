"""Every proposal, fill and exit message explains itself in plain words."""
import unittest

from helpers import Env

import engine


class PlainWordsTest(unittest.TestCase):
    def test_proposal_fill_and_exit_have_plain_words(self):
        with Env() as env:
            env.prices["RELIANCE"] = 1452.0
            st = engine.load()
            r = engine.propose(st, "RELIANCE", "BUY", "ORB long", 1452.0, 1441.5, 1473.0,
                               ["broke OR high 1449.8 on 1.8x vol"], "5m close < 1446",
                               summary="Oil stocks are rising with the market today.")
            msg = env.full()
            self.assertIn("In plain words:", msg)
            self.assertIn("Buy 95 RELIANCE shares at about Rs 1,452.00", msg)
            self.assertIn("first 15-minute range", msg)
            self.assertIn("Oil stocks are rising with the market today.", msg)
            self.assertIn("you make about Rs 1,995", msg)   # 95 x 21
            self.assertIn("you lose about Rs 998", msg)     # 95 x 10.5
            self.assertIn("Details:", msg)

            env.tap("yes", r["proposal_id"])
            engine.wait_decision(st, r["proposal_id"], 1)
            engine.execute(st, r["proposal_id"])
            self.assertIn("Bought 95 RELIANCE @", env.sent[-1])

            engine.close(st, "RELIANCE", 1473.0, "target")
            self.assertIn("It reached the target price", env.sent[-1])
            self.assertIn("after fees", env.sent[-1])

    def test_sell_is_explained(self):
        with Env() as env:
            st = engine.load()
            engine.propose(st, "TCS", "SELL", "VWAP rejection", 3000.0, 3010.0, 2980.0, ["a"], "x")
            self.assertIn("buy them back later today (this profits if the price falls)", env.full())
            self.assertIn("the price rises to Rs 3,010.00", env.full())
            self.assertIn("SHORT TCS (bet on a fall)", env.sent[-1])
            self.assertIn("Sell 50 @ ~3,000.00 now, buy back by 15:10", env.sent[-1])


if __name__ == "__main__":
    unittest.main()
