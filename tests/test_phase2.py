"""Phase 2: company news on proposals, market mood, shadow rules, /news, /portfolio, /stop, data alerts."""
import unittest
from datetime import datetime

from helpers import Env, minute_bars

import advisor
import engine


def trend(env, symbol, open_, close):
    env.bars[(symbol, "5m")] = minute_bars(datetime(2026, 11, 9, 9, 15), [(open_,) * 4, (close,) * 4])


class NewsOnProposalsTest(unittest.TestCase):
    def test_worrying_filing_is_flagged_and_listed(self):
        with Env() as env:
            env.add_news("HDFCBANK", "rating", "HDFC Bank has informed the Exchange about Credit Rating downgrade")
            env.add_news("INFY", "order", "Infosys wins a large deal")
            env.prices["HDFCBANK"] = 800.0
            st = engine.load()
            engine.propose(st, "HDFCBANK", "BUY", "ORB long", 800.0, 797.0, 806.0, ["a"], "x")
            msg = env.full()
            self.assertIn("Recent filing to read first: 09 Nov 10:05, credit rating:", msg)
            self.assertIn("News (NSE filings):", msg)
            self.assertIn("https://x/y.pdf", msg)
            self.assertNotIn("Infosys", msg)

    def test_quiet_and_unavailable(self):
        with Env() as env:
            env.news_files["2026-11-09"] = []
            env.prices["ITC"] = 400.0
            st = engine.load()
            engine.propose(st, "ITC", "BUY", "ORB long", 400.0, 398.0, 404.0, ["a"], "x")
            self.assertIn("No NSE filings for this company today or yesterday.", env.full())
        with Env() as env:
            env.prices["ITC"] = 400.0
            st = engine.load()
            engine.propose(st, "ITC", "BUY", "ORB long", 400.0, 398.0, 404.0, ["a"], "x")
            self.assertIn("News: unavailable right now", env.full())

    def test_board_meeting_soon(self):
        with Env() as env:
            env.add_news("TCS", "board_meeting", "Board meeting will be held on 10-Nov-2026 to consider results")
            ok, text, pts = advisor.news_check("TCS")
            self.assertIn("Board meeting on 10 Nov", text)
            self.assertEqual(pts, -5)

    def test_previous_trading_day_counts(self):
        with Env() as env:   # Monday 9 Nov: the previous trading day is Friday 6 Nov
            env.add_news("LT", "order", "L&T bags a large order", when="2026-11-06 16:00:00")
            env.news_files["2026-11-09"] = []
            self.assertEqual(len(engine.news.for_symbol("LT")), 1)


class MoodAndShadowTest(unittest.TestCase):
    def test_mood_labels(self):
        self.assertEqual(advisor.market_mood(0.6), "trending up")
        self.assertEqual(advisor.market_mood(-0.5), "trending down")
        self.assertEqual(advisor.market_mood(0.1, 28), "jumpy")
        self.assertEqual(advisor.market_mood(0.1, 14), "choppy")
        self.assertIsNone(advisor.market_mood(None))

    def test_mood_and_shadow_in_message_and_journal(self):
        with Env() as env:
            env.at(14, 5)
            trend(env, "^NSEI", 24000, 23850)
            env.prices["RELIANCE"] = 1452.0
            st = engine.load()
            st["plan"] = {"vix": 28.2}
            engine.propose(st, "RELIANCE", "BUY", "ORB long", 1452.0, 1441.5, 1473.0, ["a"], "x")
            self.assertIn("Market mood: trending down (Nifty -0.62% since the open, VIX 28.2).", env.full())
            note = [r for r in engine.storage.read_rows("journal/2026-11-09.csv") if r["event"] == "proposed"][0]["note"]
            self.assertIn("mood trending down", note)
            self.assertIn("shadow-block nifty_against_0.3,against_mood,after_14h", note)


class CommandsTest(unittest.TestCase):
    def test_news_portfolio_stop_resume(self):
        with Env(capital=40_000) as env:
            env.add_news("RELIANCE", "results", "Reliance Industries: financial results for the quarter")
            st = engine.load()
            env.text("/news reliance")
            env.text("/portfolio")
            env.text("/stop")
            engine.tg.poll(st, wait=0)
            engine.answer_requests(st)
            news_msg, port, stop = env.sent[-3:]
            self.assertIn("RELIANCE: NSE filings, newest first", news_msg)
            self.assertIn("results: Reliance Industries", news_msg)
            self.assertIn("Swing:     Rs 60,000 of Rs 60,000, not trading yet (rules on hold; to be held in NIFTYBEES)", port)
            self.assertIn("Long-term: Rs 1,00,000 of Rs 1,00,000, not trading yet (shadow mode until the 18 Dec review)", port)
            self.assertIn("Total:     Rs 2,00,000 of Rs 2,00,000", port)
            self.assertIn("Stopped: no new proposals in any pool", stop)
            env.prices["ITC"] = 400.0
            r = engine.propose(st, "ITC", "BUY", "ORB long", 400.0, 398.0, 404.0, ["a"], "x")
            self.assertIn("stopped by you with /stop", r["reason"])
            env.at(10, 45)
            st2 = engine.load()
            st2.update(st)
            env.text("/resume")
            engine.tg.poll(st2, wait=0)
            engine.answer_requests(st2)
            self.assertIn("Resumed", env.sent[-1])
            self.assertFalse(st2.get("stopped"))


class DataHealthTest(unittest.TestCase):
    def test_stale_prices_block_then_clear(self):
        with Env() as env:
            env.news_files["2026-11-09"] = [{"id": "a", "symbol": "X", "fetched_at_ist": "2026-11-09 10:30:00"}]
            st = engine.load()
            engine.data_health(st, {"time": "2026-11-09 10:05"})       # 35 min old at 10:40
            self.assertTrue(st["data_stale"])
            self.assertIn("Data alert: market prices look stale", env.sent[-1])
            env.prices["ITC"] = 400.0
            r = engine.propose(st, "ITC", "BUY", "ORB long", 400.0, 398.0, 404.0, ["a"], "x")
            self.assertIn("prices look stale", r["reason"])
            n = len(env.sent)
            engine.data_health(st, {"time": "2026-11-09 10:05"})       # no repeat
            self.assertEqual(len(env.sent), n)
            engine.data_health(st, {"time": "2026-11-09 10:35"})
            self.assertFalse(st["data_stale"])
            self.assertIn("Prices are fresh again", env.sent[-1])

    def test_news_feed_behind_alert_once(self):
        with Env() as env:
            env.news_files["2026-11-09"] = [{"id": "a", "symbol": "X", "fetched_at_ist": "2026-11-09 08:00:00"}]
            st = engine.load()
            engine.data_health(st, {"time": "2026-11-09 10:35"})
            self.assertIn("company-news feed is 160 minutes old", env.sent[-1])
            n = len(env.sent)
            engine.data_health(st, {"time": "2026-11-09 10:35"})
            self.assertEqual(len(env.sent), n)


if __name__ == "__main__":
    unittest.main()
