"""Faster cycles (from 19 Nov): wake as soon as the next candle shows, check stops every minute while waiting, wake
early on fresh news, and scan several stocks at a time."""
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "trader"))

from helpers import Env, minute_bars  # noqa: E402

import config  # noqa: E402
import engine  # noqa: E402
import server  # noqa: E402

DAY = datetime(2026, 11, 16, 10, 41, 30)


def plan_with(universe=("GODREJIND",)):
    return {"tradable_universe": list(universe), "news_radar": [], "risk_per_trade_pct": 1.0}


class Clock:
    """Advance the test clock when the code sleeps or long-polls Telegram, so the wait loop moves on."""

    def __init__(self, env):
        self.env = env

    def sleep(self, seconds):
        self.env.clock += timedelta(seconds=seconds)

    def poll(self, state, wait=0):
        self.env.clock += timedelta(seconds=wait)


def nifty_bar(at):
    return {"time": at, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0}


class WaitTest(unittest.TestCase):
    def patched(self, env):
        c = Clock(env)
        return [mock.patch.object(server.time, "sleep", c.sleep), mock.patch.object(server.tg, "poll", c.poll)]

    def test_wakes_when_the_new_candle_shows(self):
        with Env(DAY) as env:
            env.bars[("^NSEI", "5m")] = [nifty_bar("2026-11-16 10:40"), nifty_bar("2026-11-16 10:45")]
            with self.patched(env)[0], self.patched(env)[1]:
                out = server.wait_for_next_candle("15:10")
            self.assertEqual(out["woke_for"], "candle")
            # the 10:45 candle already shows, so it returns right after the 3-second wake, not at 10:45:20
            self.assertEqual(out["time_ist"], "10:45:03")

    def test_waits_for_yahoo_up_to_the_limit(self):
        with Env(DAY) as env:
            env.bars[("^NSEI", "5m")] = [nifty_bar("2026-11-16 10:40")]       # the new candle never shows
            with self.patched(env)[0], self.patched(env)[1]:
                out = server.wait_for_next_candle("15:10")
            self.assertGreaterEqual(out["time_ist"], "10:45:25")
            self.assertLess(out["time_ist"], "10:45:30")

    def test_fresh_news_ends_the_wait_without_being_used_up(self):
        with Env(DAY) as env:
            st = engine.load()
            engine.save_universe(plan_with())
            engine.start_day(st, plan_with(), ["HDFCBANK"])
            engine.save(st)
            env.add_news("GODREJIND", "order", "Big order", when="2026-11-16 10:41:00")
            with self.patched(env)[0], self.patched(env)[1]:
                out = server.wait_for_next_candle("15:10")
            self.assertEqual((out["woke_for"], out["news_on"]), ("news", ["GODREJIND"]))
            self.assertLess(out["time_ist"], "10:45:00")                         # within about a minute
            st = engine.load()
            self.assertEqual([a["symbol"] for a in engine.news_alerts(st)], ["GODREJIND"])   # run_cycle still reports it

    def test_stops_checked_every_minute_while_waiting(self):
        with Env(DAY) as env:
            env.prices["RELIANCE"] = 1452.0
            st = engine.load()
            pid = engine.propose(st, "RELIANCE", "BUY", "ORB long", 1452.0, 1441.5, 1473.0, ["a"], "x")["proposal_id"]
            env.tap("yes", pid)
            engine.wait_decision(st, pid, 1)
            engine.execute(st, pid)
            engine.save(st)
            env.bars[("RELIANCE", "1m")] = minute_bars(datetime(2026, 11, 16, 10, 42), [(1450, 1451, 1440, 1441)])
            env.bars[("^NSEI", "5m")] = [nifty_bar("2026-11-16 10:45")]
            with self.patched(env)[0], self.patched(env)[1]:
                server.wait_for_next_candle("15:10")
            st = engine.load()
            self.assertEqual(st["positions"], {})                               # closed at the stop while waiting
            self.assertEqual([(e["symbol"], e["status"]) for e in st["pending_events"]], [("RELIANCE", "closed")])
            self.assertLess(st["pending_events"][0]["exit_price"], 1441.5)      # at the stop (with slippage)


class ScanTest(unittest.TestCase):
    def test_parallel_scan_keeps_order_and_reports_errors(self):
        def analyse(sym):
            if sym == "BAD":
                raise RuntimeError("no data")
            return {"symbol": sym, "signals": ["ORB long"] if sym != "QUIET" else []}

        with mock.patch.object(server.scanner, "analyse", analyse), \
                mock.patch.object(server, "_with_market", lambda info, moves: info):
            found = server._scan(["AAA", "QUIET", "BAD", "BBB", "CCC", "DDD"])
        self.assertEqual([f["symbol"] for f in found], ["AAA", "BAD", "BBB", "CCC", "DDD"])
        self.assertEqual(found[1]["error"], "no data")
        self.assertEqual(config.SCAN_WORKERS, 4)


if __name__ == "__main__":
    unittest.main()
