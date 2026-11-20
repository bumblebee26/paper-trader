"""Opening buy at 09:15 on the plan's news-radar and scanner picks (paper only, from 21 Nov)."""
import unittest
from datetime import date, datetime

from helpers import Env

import engine
import weekly
from test_research import five_min


class OpenBuyTest(unittest.TestCase):
    def test_gap_that_runs(self):
        bars = five_min("2026-11-16", [(100, 100.8, 99.6, 100.6, 1), (100.6, 102.6, 100.5, 102.5, 1),
                                       (102.5, 103.0, 102.2, 102.8, 1)])
        with Env(datetime(2026, 11, 16, 15, 20)):
            t = engine.open_buy_trade(bars, 1000.0, 100_000)
        self.assertGreater(t["2r"], 1500)                  # stop 98.8, target 102.4: about +2R less costs

    def test_gap_that_fades_hits_the_stop(self):
        bars = five_min("2026-11-16", [(100, 100.2, 99.5, 99.6, 1), (99.6, 99.7, 98.5, 98.6, 1)])
        with Env(datetime(2026, 11, 16, 15, 20)):
            t = engine.open_buy_trade(bars, 1000.0, 100_000)
        self.assertLess(t["2r"], -1000)                    # -1R plus double slippage and costs
        self.assertEqual(t["2r"], t["eod"])

    def test_needs_the_0915_candle(self):
        bars = five_min("2026-11-16", [(100, 101, 99, 100.5, 1)], start="09:20")
        self.assertIsNone(engine.open_buy_trade(bars, 1000.0, 100_000))

    def test_report_and_week(self):
        with Env(datetime(2026, 11, 16, 15, 20)) as env:
            st = engine.load()
            st["plan"] = {"news_radar": [{"symbol": "BSE"}], "scanner": {"strict": [{"symbol": "RADICO"}, {"symbol": "BSE"}]}}
            run = [(100, 100.8, 99.6, 100.6, 1), (100.6, 102.6, 100.5, 102.5, 1)]
            env.bars[("BSE", "5m")] = five_min("2026-11-16", run)
            env.bars[("RADICO", "5m")] = five_min("2026-11-16", run)
            s = engine.report(st)
            self.assertEqual((s["open_buy_stocks"], s["open_buy_trades"]), (2, 2))
            self.assertIn("Opening buy at 09:15", env.sent[-1])
            w = weekly.build(date(2026, 11, 16))
            self.assertEqual(w["orb_trial"]["trades"].get("open"), 2)
            self.assertTrue(any("Opening buy at 09:15" in x for x in weekly.paper_lines(w)))


if __name__ == "__main__":
    unittest.main()
