"""Loss limits in R (one trade's planned risk): daily limit incl. open risk, weekly brake, cost filter."""
import unittest
from contextlib import ExitStack
from datetime import datetime
from unittest import mock

from helpers import Env

import config
import engine
import storage


def real_limits(stack):
    """The live settings (the shared test setup pins older mechanics)."""
    for name, value in (("DAILY_LOSS_LIMIT_RS", 0), ("DAILY_LOSS_LIMIT_R", 4.0), ("WEEKLY_LOSS_LIMIT_R", 8.0),
                        ("WEEKLY_BRAKE_FACTOR", 0.5), ("MAX_COST_SHARE_PCT", 30.0),
                        ("MAX_POSITION_MARGIN_PCT", 100.0)):
        stack.enter_context(mock.patch.object(config, name, value))


def open_trade(env, st, symbol, entry, stop, target):
    env.prices[symbol] = entry
    r = engine.propose(st, symbol, "BUY", "ORB long", entry, stop, target, ["a"], "x")
    if r["status"] != "pending":
        return r
    env.tap("yes", r["proposal_id"])
    engine.wait_decision(st, r["proposal_id"], 1)
    engine.execute(st, r["proposal_id"])
    return r


class RiskLimitsTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)

    def env(self, when=datetime(2026, 11, 11, 10, 40)):
        env = self.stack.enter_context(Env(when))
        real_limits(self.stack)
        return env

    def test_daily_limit_is_four_trades_risk_and_follows_vix(self):
        self.env()
        st = engine.load()
        self.assertEqual(engine.one_r(st), 1000.0)            # 1% of Rs 1 lakh
        self.assertEqual(engine.loss_limit_rs(st), 4000.0)
        st["day_risk_pct"] = 0.75                             # VIX >= 20
        self.assertEqual(engine.loss_limit_rs(st), 3000.0)

    def test_open_risk_counts_and_shrinks_the_next_trade(self):
        env = self.env()
        st = engine.load()
        open_trade(env, st, "HDFCBANK", 1600.0, 1584.0, 1632.0)      # ~Rs 1,000 at risk
        st["realized_today"] = -2500.0
        room = engine.loss_room(st)
        self.assertLess(room, 600)                                    # 4,000 - 2,500 - open risk
        r = open_trade(env, st, "TCS", 3000.0, 2970.0, 3060.0)        # wants Rs 1,000; gets what fits
        self.assertEqual(r["status"], "pending")
        self.assertLessEqual(st["proposals"][r["proposal_id"]]["risk_rs"], room)
        st["realized_today"] = -3000.0                                # nothing left for a Rs 30 stop
        r = engine.propose(st, "RELIANCE", "BUY", "ORB long", 1500.0, 1470.0, 1560.0, ["a"], "x")
        self.assertEqual(r["status"], "blocked")
        self.assertIn("no room left under the daily loss limit", r["reason"])

    def test_cost_filter_blocks_stops_too_tight_for_the_fees(self):
        env = self.env()
        st = engine.load()
        env.prices["TCS"] = 3000.0
        r = engine.propose(st, "TCS", "SELL", "VWAP rejection", 3000.0, 3010.0, 2980.0, ["a"], "x")  # 0.33% stop
        self.assertEqual(r["status"], "blocked")
        self.assertIn("fees and slippage would be", r["reason"])
        ok = open_trade(env, st, "HDFCBANK", 1600.0, 1584.0, 1632.0)  # 1% stop: costs ~13% of risk
        self.assertEqual(ok["status"], "pending")
        self.assertLess(engine.cost_share_pct(1600.0, 16.0, 62), 30)

    def test_weekly_brake_halves_risk_until_monday(self):
        env = self.env()
        st = engine.load()
        st["week"]["realized"] = -7500.0                              # Mon-Tue
        open_trade(env, st, "HDFCBANK", 1600.0, 1584.0, 1632.0)
        engine.close(st, "HDFCBANK", 1584.0, "stop_loss")             # week now past -8R (Rs 8,000)
        self.assertTrue(engine.weekly_brake_on(st))
        self.assertTrue(any("Weekly brake" in m for m in env.sent))
        ok, _, qty, risk, _ = engine.risk_gate(st, "TCS", "BUY", 3000.0, 2970.0, 3060.0)
        self.assertTrue(ok)
        self.assertLessEqual(risk, 500.0)                             # half of the usual Rs 1,000
        st["day_risk_pct"] = 0.75                                     # VIX 25 and the brake: Rs 375 a trade
        ok, why, _, _, _ = engine.risk_gate(st, "TCS", "BUY", 3000.0, 2976.0, 3048.0)  # 0.8% stop
        self.assertTrue(ok, why)                                      # the brake halves size, not the setups
        self.assertIn("weekly brake ON", engine.ledger_statement(st))

        engine.save(st)                                               # next Monday: a new week, brake off
        env.clock = env.clock.replace(day=16)
        st = engine.load()
        self.assertFalse(engine.weekly_brake_on(st))
        self.assertEqual(st["week"]["start"], "2026-11-16")
        self.assertEqual(engine.week_realized(st), 0.0)

    def test_week_counts_earlier_days_from_the_journal(self):
        env = self.env()
        storage.append_row("journal/2026-11-09.csv", engine.JOURNAL_COLUMNS,
                           {"event": "filled", "fees_rs": 20, "event_id": "a"})
        storage.append_row("journal/2026-11-09.csv", engine.JOURNAL_COLUMNS,
                           {"event": "exited", "pnl_rs": -2316, "event_id": "b"})
        st = engine.load()                                            # first run with the week counter: Wed
        self.assertEqual(st["week"]["realized"], -2336.0)
        engine.save(st)
        st["realized_today"] = -400.0
        engine.save(st)
        env.clock = env.clock.replace(day=12)                         # Thursday adds Wednesday's result
        st = engine.load()
        self.assertEqual(st["week"]["realized"], -2736.0)

    def test_ledger_shows_limit_and_room(self):
        self.env()
        st = engine.load()
        text = engine.ledger_statement(st)
        self.assertIn("Loss limit today: -Rs 4,000 (4 trades' risk of Rs 1,000) | room left Rs 4,000", text)
        self.assertIn("This week:", text)


if __name__ == "__main__":
    unittest.main()
