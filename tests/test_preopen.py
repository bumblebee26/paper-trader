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
