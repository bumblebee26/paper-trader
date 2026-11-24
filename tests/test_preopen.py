"""NSE pre-open auction (24 Nov): saved and shown, never part of a decision."""
import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from helpers import Env  # noqa: E402

import engine  # noqa: E402
import preopen  # noqa: E402
import storage  # noqa: E402
from unittest import mock  # noqa: E402

PAYLOAD = {"data": [
    {"metadata": {"symbol": "TCS", "iep": 4141.0, "previousClose": 4100.0},
     "detail": {"preOpenMarket": {"totalBuyQuantity": 18000, "totalSellQuantity": 10000}}},
    {"metadata": {"symbol": "INFY", "iep": "1,470.00", "previousClose": "1,500.00"},
     "detail": {"preOpenMarket": {"totalBuyQuantity": 5000, "totalSellQuantity": 0}}},
    {"metadata": {"symbol": "NOTTRADABLE", "iep": 10, "previousClose": 9}, "detail": {}},
    {"metadata": {"symbol": "ZERO", "iep": 0, "previousClose": 9}, "detail": {}},
]}


class PreOpen(unittest.TestCase):
    def test_parse_save_and_lines(self):
        with Env(datetime(2026, 11, 25, 9, 9)):
            stocks = preopen.capture({"TCS", "INFY", "ZERO"}, fetch=lambda: PAYLOAD)
            self.assertEqual(set(stocks), {"TCS", "INFY"})
            self.assertEqual((stocks["TCS"]["gap_pct"], stocks["TCS"]["buy_sell"]), (1.0, 1.8))
            self.assertIsNone(stocks["INFY"]["buy_sell"])
            self.assertEqual(storage.get_json("preopen/2026-11-25.json")["stocks"], stocks)
            self.assertEqual(preopen.today(), stocks)
            text = "\n".join(preopen.lines(stocks))
            self.assertIn("Gaps up: TCS +1.0%, buy/sell 1.8x", text)
            self.assertIn("Gaps down: INFY -2.0%", text)
            self.assertIn("information only", text)
            msg = engine.in_play_message([{"symbol": "TCS", "score": 2, "rvol": 2, "gap_pct": 1.0}], pre=stocks)
            self.assertIn("pre-open +1.0%, buy/sell 1.8x", msg)

    def test_nse_blocked_changes_nothing(self):
        def blocked():
            raise RuntimeError("HTTP 403")
        with Env(datetime(2026, 11, 25, 9, 9)):
            self.assertEqual(preopen.capture(None, fetch=blocked), {})
            self.assertIsNone(storage.get_json("preopen/2026-11-25.json"))
            self.assertIn("not available today", preopen.lines({})[0])


if __name__ == "__main__":
    unittest.main()


class MorningStart(unittest.TestCase):
    """From 25 Nov the session starts at 08:55: the plan is held until the final pre-open read (~09:09:30)."""

    def test_plan_held_then_sent_with_the_final_pre_open(self):
        import server
        from test_cycle_speed import Clock, plan_with
        with Env(datetime(2026, 11, 25, 8, 57)) as env:
            engine.save_universe(plan_with(["TCS", "INFY"]))
            reads = []

            def fake(*a, **k):
                reads.append(env.clock.strftime("%H:%M:%S"))
                return PAYLOAD
            c = Clock(env)
            with mock.patch.object(server.time, "sleep", c.sleep), mock.patch.object(server.tg, "poll", c.poll), \
                    mock.patch.object(preopen, "get_json", side_effect=fake):
                out = server.start_day(["TCS"], plan_with(["TCS", "INFY"]))
                self.assertIn("held", out["plan_message"])
                self.assertEqual(env.sent, [])                                # nothing before the auction ends
                self.assertEqual(reads, [])                                   # and NSE isn't asked before 09:00
                w = server.wait_for_open()
            self.assertEqual(w["plan"], "sent")
            self.assertEqual(reads[0][:5], "09:04")                           # indicative, saved only
            self.assertGreaterEqual(reads[1], "09:09:30")                     # final, after the random close
            self.assertEqual(len(env.sent), 1)
            self.assertIn("Gaps up: TCS +1.0%", env.sent[0])
            self.assertTrue(storage.get_json("preopen/2026-11-25_indicative.json"))
            self.assertEqual(server.wait_for_open()["plan"], "already sent")  # a second call does nothing

    def test_late_start_sends_at_once(self):
        import server
        from test_cycle_speed import plan_with
        with Env(datetime(2026, 11, 25, 9, 16)) as env:
            engine.save_universe(plan_with(["TCS"]))
            with mock.patch.object(preopen, "get_json", return_value=PAYLOAD):
                out = server.start_day(["TCS"], plan_with(["TCS"]))
            self.assertNotIn("plan_message", out)
            self.assertEqual(len(env.sent), 1)

    def test_nothing_read_before_nine(self):
        with Env(datetime(2026, 11, 25, 8, 58)):
            self.assertEqual(preopen.capture(None, fetch=lambda: PAYLOAD), {})


class OpeningRead(unittest.TestCase):
    def test_read_in_the_0925_message(self):
        read = {"nifty_gap_pct": 0.4, "nifty_first10_pct": -0.2, "nifty_gap": "faded", "up_from_open": "7/20",
                "median_or_vs_day_range": 0.35, "india_vix_change_pct": 3.2}
        msg = engine.in_play_message([{"symbol": "TCS", "score": 2, "rvol": 2, "gap_pct": 1.0}], read=read)
        self.assertIn("Opening read: Nifty opened +0.40% and moved -0.20% in 10 min (faded); 7/20 stocks up from "
                      "their open; opening range 35% of a usual day's range; India VIX +3.2%.", msg)
