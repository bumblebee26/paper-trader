"""Your own analysis and trades from Telegram: /analyze, /buy, /short, /swing, /longterm, Confirm, custom marks."""
import unittest
from datetime import datetime, timedelta
from unittest import mock

from helpers import Env

import brief
import custom
import engine
import market
import pools
import storage
import telegram_bot as tg

DAY = datetime(2026, 11, 16, 10, 40)


def rows(event):
    return [r for r in storage.read_rows("journal/2026-11-16.csv") if r["event"] == event]


def send(env, st, text):
    env.text(text)
    tg.poll(st)
    engine.answer_requests(st)


def last_order(st):
    return list(st["custom_orders"].items())[-1]


class ParseTest(unittest.TestCase):
    def test_commands(self):
        self.assertEqual(custom.parse(["BUY", "TCS", "10", "SL", "2,970", "TGT", "3060"]),
                         ("order", {"pool": "intraday", "side": "BUY", "symbol": "TCS", "qty": 10, "sl": 2970.0,
                                    "tgt": 3060.0}))
        self.assertEqual(custom.parse(["SHORT", "M&M"])[1]["side"], "SELL")
        self.assertEqual(custom.parse(["LT", "SELL", "ITC", "3"])[1], {"pool": "long_term", "side": "SELL",
                                                                       "symbol": "ITC", "qty": 3, "sl": None, "tgt": None})
        self.assertEqual(custom.parse(["ANALYZE", "TCS", "SWING"]), ("analyze", {"symbol": "TCS", "view": "swing"}))
        self.assertEqual(custom.parse(["BUY", "TCS", "TEN"])[0], "error")
        self.assertIsNone(custom.parse(["LEDGER"]))


class IntradayOrderTest(unittest.TestCase):
    def test_preview_confirm_and_custom_marks(self):
        with Env(DAY) as env:
            st = engine.load()
            env.prices["TCS"] = 3000.0
            send(env, st, "/buy TCS 10 sl 2970 tgt 3060")
            self.assertIn("Your order · BUY TCS · intraday", env.sent[-1])
            self.assertIn("Buy 10 @ ~3,000.00 now", env.sent[-1])
            oid, _ = last_order(st)
            env.tap("cf", oid)
            tg.poll(st)
            engine.answer_requests(st)
            self.assertEqual(st["positions"]["TCS"]["qty"], 10)
            self.assertEqual(st["trades_today"], 0)                   # not the trader's allowance
            self.assertIn("source custom", rows("proposed")[0]["note"])
            self.assertEqual((rows("filled")[0]["setup"], rows("filled")[0]["user_reply"]), ("Custom", "CUSTOM"))
            self.assertIn("Bought 10 TCS", env.sent[-1])

    def test_defaults_size_to_one_trade_risk_and_warnings(self):
        with Env(DAY) as env:
            st = engine.load()
            env.prices["TCS"] = 3000.0
            send(env, st, "/short TCS")
            _, o = last_order(st)
            self.assertEqual((o["side"], o["sl"], o["tgt"]), ("SELL", 3030.0, 2940.0))
            self.assertEqual(o["qty"], 33)                             # 1R = Rs 1,000 / Rs 30, under the 30% cap
            env.prices["PENNY"] = 12.0
            send(env, st, "/buy PENNY 100")
            self.assertIn("penny stocks", env.sent[-1])
            self.assertIn("not on the trader's own list", env.sent[-1])

    def test_closed_market_expiry_and_price_move(self):
        with Env(datetime(2026, 11, 16, 15, 5)) as env:
            st = engine.load()
            env.prices["TCS"] = 3000.0
            send(env, st, "/buy TCS 5")
            self.assertIn("orders run 09:15–15:00", env.sent[-1])
        with Env(DAY) as env:
            st = engine.load()
            env.prices["TCS"] = 3000.0
            send(env, st, "/buy TCS 5")
            oid, _ = last_order(st)
            env.prices["TCS"] = 3050.0
            env.tap("cf", oid)
            tg.poll(st)
            engine.answer_requests(st)
            self.assertIn("price moved to 3,050.00", env.edits[-1])
            self.assertNotIn("TCS", st["positions"])
            send(env, st, "/buy TCS 5")
            env.clock += timedelta(minutes=4)
            engine.answer_requests(st)
            self.assertIn("Expired", env.edits[-1])
            self.assertEqual(st["custom_orders"], {})


