"""Holdings news alert (24 Nov): today's NSE filings on stocks the swing / long-term pools hold, sent once each."""
import unittest

from helpers import Env

import engine
import pools


class HeldNewsTest(unittest.TestCase):
    def test_sent_once_with_why_and_only_for_held_stocks(self):
        with Env(capital=40_000) as env:
            env.at(11, 0)
            p = pools.load("swing")
            pools.buy(p, "INFY", 5, 1500.0, reason="breakout")
            pools.save(p)
            q = pools.load("long_term")
            pools.buy(q, "INFY", 3, 1500.0, reason="quality")
            pools.buy(q, "TCS", 2, 4000.0, reason="quality")
            pools.save(q)
            env.add_news("INFY", "pledge", "Promoter pledge created", when="2026-11-09 10:05:00")
            env.add_news("SBIN", "results", "Results", when="2026-11-09 10:06:00")         # not held
            env.add_news("TCS", "results", "Old results", when="2026-11-08 15:00:00")     # yesterday
            st = engine.load()
            out = engine.held_news(st)
            self.assertEqual(len(out), 1)
            self.assertIn("INFY ⚠️ (Swing and Long term pool)", out[0])
            self.assertIn("Why it matters: owners pledging shares", out[0])
            self.assertIn("News on stocks the pools hold", env.sent[-1])
            n = len(env.sent)
            self.assertEqual(engine.held_news(st), [])     # never repeated
            self.assertEqual(len(env.sent), n)

    def test_nothing_held_or_feed_down_sends_nothing(self):
        with Env(capital=40_000) as env:
            env.at(11, 0)
            env.add_news("INFY", "results", "Results", when="2026-11-09 10:05:00")
            st = engine.load()
            self.assertEqual(engine.held_news(st), [])
            self.assertEqual(env.sent, [])


if __name__ == "__main__":
    unittest.main()
