"""Ledger, risk gate, approvals, fills, exits, day plan, sessions and the guardian Lambda."""
import json
import unittest
from datetime import datetime

from helpers import Env, minute_bars

import config
import engine
import handlers
import storage


def propose_reliance(state):
    return engine.propose(state, "RELIANCE", "BUY", "ORB long", 1452.0, 1441.5, 1473.0,
                          ["broke OR high 1449.8 on 1.8x vol", "above VWAP 1446.2"], "5m close < 1446")


class EngineTest(unittest.TestCase):
    def test_new_ledger_starts_with_one_lakh(self):
        with Env():
            st = engine.load()
            self.assertEqual(st["balance"], 100_000)
            self.assertEqual(st["day_risk_pct"], config.RISK_PER_TRADE_PCT)

    def test_full_trade_cycle_to_target(self):
        with Env() as env:
            env.prices["RELIANCE"] = 1452.0
            st = engine.load()
            r = propose_reliance(st)
            self.assertEqual(r["status"], "pending")
            pid = r["proposal_id"]
            self.assertEqual(r["qty"], 95)  # floor(1000 / 10.5)
            self.assertTrue(env.sent[0].startswith("[MOCK] BUY RELIANCE"))

            env.tap("yes", pid, user="999")  # stranger: ignored
            self.assertEqual(engine.wait_decision(st, pid, 1)["status"], "pending")
            env.tap("yes", pid)
            self.assertEqual(engine.wait_decision(st, pid, 1)["status"], "approved")

            fill = engine.execute(st, pid)
            self.assertEqual(fill["status"], "filled")
            self.assertGreater(fill["fill_price"], 1452.0)  # slippage is against us
            self.assertEqual(engine.execute(st, pid)["status"], "refused")  # only once

            env.bars[("RELIANCE", "1m")] = minute_bars(datetime(2026, 11, 9, 10, 41),
                                                       [(1453, 1455, 1450, 1454), (1458, 1475, 1457, 1474)])
            env.at(10, 45)
            events = engine.monitor(st)["events"]
            self.assertEqual(events[0]["status"], "closed")
            self.assertGreater(events[0]["pnl_rs"], 0)
            self.assertGreater(st["balance"], 100_000)
            engine.save(st)
            rows = storage.read_rows("journal/2026-11-09.csv")
            self.assertEqual([r["event"] for r in rows], ["proposed", "filled", "exited"])

    def test_stop_checked_before_target_in_same_bar(self):
        with Env() as env:
            env.prices["RELIANCE"] = 1452.0
            st = engine.load()
            pid = propose_reliance(st)["proposal_id"]
            env.tap("yes", pid)
            engine.wait_decision(st, pid, 1)
            engine.execute(st, pid)
            env.bars[("RELIANCE", "1m")] = minute_bars(datetime(2026, 11, 9, 10, 41), [(1450, 1480, 1430, 1460)])
            ev = engine.monitor(st)["events"][0]
            self.assertLess(ev["pnl_rs"], 0)

    def test_blocked_outside_entry_window(self):
        with Env() as env:
            env.at(14, 45)
            st = engine.load()
            self.assertEqual(propose_reliance(st)["status"], "blocked")

    def test_no_reply_expires(self):
        with Env() as env:
            st = engine.load()
            pid = propose_reliance(st)["proposal_id"]
            env.at(10, 45)
            self.assertEqual(engine.wait_decision(st, pid, 1)["status"], "expired")
            self.assertEqual(engine.execute(st, pid)["status"], "refused")

    def test_typed_no_rejects_latest(self):
        with Env() as env:
            st = engine.load()
            pid = propose_reliance(st)["proposal_id"]
            env.text("no")
            self.assertEqual(engine.wait_decision(st, pid, 1)["status"], "rejected")

    def test_start_day_caps_risk_and_halves_size(self):
        with Env():
            st = engine.load()
            out = engine.start_day(st, {"risk_per_trade_pct": 5.0, "bias": "neutral"}, ["reliance"])
            self.assertEqual(out["risk_per_trade_pct"], config.RISK_PER_TRADE_PCT)  # capped
            engine.start_day(st, {"risk_per_trade_pct": 0.5, "bias": "neutral"}, ["RELIANCE"])
            self.assertEqual(propose_reliance(st)["qty"], 47)  # floor(500 / 10.5)
            self.assertTrue(storage.exists("plans/2026-11-09.json"))

    def test_new_day_resets_plan(self):
        with Env() as env:
            st = engine.load()
            engine.start_day(st, {"risk_per_trade_pct": 0.5}, ["TCS"])
            engine.save(st)
            env.clock = env.clock.replace(day=10)
            st2 = engine.load()
            self.assertEqual(st2["day_risk_pct"], config.RISK_PER_TRADE_PCT)
            self.assertEqual(st2["focus"], [])

    def test_claim_session_once_per_day(self):
        with Env():
            self.assertTrue(engine.claim_session("morning"))
            self.assertFalse(engine.claim_session("morning"))
            self.assertTrue(engine.claim_session("afternoon"))


class GuardianTest(unittest.TestCase):
    def _open_position(self, env):
        env.prices["RELIANCE"] = 1452.0
        st = engine.load()
        pid = propose_reliance(st)["proposal_id"]
        env.tap("yes", pid)
        engine.wait_decision(st, pid, 1)
        engine.execute(st, pid)
        engine.save(st)

    def test_squareoff_skips_when_session_alive(self):
        with Env() as env:
            self._open_position(env)
            env.at(15, 12)
            engine.heartbeat()
            self.assertEqual(handlers.guardian({"action": "squareoff"}, None), {"skipped": "session alive"})

    def test_squareoff_closes_when_session_dead(self):
        with Env() as env:
            self._open_position(env)
            engine.heartbeat()
            env.at(15, 12)
            out = handlers.guardian({"action": "squareoff"}, None)
            self.assertEqual(out["squared_off"][0]["status"], "closed")
            self.assertEqual(json.loads(storage.backend().get("state/ledger.json"))["positions"], {})

    def test_report_once(self):
        with Env() as env:
            self._open_position(env)
            env.at(15, 40)
            self.assertIn("report", handlers.guardian({"action": "report"}, None))
            self.assertEqual(handlers.guardian({"action": "report"}, None), {"skipped": "report already sent"})



class KickerTest(unittest.TestCase):
    def test_dispatches_workflow_with_phase(self):
        import os
        from unittest import mock
        calls = []
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_test", "GITHUB_REPO": "bumblebee26/paper-trader"}), \
                mock.patch.object(handlers, "request", lambda *a, **k: calls.append((a, k)) or b""):
            self.assertEqual(handlers.kicker({"phase": "afternoon"}, None), {"dispatched": "afternoon"})
        (method, url), kw = calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/repos/bumblebee26/paper-trader/actions/workflows/trading-session.yml/dispatches"))
        self.assertEqual(json.loads(kw["data"]), {"ref": "main", "inputs": {"phase": "afternoon"}})
        self.assertNotIn("ghp_test", kw["log_url"])


if __name__ == "__main__":
    unittest.main()
