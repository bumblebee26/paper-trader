"""One Telegram reader at a time: the session stays visibly alive, and a read conflict never fails a tool."""
import unittest
from datetime import datetime, timedelta
from unittest import mock

from helpers import Env

import engine
import handlers
import server
import telegram_bot as tg


class TelegramReaderTest(unittest.TestCase):
    def test_every_tool_call_refreshes_the_heartbeat(self):
        with Env(datetime(2026, 11, 11, 13, 20)) as env:
            engine.heartbeat()
            env.clock += timedelta(minutes=15)                 # a long think plus a reviewer call
            self.assertFalse(engine.session_alive())
            server._run(lambda state: None)                    # any tool, e.g. wait_for_decision
            self.assertTrue(engine.session_alive())

    def test_responder_waits_twenty_minutes(self):
        with Env(datetime(2026, 11, 11, 13, 20)) as env:
            engine.heartbeat()
            env.clock += timedelta(minutes=15)
            env.text("/help")
            self.assertEqual(handlers.responder({}, None), {"skipped": "session live"})
            env.clock += timedelta(minutes=6)                  # session really gone
            self.assertNotEqual(handlers.responder({}, None), {"skipped": "session live"})

    def test_conflict_is_skipped_not_raised(self):
        with Env(datetime(2026, 11, 11, 13, 20)):
            st = engine.load()
            err = tg.TelegramError("getUpdates failed: Conflict: terminated by other getUpdates request")
            with mock.patch.object(tg, "_api", side_effect=err):
                tg.poll(st, wait=1)                            # no exception
            with mock.patch.object(tg, "_api", side_effect=tg.TelegramError("getUpdates failed: Unauthorized")):
                with self.assertRaises(tg.TelegramError):
                    tg.poll(st, wait=1)

    def test_wait_for_decision_survives_a_conflict(self):
        with Env(datetime(2026, 11, 11, 13, 20)) as env:
            st = engine.load()
            env.prices["HDFCBANK"] = 1600.0
            r = engine.propose(st, "HDFCBANK", "BUY", "ORB long", 1600.0, 1584.0, 1632.0, ["a"], "x")
            calls = {"n": 0}
            real = tg._api

            def flaky(method, http_timeout=30, **p):
                if method == "getUpdates" and calls["n"] == 0:
                    calls["n"] += 1
                    raise tg.TelegramError("getUpdates failed: Conflict: terminated by other getUpdates request")
                return real(method, http_timeout=http_timeout, **p)

            env.tap("yes", r["proposal_id"])
            with mock.patch.object(tg, "_api", flaky), mock.patch("time.time", side_effect=[0, 0, 1, 1, 2, 2, 3, 3]):
                self.assertEqual(engine.wait_decision(st, r["proposal_id"], 3)["status"], "approved")


if __name__ == "__main__":
    unittest.main()
