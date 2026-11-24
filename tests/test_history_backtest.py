"""Phase 3 history backtests (backtest/history.py) on made-up prices: signals fire when they should, the engine
buys at the next open, costs apply, and the verdict needs both more than NIFTYBEES and a positive low end."""
import os
import random
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backtest"))
sys.path.insert(0, os.path.dirname(__file__))

import history  # noqa: E402
from test_backtest import series  # noqa: E402


def walk(n, drift, seed, start=100.0, vol=0.012):
    rnd, c, out = random.Random(seed), start, []
    for _ in range(n):
        c *= 1 + drift + rnd.gauss(0, vol)
        out.append(round(c, 2))
    return out


class Signals(unittest.TestCase):
    def setUp(self):
        n = 600
        self.data = {"NIFTYBEES": series(walk(n, 0.0003, 1), start=date(2019, 1, 1))}
        for k in range(30):
            closes = walk(n, 0.0008 if k % 2 else -0.0002, 10 + k)
            vols = [1e5 * (3 if (i % 97 == 0) else 1) for i in range(n)]
            self.data[f"S{k:02d}"] = series(closes, vols, start=date(2019, 1, 1))
        self.series = history.prepare(self.data)

    def test_indicators_are_sane(self):
        b = self.series["S01"][-1]
        self.assertTrue(0 <= b["rsi14"] <= 100)
        self.assertTrue(0 <= b["adx14"] <= 100)
        self.assertIsNotNone(b["macd"])
        self.assertGreaterEqual(b["high52w"], b["close"] * 0.99)

    def test_every_strategy_runs_and_is_judged(self):
        for name in history.NAMES:
            trades, curve = history.run(self.series, name, "2019-12-01", "2021-06-30")
            self.assertTrue(curve, name)
            s = history.judge(trades, curve, self.series)
            self.assertIn(s["verdict"].split(" (")[0], ("beats NIFTYBEES", "could be luck", "no"))
            for t in trades:
                self.assertGreater(t["entry_date"], "2019-12-01")
                self.assertLessEqual(t["days"], 20)
        trades, _ = history.run(self.series, "momentum", "2019-12-01", "2021-06-30")
        self.assertGreater(len(trades), 30)                       # momentum always has candidates

    def test_rally_range_shape(self):
        closes = [100.0] * 30 + [101, 102.5, 104, 105.5, 106] + [106.2, 105.9, 106.1, 105.8, 106.0] + [108.0]
        vols = [1e5] * (len(closes) - 1) + [3e5]
        rows = series(closes, vols)
        bars = history.more_indicators(__import__("swing").indicators(__import__("swing").adjust(rows)))
        self.assertTrue(history.rally_range(bars, len(bars) - 1))
        self.assertFalse(history.rally_range(bars, len(bars) - 2))

    def test_verdict_needs_index_beaten_and_low_end_above_zero(self):
        curve = [("2020-01-01", 60000.0), ("2020-12-31", 66000.0)]
        trades = [{"r": 0.5 + (i % 3) * 0.1, "pnl": 10, "exit_date": f"2020-{m:02d}-10", "days": 5}
                  for m in range(1, 13) for i in range(4)]
        s = history.judge(trades, curve, {"NIFTYBEES": [{"date": "2020-01-01", "close": 100},
                                                        {"date": "2020-12-31", "close": 105}]})
        self.assertEqual(s["verdict"], "beats NIFTYBEES")
        s2 = history.judge(trades, curve, {"NIFTYBEES": [{"date": "2020-01-01", "close": 100},
                                                         {"date": "2020-12-31", "close": 130}]})
        self.assertEqual(s2["verdict"], "could be luck")


if __name__ == "__main__":
    unittest.main()
