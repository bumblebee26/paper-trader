"""NSE holidays: no session, no parking, no entries; /pause and messages skip to the next trading day."""
import unittest
from datetime import date, datetime

from helpers import Env

import engine
import handlers
import market
import pools
import session_ctl


class HolidayTest(unittest.TestCase):
    def test_calendar(self):
        self.assertEqual(market.holiday(date(2026, 11, 10)), "Diwali-Balipratipada")
        self.assertTrue(market.is_trading_day(date(2026, 11, 11)))
        self.assertFalse(market.is_trading_day(date(2026, 11, 14)))  # Saturday
        self.assertEqual(market.next_trading_day_after(date(2026, 11, 9)), date(2026, 11, 11))
        self.assertEqual(market.next_trading_day_after(date(2026, 11, 23)), date(2026, 11, 25))

    def test_session_skipped_with_one_message(self):
        with Env(datetime(2026, 11, 10, 9, 8)) as env:
            self.assertEqual(session_ctl.main(["x", "claim", "morning"]), 3)
            self.assertEqual(session_ctl.main(["x", "claim", "morning"]), 3)  # backup run: no second message
            self.assertEqual(session_ctl.main(["x", "claim", "afternoon"]), 3)
            self.assertEqual(len(env.sent), 1)
            self.assertIn("NSE is closed for Diwali-Balipratipada", env.sent[0])
            self.assertIn("Wed 11 Nov", env.sent[0])
            self.assertFalse(engine.session_active())  # the bot keeps answering commands all day

    def test_normal_day_still_claims(self):
        with Env(datetime(2026, 11, 11, 9, 8)):
            self.assertEqual(session_ctl.main(["x", "claim", "morning"]), 0)

    def test_no_parking_or_entries_on_holiday(self):
        with Env(datetime(2026, 11, 10, 15, 14)) as env:
            env.prices["NIFTYBEES"] = 290.0
            self.assertEqual(handlers.guardian({"action": "park"}, None), {"skipped": "market closed today"})
            self.assertFalse(pools.load("swing")["holdings"])
            env.clock = datetime(2026, 11, 10, 10, 0, tzinfo=env.clock.tzinfo)
            self.assertFalse(engine.in_entry_window())

    def test_pause_on_holiday_eve_goes_to_next_trading_day(self):
        with Env(datetime(2026, 11, 9, 19, 0)):
            self.assertEqual(engine.next_trading_day(), "2026-11-11")


if __name__ == "__main__":
    unittest.main()
