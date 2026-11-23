"""23 Nov review: the trailing stop (by setting only), the sector filter live, 5 open."""
import unittest
from datetime import datetime
from unittest import mock

from helpers import Env

import config
import engine


def bar(t, o, h, l, c):  # noqa: E741
    return {"time": f"2026-11-24 {t}", "open": o, "high": h, "low": l, "close": c, "volume": 1}


class TrailingExitTest(unittest.TestCase):
    def pos(self):
        return {"side": "BUY", "qty": 10, "fill_price": 100.0, "stop": 98.0, "initial_stop": 98.0, "target": 104.0,
                "exit_rule": "trailing"}

    def test_trails_after_one_r_and_locks_the_gain(self):
        p = self.pos()
        bars = [bar("10:01", 100, 101, 99.5, 100.8), bar("10:02", 101, 105, 100.9, 104.8),   # past 2R: no target exit
                bar("10:03", 104.8, 106, 104.5, 105.5)]
        self.assertIsNone(engine.trailing_exit(p, bars))
        self.assertEqual(p["stop"], 104.0)                     # 1R (2.0) behind the best price, 106
        hit = engine.trailing_exit(p, bars + [bar("10:04", 105, 105.2, 103.5, 103.8)])
        self.assertEqual(hit, (104.0, "trailing_stop"))

    def test_before_one_r_it_is_the_plain_stop(self):
        p = self.pos()
        hit = engine.trailing_exit(p, [bar("10:01", 100, 101.5, 99, 99.2), bar("10:02", 99, 99.1, 97.5, 97.8)])
        self.assertEqual(hit, (98.0, "stop_loss"))

    def test_stop_never_moves_back(self):
        p = self.pos()
        engine.trailing_exit(p, [bar("10:01", 100, 103, 100, 102.5), bar("10:02", 102.5, 102.6, 101.2, 101.5)])
        self.assertEqual(p["stop"], 101.0)


class ExitRuleTest(unittest.TestCase):
    def test_fixed_target_by_default_and_no_remembered_switch(self):
        with Env(datetime(2026, 11, 24, 9, 30)):
            st = engine.load()
            st["exit_rule_live"] = {"day": "2026-11-23", "rule": "trailing"}   # what the withdrawn switch stored
            self.assertEqual(config.EXIT_RULE_LIVE, "current")
            self.assertEqual(engine.live_exit_rule(st), "current")
            self.assertNotIn("exit_rule_live", st)

    def test_trailing_only_when_set(self):
        with Env(datetime(2026, 11, 24, 9, 30)), mock.patch.object(config, "EXIT_RULE_LIVE", "trailing"):
            self.assertEqual(engine.live_exit_rule(engine.load()), "trailing")


class LimitsTest(unittest.TestCase):
    def test_five_open_positions(self):
        self.assertEqual(config.MAX_OPEN_POSITIONS, 5)

    def test_buy_weaker_than_its_sector_is_blocked(self):
        with Env(datetime(2026, 11, 24, 10, 0)):
            st = engine.load()
            with mock.patch.object(engine, "risk_gate", return_value=(True, "ok", 10, 200.0, 2.0)), \
                    mock.patch.object(engine.advisor, "assess", return_value={"shadow": ["lagging_sector"]}), \
                    mock.patch.object(engine.reviewer, "review") as review:
                out = engine.propose(st, "TCS", "BUY", "ORB long", 100.0, 98.0, 104.0, ["r"], "x")
            self.assertEqual(out["status"], "blocked")
            self.assertIn("weaker than its sector", out["reason"])
            review.assert_not_called()


if __name__ == "__main__":
    unittest.main()
