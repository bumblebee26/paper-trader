"""NSE holidays (live list from NSE, cached in S3): no session, no parking, no entries; dates skip holidays."""
import unittest
from unittest import mock
from datetime import date, datetime

from helpers import Env

import engine
import handlers
import market
import nse_calendar
import pools
import storage
import session_ctl


class HolidayTest(unittest.TestCase):
    def test_calendar(self):
        with Env(datetime(2026, 11, 9, 20, 0)):
            self._calendar()

    def _calendar(self):
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

    def test_parse_nse_response(self):
        payload = {"CM": [{"tradingDate": "10-Nov-2026", "weekDay": "Tuesday", "description": "Diwali-Balipratipada"},
                          {"tradingDate": "08-Nov-2026", "description": "Diwali Laxmi Pujan*"},
                          {"tradingDate": "bad"}], "FO": []}
        self.assertEqual(nse_calendar.parse(payload),
                         {"2026-11-10": "Diwali-Balipratipada", "2026-11-08": "Diwali Laxmi Pujan"})

    def test_cached_in_s3_and_reused_when_nse_is_down(self):
        with Env(datetime(2026, 11, 9, 20, 0)) as env:
            self.assertTrue(market.holiday(date(2026, 11, 10)))
            self.assertIn("2026-11-10", storage.get_json(nse_calendar.KEY)["holidays"])
            env.nse_holidays = None  # NSE unreachable a few days later: the cache still answers
            nse_calendar.reset_cache()
            env.clock = datetime(2026, 11, 12, 9, 0, tzinfo=env.clock.tzinfo)
            self.assertEqual(market.holiday(date(2026, 11, 24)), "Prakash Gurpurb Sri Guru Nanak Dev")

    def test_new_list_from_nse_is_picked_up(self):
        with Env(datetime(2026, 12, 21, 9, 8)) as env:
            env.nse_holidays = {"2027-01-26": "Republic Day"}
            self.assertEqual(session_ctl.main(["x", "claim", "morning"]), 0)
            self.assertEqual(market.holiday(date(2027, 1, 26)), "Republic Day")

    def test_lambda_reads_cache_only(self):
        with Env(datetime(2026, 11, 10, 15, 14)) as env, mock.patch.dict("os.environ", {"AWS_LAMBDA_FUNCTION_NAME": "g"}):
            env.prices["NIFTYBEES"] = 290.0
            self.assertEqual(handlers.guardian({"action": "park"}, None)["status"], "bought")  # no cache yet
            nse_calendar.refresh()  # the morning claim on the runner
            nse_calendar.reset_cache()
            self.assertEqual(market.holiday(date(2026, 11, 10)), "Diwali-Balipratipada")

    def test_no_list_at_all_means_weekdays_trade(self):
        with Env(datetime(2026, 11, 10, 9, 8)) as env:
            env.nse_holidays = None
            self.assertTrue(market.is_trading_day(date(2026, 11, 10)))
            self.assertEqual(session_ctl.main(["x", "claim", "morning"]), 0)  # old behaviour: Claude finds no candles


if __name__ == "__main__":
    unittest.main()
