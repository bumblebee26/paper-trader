"""Stocks in play (opening relative volume), VWAP and relative-strength checks, and the opening-range paper test."""
import unittest
from datetime import date, datetime, timedelta

from helpers import Env

import advisor
import engine
import storage
import weekly


def five_min(day, rows, start="09:15"):
    """5-minute candles from `start` on `day`: rows of (open, high, low, close, volume)."""
    t = datetime.strptime(f"{day} {start}", "%Y-%m-%d %H:%M")
    return [{"time": (t + timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M"), "open": o, "high": h, "low": lo,
             "close": c, "volume": v} for i, (o, h, lo, c, v) in enumerate(rows)]


def history(today_vol, usual_vol=1000, days=10, close=100.0):
    out = []
    for i in range(days, 0, -1):
        d = (date(2026, 11, 16) - timedelta(days=i)).isoformat()
        out += five_min(d, [(close, close + 1, close - 1, close, usual_vol)] * 3)
    return out + five_min("2026-11-16", [(close * 1.02, close * 1.03, close * 1.01, close * 1.025, today_vol)] * 2)


class StocksInPlayTest(unittest.TestCase):
    def test_opening_rvol_and_gap(self):
        r = engine.opening_rvol("X", history(today_vol=3000))
        self.assertEqual(r["rvol"], 3.0)                         # 6,000 in the first 10 minutes vs usually 2,000
        self.assertEqual(r["gap_pct"], 2.0)

    def test_focus_becomes_the_most_active_names(self):
        with Env(datetime(2026, 11, 16, 9, 26)) as env:
            st = engine.load()
            engine.start_day(st, {}, ["HDFCBANK", "TCS"])
            env.bars[("TCS", "5m")] = history(today_vol=1500)
            env.bars[("SBIN", "5m")] = history(today_vol=5000)
            env.bars[("INFY", "5m")] = history(today_vol=900)
            top = engine.rank_in_play(st)
            self.assertEqual([r["symbol"] for r in top], ["SBIN", "TCS", "INFY"])
            self.assertEqual(st["focus"][:3], ["SBIN", "TCS", "INFY"])
            self.assertEqual(engine.rank_in_play(st), [])          # once a day
            self.assertTrue(any(r["event"] == "in_play" for r in storage.read_rows("journal/2026-11-16.csv")))


class VwapStrengthTest(unittest.TestCase):
    def test_buy_below_vwap_and_weaker_than_its_sector_is_marked_down(self):
        with Env(datetime(2026, 11, 16, 11, 0)) as env:
            st = engine.load()
            env.prices["HDFCBANK"] = 1580.0
            env.bars[("HDFCBANK", "5m")] = five_min("2026-11-16", [(1600, 1605, 1595, 1600, 5000),
                                                                  (1600, 1601, 1578, 1580, 5000)])
            env.bars[("^NSEBANK", "5m")] = five_min("2026-11-16", [(50000, 50100, 49990, 50050, 1),
                                                                  (50050, 50300, 50040, 50250, 1)])
            a = advisor.assess(st, "HDFCBANK", "BUY", 1580.0, 1564.0, 1612.0, 2.0)
            text = "\n".join(a["market_lines"])
            self.assertIn("wrong side of today's average price", text)
            self.assertIn("weaker than its sector today (-1.25% vs +0.50%)", text)
            self.assertIn("vwap_wrong_side", a["shadow"])
            self.assertIn("lagging_sector", a["shadow"])
            self.assertIn("below VWAP ⚠️", a["market_short"])
            s = advisor.assess(st, "HDFCBANK", "SELL", 1580.0, 1596.0, 1548.0, 2.0)
            self.assertNotIn("vwap_wrong_side", s["shadow"])        # the same facts suit a SELL
            self.assertIn("weaker than its sector today", "\n".join(s["market_lines"]))


class OrbTrialTest(unittest.TestCase):
    def day(self):
        rows = [(100, 100.6, 99.8, 100.4, 1)]                         # 09:15 range 99.8-100.6
        rows += [(100.4, 100.7, 100.0, 100.5, 1), (100.5, 100.6, 100.1, 100.3, 1)]   # 09:20, 09:25 (15-min range)
        rows += [(100.3, 101.2, 100.2, 101.0, 1)]                     # 09:30: breaks both ranges up
        rows += [(101.0, 103.0, 100.9, 102.8, 1)] + [(102.8, 104.5, 102.5, 104.0, 1)] * 6
        return five_min("2026-11-16", rows)

    def test_breakout_with_target_and_held_to_close(self):
        with Env(datetime(2026, 11, 16, 15, 20)):
            t5 = engine.orb_trade(self.day(), 5, 1000.0, 100_000)
            t15 = engine.orb_trade(self.day(), 15, 1000.0, 100_000)
        self.assertEqual((t5["side"], t5["time"]), ("BUY", "09:30"))   # first close above the 09:15 high (100.6)
        self.assertGreater(t5["2r"], 0)
        self.assertEqual(t15["time"], "09:30")
        self.assertGreater(t15["eod"], t15["2r"])                     # the runner pays more when held

    def test_report_and_week(self):
        with Env(datetime(2026, 11, 16, 15, 20)) as env:
            st = engine.load()
            st["in_play"] = [{"symbol": "TCS", "rvol": 3.0}]
            env.bars[("TCS", "5m")] = self.day()
            s = engine.report(st)
            self.assertEqual(s["orb_trial_trades"], {"orb5": 1, "orb15": 1})
            self.assertIn("Opening-range test on the top 1 stocks in play", env.sent[-1])
            w = weekly.build(date(2026, 11, 16))
            self.assertEqual(w["orb_trial"]["trades"], {"orb5": 1, "orb15": 1})
            self.assertTrue(any("Opening-range test" in x for x in weekly.paper_lines(w)))


if __name__ == "__main__":
    unittest.main()
