"""Swing and long-term pool ledgers, the portfolio circuit breaker, and the Friday weekly report."""
import unittest
from datetime import date

from helpers import Env

import engine
import pools
import storage
import weekly


class PoolsTest(unittest.TestCase):
    def test_new_pools_start_with_their_capital(self):
        with Env(capital=40_000):
            self.assertEqual(pools.load("swing")["cash"], 60_000)
            self.assertEqual(pools.load("long_term")["cash"], 100_000)
            with self.assertRaises(ValueError):
                pools.load("crypto")

    def test_buy_and_sell_with_delivery_costs(self):
        with Env(capital=40_000):
            p = pools.load("swing")
            r = pools.buy(p, "INFY", 9, 1500.0, reason="breakout")
            self.assertEqual(r["status"], "bought")
            self.assertEqual(r["fill"], 1500.75)                       # 0.05% slippage
            self.assertEqual(r["fees_rs"], round(9 * 1500.75 * 0.0012, 2))
            pools.save(p)
            p = pools.load("swing")
            out = pools.sell(p, "INFY", price=1650.0, reason="target")
            self.assertEqual(out["status"], "sold")
            self.assertGreater(out["pnl_rs"], 1200)
            self.assertNotIn("INFY", p["holdings"])
            self.assertEqual(p["realized"], out["pnl_rs"])
            self.assertAlmostEqual(p["cash"], 60_000 + out["pnl_rs"], places=1)
            rows = storage.read_rows("journal/pools/swing/2026-11-09.csv")
            self.assertEqual([r["event"] for r in rows], ["bought", "sold"])

    def test_limits(self):
        with Env(capital=40_000):
            p = pools.load("swing")
            self.assertIn("over 25% of the pool", pools.buy(p, "TCS", 7, 3000.0)["reason"])     # Rs 21k > 15k
            self.assertEqual(pools.buy(p, "TCS", 4, 3000.0)["status"], "bought")
            self.assertEqual(pools.buy(p, "INFY", 9, 1500.0)["status"], "bought")
            self.assertIn("already 2 IT holdings", pools.buy(p, "WIPRO", 10, 250.0)["reason"])
            self.assertIn("not enough cash", pools.buy(p, "ITC", 1000, 400.0)["reason"])
            lt = pools.load("long_term")
            self.assertIn("over 15% of the pool", pools.buy(lt, "HDFCBANK", 20, 800.0)["reason"])   # 16k > 15k

    def test_circuit_breaker(self):
        with Env(capital=40_000) as env:
            st = engine.load()
            first = pools.snapshot(st)
            self.assertEqual(first["total"], 200_000)
            self.assertFalse(first["breaker"])
            st["balance"] = 15_000.0                       # total 175k = 12.5% below the 200k peak
            snap = pools.snapshot(st)
            self.assertTrue(snap["breaker"] and snap["tripped_now"])
            env.prices["ITC"] = 400.0
            r = engine.propose(st, "ITC", "BUY", "ORB long", 400.0, 398.0, 404.0, ["a"], "x")
            self.assertIn("portfolio circuit breaker", r["reason"])
            self.assertIn("circuit breaker is on", pools.buy(pools.load("swing"), "ITC", 10, 400.0)["reason"])
            env.text("/resume")
            engine.tg.poll(st, wait=0)
            engine.answer_requests(st)
            self.assertIn("portfolio circuit breaker reset", env.sent[-1])
            self.assertFalse(pools.breaker_on())

    def test_portfolio_command_shows_pool_holdings(self):
        with Env(capital=40_000):
            p = pools.load("long_term")
            pools.buy(p, "ITC", 30, 400.0)
            pools.save(p)
            text = engine.portfolio_text(engine.load())
            self.assertIn("Long-term: Rs 99,986 of Rs 1,00,000, 1 holdings, cash Rs 87,980", text)


class WeeklyTest(unittest.TestCase):
    def _trade(self, env, st, sym, exit_px, reason, ai=None):
        env.prices[sym] = 1000.0
        r = engine.propose(st, sym, "BUY", "ORB long", 1000.0, 995.0, 1010.0, ["a"], "x", ai_yes_pct=ai)
        env.tap("yes", r["proposal_id"])
        engine.wait_decision(st, r["proposal_id"], 1)
        engine.execute(st, r["proposal_id"])
        engine.close(st, sym, exit_px, reason)

    def test_week_in_plain_words(self):
        with Env(capital=40_000) as env:
            st = engine.load()
            self._trade(env, st, "HDFCBANK", 995.0, "stop_loss", ai=10)    # suggestion NO, you said YES, lost
            self._trade(env, st, "INFY", 1010.0, "target", ai=90)          # suggestion YES, won
            env.prices["ITC"] = 400.0
            r = engine.propose(st, "ITC", "BUY", "ORB long", 400.0, 398.0, 404.0, ["a"], "x")
            env.tap("no", r["proposal_id"])
            engine.wait_decision(st, r["proposal_id"], 1)
            env.prices["ITC"] = 396.0
            engine.report(st, send=False)                                  # writes passed_outcome
            out = weekly.run(st, send=True, today=date(2026, 11, 13))
            w, text = out["week"], env.sent[-1]
            self.assertEqual((w["trades"], w["taken"], w["passed"]), (2, 2, 1))
            self.assertIn("Weekly report, 09 Nov to 13 Nov", text)
            self.assertIn("Intraday: 2 trades", text)
            self.assertIn("Only one setup traded: ORB long BUY", text)
            self.assertIn("Intraday: 2 trades, +Rs 91 after fees", text)   # equals the ledger change
            self.assertIn("You took 2 proposals", text)
            self.assertIn("and skipped 1, which would have made", text)
            self.assertIn("You followed the suggestion once", text)
            self.assertIn("Rs 2,00,", text)
            self.assertNotIn("\n\n\n", text)
            self.assertIn("Total Rs", text)
            self.assertTrue(storage.get_json("reports/weekly/2026-11-13.json"))

    def test_empty_week(self):
        with Env(capital=40_000) as env:
            weekly.run(engine.load(), today=date(2026, 11, 13))
            self.assertIn("No intraday trades closed this week.", env.sent[-1])


if __name__ == "__main__":
    unittest.main()
