"""Chart patterns (24 Nov): fixed rules on completed bars, information only."""
import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "trader"))

import patterns  # noqa: E402


def make(closes, start=date(2026, 6, 1), wick=1.0):
    """Weekday daily bars from a list of closes (high/low = close +/- wick)."""
    out, d = [], start
    for c in closes:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append({"time": f"{d} 09:15", "open": c, "high": c + wick, "low": c - wick, "close": c, "volume": 1})
        d += timedelta(days=1)
    return out


def leg(a, b, n):
    return [a + (b - a) * i / n for i in range(1, n + 1)]


class Patterns(unittest.TestCase):
    def test_double_bottom_forming_then_broken(self):
        closes = [120] * 5 + leg(120, 100, 8) + leg(100, 112, 6) + leg(112, 101, 6) + leg(101, 108, 4)
        bars = make(closes)
        today = str(date.fromisoformat(bars[-1]["time"][:10]) + timedelta(days=1))
        db = [p for p in patterns.find(bars, today) if p["name"] == "double bottom" and p["frame"] == "daily"][0]
        self.assertEqual(db["status"], "forming")
        self.assertEqual(db["level"], 113.0)                     # neckline = the swing high between the lows
        self.assertEqual(db["fails"], 99.0)
        self.assertAlmostEqual(db["target"], 113 + (113 - 99.5), places=1)
        broken = make(closes + leg(108, 116, 3))
        today2 = str(date.fromisoformat(broken[-1]["time"][:10]) + timedelta(days=1))
        db2 = [p for p in patterns.find(broken, today2) if p["name"] == "double bottom" and p["frame"] == "daily"][0]
        self.assertEqual(db2["status"], "neckline broken")
        text = patterns.line([db2], "BUY", 116, 114)
        self.assertIn("double bottom (daily), neckline 113.00 neckline broken -> target", text)
        self.assertIn("information only", text)
        self.assertIn("against: double bottom", patterns.line([db2], "SELL", 116, 118))

    def test_failed_double_bottom_is_dropped(self):
        closes = [120] * 5 + leg(120, 100, 8) + leg(100, 112, 6) + leg(112, 101, 6) + leg(101, 95, 4)
        bars = make(closes)
        today = str(date.fromisoformat(bars[-1]["time"][:10]) + timedelta(days=1))
        self.assertFalse([p for p in patterns.find(bars, today) if p["name"] == "double bottom" and p["frame"] == "daily"])

    def test_bull_flag(self):
        closes = [100] * 12 + leg(100, 112, 5) + [111, 110.5, 111, 110.8]
        bars = make(closes, wick=0.4)
        today = str(date.fromisoformat(bars[-1]["time"][:10]) + timedelta(days=1))
        flag = [p for p in patterns.find(bars, today) if p["name"] == "bull flag"][0]
        self.assertEqual(flag["side"], "BUY")
        self.assertGreater(flag["target"], flag["level"])
        self.assertEqual(patterns.note([flag]), "pattern=bull_flag_daily:forming")

    def test_today_is_never_used_and_empty_is_quiet(self):
        closes = [100] * 12 + leg(100, 112, 5) + [111, 110.5, 111, 110.8]
        bars = make(closes, wick=0.4)
        last_day = bars[-1]["time"][:10]
        self.assertFalse([p for p in patterns.find(bars, last_day) if p["name"] == "bull flag" and p["level"] > 111.5])
        self.assertEqual((patterns.line([], "BUY", 1, 0), patterns.note([])), ("", "pattern=-"))
        self.assertEqual(len(patterns.weekly(make([1] * 10))), 2)


if __name__ == "__main__":
    unittest.main()
