"""Responder Lambda: answers Telegram commands outside sessions, stays quiet while one is live."""
import unittest
from datetime import datetime

from helpers import Env

import engine
import handlers
import storage


class ResponderTest(unittest.TestCase):
    def test_answers_help_in_the_evening(self):
        with Env(datetime(2026, 11, 9, 20, 15)) as env:
            env.text("what can you do?")
            out = handlers.responder({}, None)
            self.assertEqual(out, {"answered": 1})
            self.assertTrue(env.sent[-1].startswith("[MOCK] paper-trader: what I can do"))
            self.assertIn("within about 2 minutes", env.sent[-1])
            # offset saved: the same message is not answered twice
            self.assertEqual(handlers.responder({}, None), {"answered": 0})

    def test_ledger_out_of_hours(self):
        with Env(datetime(2026, 11, 9, 21, 0)) as env:
            env.text("/ledger")
            handlers.responder({}, None)
            self.assertIn("Starting capital: Rs 1,00,000", env.sent[-1])
            self.assertIn("No open positions.", env.sent[-1])

    def test_quiet_while_session_live(self):
        with Env(datetime(2026, 11, 9, 10, 40)) as env:
            engine.heartbeat()
            env.text("/help")
            self.assertEqual(handlers.responder({}, None), {"skipped": "session live"})
            self.assertEqual(env.sent, [])

    def test_quiet_while_session_starting(self):
        with Env(datetime(2026, 11, 9, 9, 9)) as env:
            engine.claim_session("morning")  # claimed, Claude Code still installing, no heartbeat yet
            env.text("/help")
            self.assertEqual(handlers.responder({}, None), {"skipped": "session live"})

    def test_evening_pause_applies_to_next_trading_day(self):
        with Env(datetime(2026, 11, 13, 19, 0)) as env:  # Friday evening
            env.text("/pause")
            handlers.responder({}, None)
            self.assertIn("Paused for Mon 16 Nov", env.sent[-1])
            env.clock = datetime(2026, 11, 16, 10, 0, tzinfo=env.clock.tzinfo)
            st = engine.load()
            self.assertTrue(engine.account(st)["paused_by_user"])
            r = engine.propose(st, "TCS", "BUY", "ORB long", 3000.0, 2990.0, 3020.0, ["a"], "x")
            self.assertEqual(r["status"], "blocked")
            env.clock = datetime(2026, 11, 17, 10, 0, tzinfo=env.clock.tzinfo)
            self.assertFalse(engine.account(engine.load())["paused_by_user"])  # only that one day

    def test_resume_cancels_and_unknown_text_gets_hint(self):
        with Env(datetime(2026, 11, 9, 19, 0)) as env:
            env.text("/pause")
            env.text("/resume")
            env.text("hello there")
            env.text("yes")
            handlers.responder({}, None)
            self.assertIn("Resumed", env.sent[-3])
            self.assertIn("I didn't catch that", env.sent[-2])
            self.assertIn("no proposal waiting", env.sent[-1])
            self.assertIsNone(storage.get_json(storage.LEDGER_KEY).get("paused_for"))

    def test_exit_out_of_hours(self):
        with Env(datetime(2026, 11, 9, 19, 0)) as env:
            env.text("/exit HDFCBANK")
            handlers.responder({}, None)
            self.assertIn("No open positions", env.sent[-1])


if __name__ == "__main__":
    unittest.main()
