"""Room to the next price level (B1, 24 Nov): shown on proposals, never a filter."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "trader"))

import levels  # noqa: E402


def bar(d, h, low):
    return {"time": f"{d} 09:15", "open": low, "high": h, "low": low, "close": h, "volume": 1}


BARS = [bar("2026-10-15", 130, 90),                      # last month (and the 52-week high)
        bar("2026-11-10", 112, 100), bar("2026-11-13", 115, 101),   # last week (ISO week 46)
        bar("2026-11-17", 108, 103), bar("2026-11-20", 109, 104),   # this week up to yesterday... week 47
        bar("2026-11-23", 106, 102),                      # Monday of week 48 = yesterday
        bar("2026-11-24", 999, 1)]                        # today: never used


class Levels(unittest.TestCase):
    def test_completed_bars_only(self):
        lv = levels.compute(BARS, "2026-11-24")
        self.assertEqual((lv["pdh"], lv["pdl"]), (106, 102))
        self.assertEqual((lv["pwh"], lv["pwl"]), (109, 103))          # week 47
        self.assertEqual((lv["pmh"], lv["pml"]), (130, 90))           # October
        self.assertEqual((lv["h52"], lv["l52"]), (130, 90))
        self.assertNotIn(999, lv.values())

    def test_room_in_the_trade_direction(self):
        lv = levels.compute(BARS, "2026-11-24")
        r = levels.room(lv, "BUY", 105, 104)                            # 1 rupee risk; next up = 106 (yesterday)
        self.assertEqual((r["key"], r["r"]), ("pdh", 1.0))
        self.assertIn("yesterday's high 106.00 is 1R away", levels.line(lv, "BUY", 105, 104))
        self.assertIn("under 1R", levels.line(lv, "SELL", 104.5, 106.5))   # next down = 103 -> 0.75R
        self.assertEqual(levels.note(lv, "SELL", 104.5, 106.5), "room=0.75R@pwl")
        self.assertIsNone(levels.room(lv, "BUY", 140, 135))
        self.assertIn("52-week high broken", levels.line(lv, "BUY", 140, 135))
        self.assertEqual((levels.line({}, "BUY", 1, 0), levels.note({}, "BUY", 1, 0)), ("", "room=-"))

    def test_weekly_rsi(self):
        rising = [bar(f"2026-{m:02d}-{d:02d}", 0, 0) | {"close": i} for i, (m, d) in
                  enumerate([(mm, dd) for mm in range(6, 12) for dd in (3, 10, 17, 24)])]
        self.assertEqual(levels.weekly_rsi(rising, "2026-11-25"), 100.0)
        self.assertIsNone(levels.weekly_rsi(rising[:10], "2026-11-25"))
        self.assertIn("weekly RSI 61", levels.line({"pdh": 106}, "BUY", 105, 104, 61.0))

    def test_in_play_message_is_a_ranking(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
        from helpers import Env
        with Env():
            import engine
            msg = engine.in_play_message([{"symbol": "TCS", "score": 3.4, "rvol": 2.4, "gap_pct": 0.8, "news": True,
                                           "scanner": "strict"}] * 6)
        self.assertIn("a ranking, not a chance of profit", msg)
        self.assertIn("1. TCS score 3.4 (2.4x usual volume, gap +0.8%, news, scanner strict)", msg)
        self.assertNotIn("6. TCS", msg)


if __name__ == "__main__":
    unittest.main()
