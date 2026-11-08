"""Yahoo chart parsing, ORB detection and the session controller."""
import unittest
from datetime import datetime, timedelta, timezone

from helpers import Env

import market
import scanner
import session_ctl


class MarketTest(unittest.TestCase):
    def test_parse_skips_null_rows_and_converts_to_ist(self):
        payload = {"chart": {"result": [{
            "timestamp": [int(datetime(2026, 11, 9, 3, 55, tzinfo=timezone.utc).timestamp()),
                          int(datetime(2026, 11, 9, 4, 0, tzinfo=timezone.utc).timestamp())],
            "indicators": {"quote": [{"open": [10, None], "high": [11, None], "low": [9, None],
                                      "close": [10.5, None], "volume": [100, None]}]}}]}}
        rows = market._parse(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["time"], "2026-11-09 09:25")
        self.assertEqual(market._parse({"chart": {"result": None}}), [])

    def test_symbols(self):
        self.assertEqual(market.to_yahoo("reliance"), "RELIANCE.NS")
        self.assertEqual(market.to_yahoo("NIFTY"), "^NSEI")


class ScannerTest(unittest.TestCase):
    def test_orb_long(self):
        bars = []
        d0 = datetime(2026, 11, 6, 9, 15)
        for i in range(75):
            bars.append({"time": (d0 + timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M"),
                         "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000})
        d1 = datetime(2026, 11, 9, 9, 15)
        seq = [(100, 100.5, 99.5, 100.2), (100.2, 100.6, 99.8, 100.4), (100.4, 100.7, 100, 100.5),
               (100.5, 100.6, 100.1, 100.3), (100.3, 101.4, 100.3, 101.3)]
        for i, (o, h, lo, c) in enumerate(seq):
            bars.append({"time": (d1 + timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M"),
                         "open": o, "high": h, "low": lo, "close": c, "volume": 3000 if i == 4 else 1000})
        with Env(datetime(2026, 11, 9, 9, 41)):
            info = scanner.analyse("TEST", bars)
        setups = {s["setup"] for s in info["signals"]}
        self.assertIn("ORB long", setups)
        self.assertIn("PDH break", setups)
        orb = next(s for s in info["signals"] if s["setup"] == "ORB long")
        self.assertAlmostEqual(orb["target"] - orb["entry"], 2 * (orb["entry"] - orb["stop"]), places=1)


class SessionCtlTest(unittest.TestCase):
    def test_phase_from_clock(self):
        with Env(datetime(2026, 11, 9, 9, 8)):
            self.assertEqual(session_ctl.current_phase(), "morning")
        with Env(datetime(2026, 11, 9, 12, 28)):
            self.assertEqual(session_ctl.current_phase(), "afternoon")
        with Env(datetime(2026, 11, 9, 16, 0)):
            self.assertEqual(session_ctl.current_phase(), "none")

    def test_claim_once_and_weekend(self):
        with Env(datetime(2026, 11, 9, 9, 8)):
            self.assertEqual(session_ctl.main(["x", "claim", "morning"]), 0)
            self.assertEqual(session_ctl.main(["x", "claim", "morning"]), 3)
            self.assertEqual(session_ctl.main(["x", "claim", "test"]), 0)
        with Env(datetime(2026, 11, 8, 9, 8)):  # Sunday
            self.assertEqual(session_ctl.main(["x", "claim", "morning"]), 3)


if __name__ == "__main__":
    unittest.main()
