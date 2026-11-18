"""Background rule "stretched" (trader/stretch.py): breakouts that already ran a long way today, in the live checks
and the nightly backtest; and the opening-range test's wait-for-the-retest version."""
import unittest
from datetime import date, datetime

from helpers import Env

import advisor
import engine
import stretch
import weekly
from test_research import five_min


class CheckTest(unittest.TestCase):
    def test_far_off_the_low(self):
        hit, why = stretch.check("BUY", 103.0, 100.0, 103.2, 102.5, 4.0)    # 2.9% off the low = 73% of 4%
        self.assertTrue(hit)
        self.assertIn("off today's low", why)

    def test_far_above_vwap(self):
        hit, why = stretch.check("BUY", 103.0, 102.5, 103.2, 101.8, 10.0)
        self.assertTrue(hit)
        self.assertIn("above today's average price", why)

    def test_not_stretched_and_missing_inputs(self):
        self.assertFalse(stretch.check("BUY", 101.0, 100.0, 101.2, 100.6, 4.0)[0])
        self.assertFalse(stretch.check("BUY", 103.0, None, None, None, None)[0])
        self.assertFalse(stretch.check("BUY", None, 100.0, 103.0, 101.0, 4.0)[0])

    def test_sell_mirrors(self):
        self.assertTrue(stretch.check("SELL", 97.0, 96.8, 100.0, 98.2, 4.0)[0])   # 3.1% off the high, 1.2% below VWAP
        self.assertFalse(stretch.check("SELL", 99.5, 99.0, 100.0, 99.7, 4.0)[0])

    def test_usual_range(self):
        daily = [{"time": f"2026-11-{d:02d} 09:15", "high": 102.0, "low": 100.0} for d in range(9, 17)]
        self.assertAlmostEqual(stretch.usual_range_pct(daily, "2026-11-16"), 2.0)   # 16 Nov itself left out
        self.assertIsNone(stretch.usual_range_pct([], "2026-11-16"))


class LiveCheckTest(unittest.TestCase):
    def _assess(self, setup):
        with Env(datetime(2026, 11, 16, 10, 5)) as env:
            st = engine.load()
            env.prices["PRESTIGE"] = 1687.9
            # opened low (1,650), rallied through the opening range: 2.3% off the low
            env.bars[("PRESTIGE", "5m")] = five_min("2026-11-16", [(1655, 1660, 1650, 1658, 5000),
                                                                  (1658, 1675, 1656, 1672, 5000),
                                                                  (1672, 1686.8, 1670, 1684, 5000),
                                                                  (1684, 1690, 1680, 1687.9, 9000)])
            env.bars[("PRESTIGE", "1d")] = [{"time": f"2026-11-{d:02d} 09:15", "open": 1700, "high": 1720,
                                             "low": 1690, "close": 1700} for d in range(9, 16)]   # usual range 1.8%
            return advisor.assess(st, "PRESTIGE", "BUY", 1687.9, 1672.0, 1719.7, 2.0, setup=setup)

    def test_breakout_after_a_rally_is_flagged_without_points(self):
        a = self._assess("ORB long")
        self.assertIn("stretched", a["shadow"])
        self.assertTrue(any("Already stretched" in x for x in a["market_lines"]))
        b = self._assess("VWAP reclaim")                          # not a breakout: not judged
        self.assertNotIn("stretched", b["shadow"])
        self.assertEqual(a["checks_pct"], b["checks_pct"])        # no points either way

    def test_weekly_names_the_rule(self):
        self.assertIn("stretched", weekly.SHADOW_WORDS)


class RetestTest(unittest.TestCase):
    def day(self, after):
        rows = [(100, 100.6, 99.8, 100.4, 1)]                                      # 09:15 range 99.8-100.6
        rows += [(100.4, 100.7, 100.0, 100.5, 1), (100.5, 100.6, 100.1, 100.3, 1)]
        rows += [(100.3, 101.2, 100.2, 101.0, 1)]                                  # 09:30 breakout
        return five_min("2026-11-16", rows + after)

    def test_comes_back_then_runs(self):
        bars = self.day([(101.0, 101.1, 100.5, 100.8, 1)] + [(100.8, 103.5, 100.7, 103.4, 1)] * 6)
        with Env(datetime(2026, 11, 16, 15, 20)):
            t = engine.orb_trade(bars, 5, 1000.0, 100_000)
        self.assertGreater(t["retest"], 1500)               # filled at 100.6 on the dip, then +2R (same Rs risk)

    def test_retest_wins_when_the_rally_stalls(self):
        # dips to 100.5 (retest fill at 100.6), rallies to 102.5 (the retest's 2R, 102.41; short of the breakout's
        # 103.4), then fades through the breakout's stop at 99.8
        bars = self.day([(101.0, 101.1, 100.5, 100.8, 1), (100.8, 102.5, 100.7, 102.3, 1),
                         (102.3, 102.4, 100.5, 100.6, 1), (100.6, 100.7, 99.5, 99.6, 1)])
        with Env(datetime(2026, 11, 16, 15, 20)):
            t = engine.orb_trade(bars, 5, 1000.0, 100_000)
        self.assertGreater(t["retest"], 1500)
        self.assertLess(t["2r"], 0)

    def test_never_comes_back(self):
        bars = self.day([(101.0, 103.0, 100.9, 102.8, 1)] + [(102.8, 104.5, 102.5, 104.0, 1)] * 6)
        with Env(datetime(2026, 11, 16, 15, 20)):
            t = engine.orb_trade(bars, 5, 1000.0, 100_000)
        self.assertIsNone(t["retest"])
        self.assertGreater(t["2r"], 0)                     # buying the breakout caught it

    def test_falls_through_on_the_fill_candle_counts_the_loss(self):
        bars = self.day([(101.0, 101.1, 99.0, 99.2, 1)] + [(99.2, 99.5, 98.8, 99.0, 1)] * 3)
        with Env(datetime(2026, 11, 16, 15, 20)):
            t = engine.orb_trade(bars, 5, 1000.0, 100_000)
        self.assertLess(t["retest"], -900)                 # about -1R plus costs

    def test_report_and_week(self):
        with Env(datetime(2026, 11, 16, 15, 20)) as env:
            st = engine.load()
            st["in_play"] = [{"symbol": "TCS", "rvol": 3.0}]
            env.bars[("TCS", "5m")] = self.day([(101.0, 101.1, 100.5, 100.8, 1)]
                                               + [(100.8, 103.5, 100.7, 103.4, 1)] * 6)
            s = engine.report(st)
            self.assertEqual(s["orb_trial_retests"], {"orb5": 1, "orb15": 1})
            self.assertIn("Waiting for the retest", env.sent[-1])
            w = weekly.build(date(2026, 11, 16))
            self.assertEqual(w["orb_trial"]["retests"], {"orb5": 1, "orb15": 1})
            self.assertTrue(any("waiting for the retest" in x for x in weekly.paper_lines(w)))


if __name__ == "__main__":
    unittest.main()
