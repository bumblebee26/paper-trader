"""Intraday order charges: brokerage, STT on sells, exchange and SEBI fees, GST, stamp duty on buys."""
import unittest

import engine
import fees


class FeesTest(unittest.TestCase):
    def test_one_order_each_side(self):
        # Rs 37,500 buy: 20 + exchange 1.11 + SEBI 0.04 + GST 3.81 + stamp 1.13 = 26.08
        self.assertAlmostEqual(fees.order(37_500, "BUY"), 26.08, places=2)
        # same value sold: STT 9.38 instead of stamp
        self.assertAlmostEqual(fees.order(37_500, "SELL"), 34.33, places=2)

    def test_round_trip_is_the_same_for_a_short(self):
        self.assertAlmostEqual(fees.round_trip(37_500, 37_500, "BUY"), fees.round_trip(37_500, 37_500, "SELL"))
        self.assertAlmostEqual(fees.round_trip(37_500, 37_500, "BUY"), 60.41, places=2)   # vs Rs 40 before

    def test_cost_filter_counts_them(self):
        # 30 shares of Rs 1,250 with a Rs 10 stop: charges 60 + slippage 37.5 = ~33% of the Rs 300 risk
        self.assertGreater(engine.cost_share_pct(1250.0, 10.0, 30), 30)
        self.assertLess(engine.cost_share_pct(1250.0, 12.5, 24), 30)          # a 1% stop passes


if __name__ == "__main__":
    unittest.main()
