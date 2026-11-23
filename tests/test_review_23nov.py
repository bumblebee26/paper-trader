"""23 Nov review (council's version): trailing stop with its automatic switch, the sector filter live, 5 open."""
import csv
import io
import unittest
from datetime import datetime
from unittest import mock

from helpers import Env

import config
import engine
import exits
import storage


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


def write_variants(day, rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["timestamp_ist", "event", "event_id", "note"])
    w.writeheader()
    for i, (cur, trail) in enumerate(rows):
        w.writerow({"timestamp_ist": f"{day} 15:20:00", "event": "exit_variants", "event_id": f"ev-{day}-{i}",
                    "note": exits.note_text({"current": cur, "trailing": trail})})
    storage.backend().put(f"journal/{day}.csv", buf.getvalue())


class AutoSwitchTest(unittest.TestCase):
    def test_stays_fixed_below_fifteen_trades(self):
        with Env(datetime(2026, 11, 24, 9, 30)) as env, mock.patch.object(config, "EXIT_RULE_LIVE", "auto"):
            write_variants("2026-11-20", [(-50, 60)] * 13)
            self.assertEqual(engine.live_exit_rule(engine.load()), "current")
            self.assertFalse(env.sent)

    def test_switches_once_at_fifteen_if_trailing_is_ahead_and_sticks(self):
        with Env(datetime(2026, 11, 24, 9, 30)) as env, mock.patch.object(config, "EXIT_RULE_LIVE", "auto"):
            write_variants("2026-11-20", [(-50, 60)] * 13)
            write_variants("2026-11-21", [(-50, 60)] * 2)
            st = engine.load()
            self.assertEqual(engine.live_exit_rule(st), "trailing")
            self.assertTrue(any("Exit rule switched" in m for m in env.sent))
            write_variants("2026-11-24", [(500, -500)] * 5)   # later trades favour the fixed target: no switch back
            st["exit_rule_live"]["day"] = "2026-11-23"
            self.assertEqual(engine.live_exit_rule(st), "trailing")

    def test_no_switch_when_the_fixed_target_is_ahead(self):
        with Env(datetime(2026, 11, 24, 9, 30)), mock.patch.object(config, "EXIT_RULE_LIVE", "auto"):
            write_variants("2026-11-20", [(60, -50)] * 16)
            self.assertEqual(engine.live_exit_rule(engine.load()), "current")


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
