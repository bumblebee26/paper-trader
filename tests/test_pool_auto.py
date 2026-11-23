"""Auto mode for the delivery pools (from 24 Nov): the real swing pool follows the momentum rules, funded from
NIFTYBEES, and takes over stocks bought by hand; long-term auto stays off until the review; Telegram toggles."""
import os
import sys
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "jobs"))

from helpers import Env  # noqa: E402

import custom  # noqa: E402
import pool_auto  # noqa: E402
import pools  # noqa: E402
import swing_momentum as sm  # noqa: E402
from test_momentum import bar, sig  # noqa: E402


def daily_bars(day_closes):
    return [{"time": f"{d} 09:15", "open": c, "high": c + 6, "low": c - 6, "close": c, "volume": 1}
            for d, c in day_closes]


class Toggle(unittest.TestCase):
    def test_defaults_and_commands(self):
        with Env(datetime(2026, 11, 24, 10, 0)):
            self.assertTrue(pool_auto.on("swing"))
            self.assertFalse(pool_auto.on("long_term"))       # waits for the 18 Dec review
            self.assertEqual(custom.parse(["LONGTERM", "AUTO", "ON"]), ("auto", {"pool": "long_term", "on": True}))
            self.assertEqual(custom.parse(["SWING", "AUTO", "OFF"]), ("auto", {"pool": "swing", "on": False}))
            self.assertEqual(custom.parse(["SWING", "AUTO"])[0], "error")
            self.assertIn("is now OFF", pool_auto.set_mode("swing", False))
            self.assertFalse(pool_auto.on("swing"))


class RealSwing(unittest.TestCase):
    def test_buys_funded_from_niftybees_and_takes_over_a_hand_bought_stock(self):
        with Env(datetime(2026, 11, 24, 15, 50)) as env:
            p = pools.load("swing")
            pools.buy(p, "MINE", 10, 1000.0, reason="your own buy", origin="custom", stop=950.0, target=1100.0)
            pools.save(p)
            env.prices["NIFTYBEES"] = 290.0
            pools.park("swing")                                   # the rest of the pool in NIFTYBEES
            nb_before = pools.load("swing")["holdings"]["NIFTYBEES"]["qty"]
            env.bars[("NIFTYBEES", "1d")] = bar("2026-11-24", 290, 291, 289, 290)
            env.bars[("AAA", "1d")] = bar("2026-11-24", 500, 512, 498, 510)
            env.bars[("MINE", "1d")] = daily_bars([(f"2026-11-{d:02d}", 1000) for d in range(3, 25)])
            snap = sm.daily("2026-11-24", [sig("AAA", 1, d="2026-11-23")], sm.REAL_POOL)
            p = pools.load("swing")
            self.assertIn("AAA", p["holdings"])                            # bought by the rules
            self.assertLess(p["holdings"]["NIFTYBEES"]["qty"], nb_before)   # paid for by selling NIFTYBEES
            mine = p["holdings"]["MINE"]
            self.assertEqual((mine["origin"], mine["atr"]), ("auto", 12.0))  # taken over: 2 x ATR(12) under cost
            self.assertNotIn("target", mine)
            self.assertTrue(any(m.startswith("TAKEN OVER MINE") for m in snap["moves"]))
            self.assertTrue(any("NIFTYBEES" in m and "pay for it" in m for m in snap["moves"]))
            self.assertGreater(p["cash"], -1)

    def test_off_means_the_real_pool_is_left_alone(self):
        with Env(datetime(2026, 11, 24, 15, 50)) as env:
            pool_auto.set_mode("swing", False)
            env.bars[("NIFTYBEES", "1d")] = bar("2026-11-24", 290, 291, 289, 290)
            env.bars[("AAA", "1d")] = bar("2026-11-24", 500, 512, 498, 510)
            with mock.patch("brief.sql", return_value=[sig("AAA", 1, d="2026-11-23")]):
                out = sm.run(send=False)
            self.assertNotIn("swing", out)
            self.assertEqual(pools.load("swing")["holdings"], {})


if __name__ == "__main__":
    unittest.main()
