"""Phase 1 intraday limits: Rs 4,000 loss limit, 10 trades, cool-off, sector and position caps."""
import unittest

from helpers import Env

import config
import engine
import sectors


def open_trade(env, st, symbol, entry=1000.0, stop=995.0, target=1010.0):
    env.prices[symbol] = entry
    r = engine.propose(st, symbol, "BUY", "ORB long", entry, stop, target, ["a"], "x")
    if r["status"] != "pending":
        return r
    env.tap("yes", r["proposal_id"])
    engine.wait_decision(st, r["proposal_id"], 1)
    engine.execute(st, r["proposal_id"])
    return r


class Phase1Test(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual((config.STARTING_CAPITAL, config.SWING_CAPITAL, config.LONG_TERM_CAPITAL),
                         (40_000, 60_000, 100_000))
        self.assertEqual(config.MAX_TRADES_PER_DAY, 10)
        self.assertEqual((config.DAILY_LOSS_LIMIT_R, config.DAILY_LOSS_LIMIT_RS), (4.0, 0))
        self.assertEqual((config.WEEKLY_LOSS_LIMIT_R, config.WEEKLY_BRAKE_FACTOR), (8.0, 0.5))
        self.assertEqual(config.MAX_COST_SHARE_PCT, 30.0)
        self.assertEqual(len(sectors.WATCHLIST), 36)
        self.assertGreaterEqual(len({sectors.sector(s) for s in sectors.WATCHLIST}), 10)

    def test_fixed_rupee_loss_limit_still_works(self):
        with Env():  # the test setup pins DAILY_LOSS_LIMIT_RS = 4,000
            st = engine.load()
            st["realized_today"] = -3_999.0
            self.assertFalse(engine.loss_limit_reached(st))
            st["realized_today"] = -4_000.0
            self.assertTrue(engine.loss_limit_reached(st))

    def test_one_open_position_per_sector(self):
        with Env() as env:
            st = engine.load()
            open_trade(env, st, "HDFCBANK")
            r = open_trade(env, st, "ICICIBANK")
            self.assertEqual(r["status"], "blocked")
            self.assertIn("already holding HDFCBANK in Banks", r["reason"])
            self.assertEqual(open_trade(env, st, "INFY")["status"], "pending")

    def test_two_trades_per_sector_per_day(self):
        with Env() as env:
            st = engine.load()
            open_trade(env, st, "HDFCBANK")
            engine.close(st, "HDFCBANK", 1010.0, "target")
            open_trade(env, st, "SBIN")
            engine.close(st, "SBIN", 1010.0, "target")
            r = open_trade(env, st, "AXISBANK")
            self.assertEqual(r["status"], "blocked")
            self.assertIn("already 2 Banks trades today", r["reason"])

    def test_position_capped_at_30_percent_margin(self):
        with Env() as env:
            st = engine.load()
            open_trade(env, st, "HDFCBANK", 792.70, 789.50, 799.10)   # 1% risk would be 312 shares
            pos = st["positions"]["HDFCBANK"]
            self.assertLessEqual(pos["qty"] * 792.70 / config.INTRADAY_LEVERAGE, 30_000)
            self.assertEqual(pos["qty"], 189)

    def test_cooloff_after_three_losses(self):
        with Env() as env:
            st = engine.load()
            for sym in ("HDFCBANK", "INFY", "RELIANCE"):
                open_trade(env, st, sym)
                engine.close(st, sym, 995.0, "stop_loss")
            self.assertIn("3 losses in a row, so a 30-minute cool-off", env.sent[-1])
            self.assertIn("until 11:10", env.sent[-1])
            r = open_trade(env, st, "ITC")
            self.assertEqual(r["status"], "blocked")
            self.assertIn("cool-off after 3 losses in a row, until 11:10", r["reason"])
            env.at(11, 11)
            self.assertEqual(open_trade(env, st, "ITC")["status"], "pending")

    def test_a_win_resets_the_streak(self):
        with Env() as env:
            st = engine.load()
            for sym, px in (("HDFCBANK", 990.0), ("INFY", 990.0), ("RELIANCE", 1010.0), ("ITC", 990.0)):
                open_trade(env, st, sym)
                engine.close(st, sym, px, "stop_loss" if px < 1000 else "target")
            self.assertEqual(st["loss_streak"], 1)
            self.assertIsNone(st.get("cooloff_until"))

    def test_trade_count_limit(self):
        with Env() as env:
            st = engine.load()
            st["trades_today"] = 10
            r = open_trade(env, st, "ITC")
            self.assertIn("all 10 trades for today are used", r["reason"])


if __name__ == "__main__":
    unittest.main()


class CapitalChangeTest(unittest.TestCase):
    def test_pool_resized_keeps_pnl(self):
        import storage
        from helpers import Env as E
        with E(capital=40_000) as env:
            storage.put_json(storage.LEDGER_KEY, {
                "balance": 98_853.0, "starting_capital": 100_000, "positions": {}, "proposals": {},
                "decisions": {}, "exit_requests": [], "tg_offset": 0, "day": "2026-11-08"})
            st = engine.load()
            self.assertEqual(st["starting_capital"], 40_000)
            self.assertEqual(st["balance"], 38_853.0)
            self.assertEqual(st["day_start_balance"], 38_853.0)
            self.assertIn("resized from Rs 1,00,000 to Rs 40,000", env.sent[-1])
            engine.save(st)
            engine.load()
            self.assertEqual(sum("resized" in m for m in env.sent), 1)
