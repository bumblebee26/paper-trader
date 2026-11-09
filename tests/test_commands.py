"""Telegram commands: /help (and friends), /plan, /pause, /resume."""
import unittest
from datetime import datetime

from helpers import Env

import engine
import telegram_bot as tg


class CommandsTest(unittest.TestCase):
    def test_help_variants(self):
        for text in ("/help", "/prompts", "commands", "/start", "What can you do?", "/help@mock_26_trader_bot"):
            with Env(datetime(2026, 11, 9, 10, 40)) as env:
                st = engine.load()
                env.text(text)
                engine.monitor(st)
                self.assertTrue(env.sent and env.sent[-1].startswith("[MOCK] paper-trader: what I can do"), text)
                for c in ("/ledger", "/plan", "/exit SYMBOL", "/pause", "/resume", "YES / NO"):
                    self.assertIn(c, env.sent[-1])

    def test_plan(self):
        with Env(datetime(2026, 11, 9, 10, 40)) as env:
            st = engine.load()
            env.text("/plan")
            engine.monitor(st)
            self.assertIn("No plan yet today", env.sent[-1])
            engine.start_day(st, {"risk_per_trade_pct": 0.75, "bias": "gap-up tendency", "vix": 28.2,
                                  "source": "stocks_lakehouse gold tables"}, ["HDFCBANK", "TCS"])
            env.text("/plan")
            engine.monitor(st)
            self.assertIn("Focus: HDFCBANK, TCS", env.sent[-1])
            self.assertIn("risk 0.75%", env.sent[-1])

    def test_pause_blocks_new_proposals_and_resume(self):
        with Env(datetime(2026, 11, 9, 10, 40)) as env:
            st = engine.load()
            env.text("/pause")
            engine.monitor(st)
            self.assertIn("Paused", env.sent[-1])
            r = engine.propose(st, "TCS", "BUY", "ORB long", 3000.0, 2990.0, 3020.0, ["a"], "x")
            self.assertEqual(r["status"], "blocked")
            self.assertIn("paused", r["reason"])
            self.assertTrue(engine.account(st)["paused_by_user"])
            env.text("/resume")
            engine.monitor(st)
            self.assertIn("Resumed", env.sent[-1])
            self.assertEqual(engine.propose(st, "TCS", "BUY", "ORB long", 3000.0, 2990.0, 3020.0, ["a"], "x")["status"],
                             "pending")

    def test_pause_resets_next_day(self):
        with Env(datetime(2026, 11, 9, 10, 40)) as env:
            st = engine.load()
            env.text("/pause")
            engine.monitor(st)
            engine.save(st)
            env.clock = env.clock.replace(day=10)
            self.assertFalse(engine.load()["paused"])

    def test_menu_lists_every_command(self):
        names = [c for c, _ in tg.COMMANDS]
        self.assertEqual(names, ["ledger", "plan", "exit", "pause", "resume", "help"])
        self.assertTrue(all(len(d) <= 256 for _, d in tg.COMMANDS))  # Telegram limit


if __name__ == "__main__":
    unittest.main()