class PoolOrderTest(unittest.TestCase):
    def test_swing_buy_paid_from_niftybees_then_sell_and_stop(self):
        with Env(DAY) as env:
            st = engine.load()
            env.prices["NIFTYBEES"] = 270.0
            pools.park("swing")
            env.prices["TCS"] = 3000.0
            send(env, st, "/swing buy TCS 4 sl 2900 tgt 3300")
            self.assertIn("Paid for by selling", env.sent[-1])
            oid, _ = last_order(st)
            env.tap("cf", oid)
            tg.poll(st)
            engine.answer_requests(st)
            p = pools.load("swing")
            self.assertEqual((p["holdings"]["TCS"]["qty"], p["holdings"]["TCS"]["origin"]), (4, "custom"))
            self.assertEqual(p["holdings"]["TCS"]["stop"], 2900.0)
            trades = rows("pool_trade")
            self.assertEqual([(r["symbol"], r["side"], r["user_reply"]) for r in trades],
                             [("NIFTYBEES", "SELL", "CUSTOM"), ("TCS", "BUY", "CUSTOM")])
            self.assertIn("source custom", trades[-1]["note"])
            env.bars[("TCS", "1d")] = [{"time": "2026-11-16 09:15", "open": 3000, "high": 3010, "low": 2890,
                                        "close": 2895, "volume": 1}]
            done = custom.check_swing_stops("2026-11-16")
            self.assertIn("your stop", done[0])
            self.assertNotIn("TCS", pools.load("swing")["holdings"])

    def test_longterm_buy_and_sell(self):
        with Env(DAY) as env:
            st = engine.load()
            env.prices["ITC"] = 400.0
            send(env, st, "/longterm buy ITC")
            _, o = last_order(st)
            self.assertEqual(o["qty"], 25)                             # 10% of Rs 1 lakh
            env.tap("cf", last_order(st)[0])
            tg.poll(st)
            engine.answer_requests(st)
            self.assertEqual(pools.load("long_term")["holdings"]["ITC"]["qty"], 25)
            env.prices["ITC"] = 420.0
            send(env, st, "/lt sell ITC 10")
            env.tap("cf", last_order(st)[0])
            tg.poll(st)
            engine.answer_requests(st)
            self.assertEqual(pools.load("long_term")["holdings"]["ITC"]["qty"], 15)
            self.assertIn("Sold 10 ITC", env.sent[-1])
            send(env, st, "/swing sell ITC")
            self.assertIn("doesn't hold ITC", env.sent[-1])

    def test_cancel(self):
        with Env(DAY) as env:
            st = engine.load()
            env.prices["ITC"] = 400.0
            send(env, st, "/lt buy ITC 5")
            env.tap("cx", last_order(st)[0])
            tg.poll(st)
            engine.answer_requests(st)
            self.assertIn("Cancelled", env.edits[-1])
            self.assertEqual(pools.load("long_term")["holdings"], {})


class AnalyzeTest(unittest.TestCase):
    def test_three_views(self):
        daily = [{"time": (datetime(2025, 11, 1) + timedelta(days=i)).strftime("%Y-%m-%d 09:15"),
                  "open": 100 + i * 0.2, "high": 101 + i * 0.2, "low": 99 + i * 0.2, "close": 100 + i * 0.2,
                  "volume": 1} for i in range(250)]
        fundamentals = [{"tests_passed": "4", "tests_available": "5", "roce_pct": "22.5", "roa_pct": None,
                         "is_financial": "false", "debt_to_equity": "0.1", "revenue_cagr_3y_pct": "12",
                         "profit_cagr_3y_pct": "15", "pe": "28", "pe_percentile": "40", "profit_down_2q": "false",
                         "rank": "7", "is_pick": "true"}]
        with Env(DAY) as env, mock.patch.object(market, "daily", lambda s, r="1y": daily), \
                mock.patch.object(brief, "sql", lambda q, max_wait=240: fundamentals):
            st = engine.load()
            env.bars[("TCS", "5m")] = [{"time": "2026-11-16 09:15", "open": 3000, "high": 3010, "low": 2990,
                                        "close": 3005, "volume": 1000},
                                       {"time": "2026-11-16 09:20", "open": 3005, "high": 3020, "low": 3000,
                                        "close": 3015, "volume": 1200}]
            send(env, st, "/analyze TCS")
            msg = env.sent[-1]
            for part in ("Analysis: TCS", "Intraday (09:20): Rs 3,015.00", "Live checks: BUY",
                         "Swing (daily): uptrend", "Long-term: passes 4 of 5 quality tests",
                         "return on capital 22%", "This week's long-term rank: 7 (one of the 10 picks)", "/swing buy TCS"):
                self.assertIn(part, msg)


if __name__ == "__main__":
    unittest.main()
