"""Ledger details on Telegram: proposal margin line, fill/close ledger lines, LEDGER statement."""
import unittest
from datetime import datetime

from helpers import Env, minute_bars

import engine


def hdfc_trade(env):
    """Today's real example: BUY HDFCBANK 234 @ ~792.70, risk 0.75%."""
    env.prices["HDFCBANK"] = 792.70
    st = engine.load()
    engine.start_day(st, {"risk_per_trade_pct": 0.75}, ["HDFCBANK"])
    pid = engine.propose(st, "HDFCBANK", "BUY", "VWAP reclaim", 792.70, 789.50, 799.10, ["a"], "x")["proposal_id"]
    env.tap("yes", pid)
    engine.wait_decision(st, pid, 1)
    engine.execute(st, pid)
    return st, pid


class LedgerTest(unittest.TestCase):
    def test_indian_rupees(self):
        self.assertEqual(engine.inr(100000), "Rs 1,00,000")
        self.assertEqual(engine.inr(1234567.4), "Rs 12,34,567")
        self.assertEqual(engine.inr(-20, sign=True), "-Rs 20")
        self.assertEqual(engine.inr(1500, sign=True), "+Rs 1,500")

    def test_proposal_and_fill_show_money(self):
        with Env(datetime(2026, 11, 9, 9, 50)) as env:
            st, _ = hdfc_trade(env)
            proposal, fill_msg = env.sent[-2], env.sent[-1]
            self.assertIn("Qty 234", proposal)
            self.assertIn("Uses Rs 37,098 margin of Rs 1,00,000 free", proposal)
            self.assertIn("Ledger: balance Rs 99,980 | in use Rs 37,117 (1 position) | free Rs 62,863 | today -Rs 20",
                          fill_msg)

    def test_close_shows_ledger(self):
        with Env(datetime(2026, 11, 9, 9, 50)) as env:
            st, _ = hdfc_trade(env)
            env.bars[("HDFCBANK", "1m")] = minute_bars(datetime(2026, 11, 9, 9, 51), [(795, 799.5, 794, 799)])
            env.at(9, 55)
            engine.monitor(st)
            msg = env.sent[-1]
            self.assertIn("Closed HDFCBANK (target)", msg)
            self.assertIn("in use Rs 0 (0 positions)", msg)
            self.assertIn("free Rs 1,01,", msg)

    def test_ledger_command_statement(self):
        with Env(datetime(2026, 11, 9, 9, 50)) as env:
            st, _ = hdfc_trade(env)
            env.prices["HDFCBANK"] = 795.0
            env.text("ledger")
            engine.monitor(st)
            stmt = next(m for m in env.sent if m.startswith("[MOCK] Ledger at"))
            for part in ("Starting capital: Rs 1,00,000", "In use (margin):  Rs 37,117",
                         "Free to trade:    Rs 62,863", "Exposure:         Rs 1,85,585 (5x intraday)",
                         "BUY HDFCBANK 234 @ 793.10, now 795.00: +Rs 445", "Unrealized: +Rs 445"):
                self.assertIn(part, stmt)
            self.assertFalse(st.get("requests"))

    def test_slash_commands(self):
        with Env(datetime(2026, 11, 9, 9, 50)) as env:
            st, _ = hdfc_trade(env)
            env.text("/LEDGER")
            env.text("/exit@mock_26_trader_bot hdfcbank")
            engine.monitor(st)
            self.assertTrue(any(m.startswith("[MOCK] Ledger at") for m in env.sent))
            self.assertNotIn("HDFCBANK", st["positions"])  # /exit closed it


if __name__ == "__main__":
    unittest.main()
