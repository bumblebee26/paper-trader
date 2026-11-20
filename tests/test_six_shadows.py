"""Six paper tests from 21 Nov: auto-bar ladder, score-sized trades, no SELLs in an uptrend, gap fade, swing
holding period, paper fills vs the real minute. Nothing here places or changes a real trade."""
import unittest
from datetime import date, datetime

from helpers import Env

import advisor
import engine
import pools
import swing_hold
import weekly
from test_research import five_min


def propose_row(pid, pct, verdict="YES"):
    engine.journal("proposed", proposal_id=pid, symbol=f"S{pid}", setup="ORB long", side="BUY",
                   note=f"suggested {verdict} {pct}% | checks 60%")


class LadderAndSizing(unittest.TestCase):
    def test_week(self):
        with Env(datetime(2026, 11, 20, 16, 0)):
            for pid, pct, outcome, taken in (("a", 52, -300.0, False), ("b", 58, -200.0, False),
                                              ("c", 63, 400.0, True), ("d", 72, 500.0, True)):
                propose_row(pid, pct)
                if taken:
                    engine.journal("filled", proposal_id=pid, symbol=f"S{pid}", fees_rs=0)
                    engine.journal("exited", proposal_id=pid, symbol=f"S{pid}", setup="ORB long", side="BUY",
                                   pnl_rs=outcome)
                else:
                    engine.journal("passed_outcome", proposal_id=pid, symbol=f"S{pid}", pnl_rs=outcome)
            w = weekly.build(date(2026, 11, 20))
            self.assertEqual(w["bar_ladder"][50], {"trades": 4, "pnl": 400.0})
            self.assertEqual(w["bar_ladder"][60], {"trades": 2, "pnl": 900.0})
            self.assertEqual(w["bar_ladder"][70], {"trades": 1, "pnl": 500.0})
            self.assertEqual(w["score_sized"], {"trades": 3, "pnl": -100.0 + 400.0 + 750.0})
            text = "\n".join(weekly.paper_lines(w))
            self.assertIn("Auto bar ladder", text)
            self.assertIn("Sized by the suggestion", text)

    def test_sizes(self):
        self.assertEqual([weekly.size_for_score(p) for p in (54, 55, 59, 60, 69, 70, 90)],
                         [None, 0.5, 0.5, 1.0, 1.0, 1.5, 1.5])


class SellUptrend(unittest.TestCase):
    def test_flags_sells_in_an_uptrend_only(self):
        now = datetime(2026, 11, 20, 11, 0)
        self.assertIn("sell_uptrend", advisor.shadow_blocks("SELL", 0.5, "trending up", now, regime="uptrend"))
        self.assertNotIn("sell_uptrend", advisor.shadow_blocks("BUY", 0.5, "trending up", now, regime="uptrend"))
        self.assertNotIn("sell_uptrend", advisor.shadow_blocks("SELL", 0.5, "choppy", now, regime="neutral"))
        self.assertIn("sell_uptrend", weekly.SHADOW_WORDS)


class GapFade(unittest.TestCase):
    def day(self, after):
        first = [(103.5, 104.0, 103.0, 103.2, 1), (103.2, 103.6, 102.8, 103.0, 1), (103.0, 103.4, 102.9, 103.1, 1)]
        return five_min("2026-11-16", first + after)

    def test_gap_that_fades(self):
        bars = self.day([(103.1, 103.2, 102.5, 102.6, 1)] + [(102.6, 102.7, 99.5, 99.8, 1)] * 3)
        with Env(datetime(2026, 11, 16, 15, 20)):
            t = engine.gap_fade_trade(bars, 100.0, 1000.0, 100_000)
        self.assertGreater(t["2r"], 1000)                 # short at 102.6, stop 104.0, target 99.8

    def test_small_gap_or_no_break_is_no_trade(self):
        with Env(datetime(2026, 11, 16, 15, 20)):
            self.assertIsNone(engine.gap_fade_trade(self.day([(103.1, 103.5, 103.0, 103.3, 1)]), 101.0, 1000.0, 100_000))
            self.assertIsNone(engine.gap_fade_trade(self.day([(103.1, 103.5, 103.0, 103.3, 1)]), 100.0, 1000.0, 100_000))


class FillCheck(unittest.TestCase):
    def test_paper_fill_against_the_minute(self):
        bars = [{"time": "2026-11-20 09:35", "open": 100.0, "high": 100.3, "low": 99.9, "close": 100.1}]
        fc = engine.fill_check({"timestamp_ist": "2026-11-20 09:35:20", "side": "BUY", "fill_price": 100.15}, bars)
        self.assertEqual(fc, {"worse_bps": 5.0, "inside": 1})
        self.assertIsNone(engine.fill_check({"timestamp_ist": "2026-11-20 09:36:00", "side": "BUY",
                                             "fill_price": 100.0}, bars))

    def test_weekly_summary(self):
        with Env(datetime(2026, 11, 20, 16, 0)):
            engine.journal("fill_check", event_id="fillchk-a", proposal_id="a", note="worse_bps=5.0;inside=1")
            engine.journal("fill_check", event_id="fillchk-b", proposal_id="b", note="worse_bps=3.0;inside=0")
            w = weekly.build(date(2026, 11, 20))
            self.assertEqual(w["fill_check"], {"fills": 2, "avg_worse_bps": 4.0, "inside_pct": 50})


class SwingHold(unittest.TestCase):
    def test_shorter_holds(self):
        days = [f"2026-11-{d:02d}" for d in range(2, 21)]
        bars = [{"time": f"{d} 09:15", "open": 100 + i, "high": 101 + i, "low": 99 + i, "close": 100 + i}
                for i, d in enumerate(days)]
        trade = {"sym": "TCS", "day": "2026-11-03", "qty": 10, "cost": 1010.0, "sold": "2026-11-19", "pnl": 150.0}
        pnl5, ok5 = swing_hold.replay(trade, bars, 5)
        self.assertTrue(ok5)
        exit_px = round(106 * (1 - 0.05 / 100), 2)        # close of the 5th day after the buy
        self.assertAlmostEqual(pnl5, round(exit_px * 10 - pools.costs("SELL", exit_px * 10) - 1010.0, 2), places=2)
        quick = {**trade, "sold": "2026-11-05", "pnl": -40.0}
        self.assertEqual(swing_hold.replay(quick, bars, 5), (-40.0, True))     # really closed within 5 days
        young = {**trade, "day": "2026-11-18", "sold": None, "pnl": None}
        self.assertEqual(swing_hold.replay(young, bars, 5), (None, False))


if __name__ == "__main__":
    unittest.main()
