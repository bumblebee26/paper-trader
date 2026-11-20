"""The trade's early exit on its exit line: live (strict: a real break) and replayed both ways (any close, strict)."""
import unittest
from datetime import datetime

from helpers import Env

import engine
import exits
from test_exits import OPEN, bars


def minutes(closes_5m, start_min=41):
    """1-minute candles whose 5-minute closes are the given values (each 5-minute bucket flat at its close)."""
    path = []
    for c in closes_5m:
        path += [(c - 0.05, c + 0.05, c)] * 5
    return bars(path, start_min=start_min)


class BreakTest(unittest.TestCase):
    def test_strict_needs_a_real_break(self):
        self.assertFalse(exits.early_break("BUY", 1753.0, [1752.0]))                 # 0.06% past: noise
        self.assertTrue(exits.early_break("BUY", 1753.0, [1752.0], strict=False))    # the old way
        self.assertTrue(exits.early_break("BUY", 1753.0, [1749.0]))                  # 0.23% past
        self.assertTrue(exits.early_break("BUY", 1753.0, [1752.5, 1752.0]))          # two in a row
        self.assertFalse(exits.early_break("BUY", 1753.0, [1752.0, 1754.0]))         # back above
        self.assertTrue(exits.early_break("SELL", 100.0, [100.1, 100.05]))           # short: above the line
        self.assertFalse(exits.early_break("BUY", None, [1.0]))

    def test_line_must_sit_between_stop_and_entry(self):
        self.assertEqual(exits.valid_exit_line("BUY", 1765.0, 1749.0, 1753.0), 1753.0)
        self.assertIsNone(exits.valid_exit_line("BUY", 1765.0, 1749.0, 1770.0))      # above the entry
        self.assertIsNone(exits.valid_exit_line("BUY", 1765.0, 1749.0, 1745.0))      # below the stop
        self.assertEqual(exits.valid_exit_line("SELL", 100.0, 102.0, 101.0), 101.0)


class ReplayTest(unittest.TestCase):
    def run_rule(self, rule, closes, line=99.0):
        return exits.replay(rule, "BUY", 100, 100.0, 98.0, 104.0, OPEN, minutes(closes), exit_line=line)

    def test_whipsaw_strict_holds_any_leaves(self):
        # one close a hair under 99, then up to the target
        closes = [100.2, 98.95, 101.0, 102.5, 104.2]
        any_, strict, cur = (self.run_rule(r, closes) for r in ("early_any", "early_strict", "current"))
        self.assertLess(any_, 0)                         # left at 98.95 for a small loss
        self.assertEqual(strict, cur)                    # held and reached the target
        self.assertGreater(strict, 0)

    def test_real_break_strict_saves_money(self):
        closes = [100.2, 98.9, 98.7, 98.4, 97.0]         # two closes under 99, then the stop
        strict, cur = self.run_rule("early_strict", closes), self.run_rule("current", closes)
        self.assertGreater(strict, cur)

    def test_without_a_line_they_are_the_current_rule(self):
        closes = [100.2, 98.9, 101.0]
        for r in ("early_any", "early_strict"):
            self.assertEqual(self.run_rule(r, closes, line=None), self.run_rule("current", closes, line=None))


class LiveTest(unittest.TestCase):
    def test_monitor_exits_on_a_real_break_only(self):
        with Env(datetime(2026, 11, 20, 10, 6)) as env:
            st = engine.load()
            st["positions"]["TECHM"] = {"side": "BUY", "qty": 25, "fill_price": 1765.0, "stop": 1749.0,
                                        "target": 1797.0, "opened_at": "2026-11-20 09:40:00", "setup": "VWAP reclaim",
                                        "proposal_id": "t1", "exit_line": 1753.0}
            env.prices["TECHM"] = 1752.0
            five = lambda cl: [{"time": f"2026-11-20 09:{m:02d}", "open": c, "high": c, "low": c, "close": c,
                                "volume": 1} for m, c in cl]                                     # noqa: E731
            one = [{"time": "2026-11-20 09:41", "open": 1760, "high": 1762, "low": 1751, "close": 1752}]
            env.bars[("TECHM", "1m")] = one
            env.bars[("TECHM", "5m")] = five([(40, 1760.0), (55, 1752.0)])   # one close 0.06% under: hold
            engine.monitor(st)
            self.assertIn("TECHM", st["positions"])
            env.bars[("TECHM", "5m")] = five([(40, 1760.0), (50, 1752.5), (55, 1752.0)])   # two in a row: out
            engine.monitor(st)
            self.assertNotIn("TECHM", st["positions"])
            self.assertTrue(any("idea broke" in m for m in env.sent))


if __name__ == "__main__":
    unittest.main()
