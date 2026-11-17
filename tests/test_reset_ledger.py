"""The 17 Nov 2026 ledger reset: every pool back to its starting cash, history and settings kept, old state archived."""
import os
import sys
import unittest
from datetime import date, datetime
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "jobs"))

from helpers import Env  # noqa: E402

import config  # noqa: E402
import engine  # noqa: E402
import pools  # noqa: E402
import reset_ledger as rl  # noqa: E402
import storage  # noqa: E402
import weekly  # noqa: E402


class ResetLedger(unittest.TestCase):
    def _seed(self):
        storage.put_json(storage.LEDGER_KEY, {
            "balance": 31_250.0, "starting_capital": config.STARTING_CAPITAL, "positions": {"AAA": {"qty": 5}},
            "proposals": {"p1": {"status": "filled", "day": "2026-11-17"}}, "decisions": {}, "exit_requests": [],
            "tg_offset": 991, "msg_index": {"55": {"day": "2026-11-17", "proposal_id": "p1"}}, "auto_mode": True,
            "day": "2026-11-17", "week": {"start": "2026-11-16", "realized": -4_100.0, "brake": True},
            "realized_today": -1_900.0})
        p = pools.load("swing_mom_shadow")
        p["cash"], p["holdings"] = 50_000.0, {"BBB": {"qty": 10, "avg_cost": 900.0, "last_price": 950.0}}
        p["meta"] = {"start_date": "2026-11-17"}
        pools.save(p)
        storage.put_json(pools.PORTFOLIO_KEY, {"peak": 205_000, "history": [{"date": "2026-11-17"}], "breaker": True})
        storage.backend().put("journal/2026-11-17.csv", "timestamp_ist,event\nx,filled\n")

    def test_dry_run_changes_nothing(self):
        with Env(datetime(2026, 11, 17, 23, 45)):
            self._seed()
            before = dict(storage.backend().files)
            out = rl.reset(confirm=False)
            self.assertFalse(out["done"])
            self.assertEqual(storage.backend().files, before)
            self.assertIn("1 open", out["lines"][0])

    def test_reset_keeps_history_and_settings(self):
        with Env(datetime(2026, 11, 17, 23, 45)) as env:
            self._seed()
            out = rl.reset(confirm=True)
            self.assertTrue(out["done"])
            st = storage.get_json(storage.LEDGER_KEY)
            self.assertEqual((st["balance"], st["positions"]), (config.STARTING_CAPITAL, {}))
            self.assertEqual((st["tg_offset"], st["auto_mode"]), (991, True))            # settings kept
            self.assertIn("55", st["msg_index"])                                         # late ratings still map
            self.assertEqual(st["week"], {"start": "2026-11-16", "realized": 0.0, "brake": False})
            for name in pools.POOLS + pools.SHADOW_POOLS:
                p = pools.load(name)
                self.assertEqual((p["cash"], p["holdings"]), (pools.capital(name), {}))
                self.assertNotIn("meta", p)                                              # shadows start afresh
            port = storage.get_json(pools.PORTFOLIO_KEY)
            self.assertEqual((port["history"], port["breaker"]), ([], False))
            self.assertIn("journal/2026-11-17.csv", env.store.files)                     # history kept
            arch = "archive/ledger-reset-2026-11-17/"
            self.assertEqual(storage.get_json(arch + storage.LEDGER_KEY)["balance"], 31_250.0)
            self.assertIn("BBB", storage.get_json(arch + "state/pools/swing_mom_shadow.json")["holdings"])
            # next trading day: a clean day and week; the old week's losses don't trip the weekly brake
            env.clock = datetime(2026, 11, 18, 9, 0).replace(tzinfo=env.clock.tzinfo)
            st = engine.load()
            self.assertEqual((st["day"], st["realized_today"], st["week"]["realized"]), ("2026-11-18", 0.0, 0.0))
            self.assertEqual(st["day_start_balance"], config.STARTING_CAPITAL)


class LedgerStart(unittest.TestCase):
    def test_weekly_counts_from_the_reset(self):
        with mock.patch.object(config, "LEDGER_START", "2026-11-18"):
            self.assertEqual([d.isoformat() for d in weekly.week_days(date(2026, 11, 20))],
                             ["2026-11-18", "2026-11-19", "2026-11-20"])
            self.assertEqual([d.isoformat() for d in weekly.week_days(date(2026, 11, 27))][0], "2026-11-23")


if __name__ == "__main__":
    unittest.main()
