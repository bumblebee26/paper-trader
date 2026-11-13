"""News radar: tradable Nifty 200 names beyond the watchlist, guardrails, news alerts, the news label,
the swing shadow pool and /shadow."""
import json
import unittest
from datetime import datetime

from helpers import Env, minute_bars

import engine
import export
import pools
import storage
import swing_shadow
import telegram_bot as tg

DAY = datetime(2026, 11, 16, 10, 40)


def plan_with(universe=("GODREJIND",), radar=("GODREJIND",)):
    return {"tradable_universe": list(universe), "news_radar": [{"symbol": s} for s in radar],
            "risk_per_trade_pct": 1.0}


class GuardrailTest(unittest.TestCase):
    def test_only_watchlist_or_the_tradable_universe(self):
        with Env(DAY) as env:
            st = engine.load()
            env.prices["GODREJIND"] = 1083.2
            r = engine.propose(st, "GODREJIND", "SELL", "ORB short", 1083.2, 1090.25, 1069.1, ["a"], "x")
            self.assertEqual(r["status"], "blocked")
            self.assertIn("isn't on the tradable list", r["reason"])
            engine.save_universe(plan_with())
            engine.start_day(st, plan_with(), ["HDFCBANK", "GODREJIND"])
            r = engine.propose(st, "GODREJIND", "SELL", "ORB short", 1083.2, 1090.25, 1069.1, ["a"], "x")
            self.assertEqual(r["status"], "pending")
            self.assertIn("📰 news pick", env.sent[-1])
            note = [x for x in storage.read_rows("journal/2026-11-16.csv") if x["event"] == "proposed"][-1]["note"]
            self.assertIn("source news", note)
            r = engine.propose(st, "HDFCBANK", "BUY", "ORB long", 1600.0, 1584.0, 1632.0, ["a"], "x")
            note = [x for x in storage.read_rows("journal/2026-11-16.csv") if x["event"] == "proposed"][-1]["note"]
            self.assertIn("source watchlist", note)

    def test_no_penny_stocks_and_no_frozen_prices(self):
        with Env(DAY) as env:
            st = engine.load()
            engine.save_universe(plan_with(universe=("CHEAPCO", "LOCKED")))
            env.prices["CHEAPCO"] = 42.0
            r = engine.propose(st, "CHEAPCO", "BUY", "ORB long", 42.0, 41.0, 44.0, ["a"], "x")
            self.assertIn("no penny stocks", r["reason"])
            env.bars[("LOCKED", "1m")] = minute_bars(datetime(2026, 11, 16, 10, 30), [(500, 500, 500, 500)] * 10)
            env.prices["LOCKED"] = 500.0
            r = engine.propose(st, "LOCKED", "BUY", "ORB long", 500.0, 495.0, 510.0, ["a"], "x")
            self.assertIn("stuck at its daily price limit", r["reason"])


class NewsAlertTest(unittest.TestCase):
    def test_fresh_filings_join_the_scan_once(self):
        with Env(DAY) as env:
            st = engine.load()
            engine.save_universe(plan_with(universe=("GODREJIND",), radar=()))
            engine.start_day(st, plan_with(radar=()), ["HDFCBANK"])
            env.add_news("GODREJIND", "mna", "Acquisition of a stake", when="2026-11-16 10:31:00")
            env.add_news("PENNYCO", "order", "Big order", when="2026-11-16 10:32:00")     # not tradable
            env.add_news("TCS", "other", "Trading window closure", when="2026-11-16 10:33:00")  # routine
            env.add_news("INFY", "results", "Old results", when="2026-11-13 16:00:00")  # not today
            alerts = engine.news_alerts(st)
            self.assertEqual([a["symbol"] for a in alerts], ["GODREJIND"])
            self.assertEqual(st["focus_news"], ["GODREJIND"])
            self.assertTrue(engine.is_news_pick(st, "GODREJIND"))
            self.assertEqual(engine.news_alerts(st), [])                         # each filing only once


class SwingShadowTest(unittest.TestCase):
    def signal(self, sym="BEL", d="2026-11-13", stop=380.0):
        return {"d": d, "symbol": sym, "company": sym, "industry": "Defence", "close": 400.0, "return_pct": 4.2,
                "volume_ratio": 3.1, "stop": stop, "news_category": "order", "news_title": "Order win"}

    def daily_bar(self, day, o, h, low, c):
        return [{"time": f"{day} 09:15", "open": o, "high": h, "low": low, "close": c, "volume": 1}]

    def test_entry_at_open_then_target_stop_or_ten_days(self):
        with Env(datetime(2026, 11, 16, 15, 50)) as env:
            env.bars[("NIFTYBEES", "1d")] = self.daily_bar("2026-11-16", 270, 272, 269, 271)
            env.bars[("BEL", "1d")] = self.daily_bar("2026-11-16", 402, 410, 398, 408)
            env.bars[("LOWCO", "1d")] = self.daily_bar("2026-11-16", 90, 95, 89, 94)
            snap = swing_shadow.daily("2026-11-16", [self.signal(), self.signal("LOWCO", stop=92.0),
                                                     self.signal("OLDCO", d="2026-11-12")])
            self.assertTrue(any(m.startswith("BUY") and "BEL" in m for m in snap["moves"]))
            self.assertTrue(any("LOWCO" in m and "below its stop" in m for m in snap["moves"]))
            self.assertFalse(any("OLDCO" in m for m in snap["moves"]))          # an old signal: not traded
            p = pools.load("swing_shadow")
            h = p["holdings"]["BEL"]
            self.assertEqual((h["stop"], h["target"]), (380.0, 446.0))          # 2x the Rs 22 risk
            self.assertLessEqual(h["qty"] * 402 * 1.001, 60_000 * 0.205)         # at most ~20% of the pool
            env.clock = datetime(2026, 11, 17, 15, 50)
            env.prices["NIFTYBEES"] = 273.0
            env.bars[("BEL", "1d")] = self.daily_bar("2026-11-17", 409, 447, 405, 440)
            snap = swing_shadow.daily("2026-11-17", [])
            self.assertTrue(any("SELL BEL" in m and "target" in m for m in snap["moves"]))
            self.assertGreater(pools.load("swing_shadow")["realized"], 0)
            self.assertEqual(snap["start_date"], "2026-11-16")
            self.assertIn("vs NIFTYBEES", swing_shadow.lines(snap)[0])
            env.clock = datetime(2026, 11, 18, 15, 50)
            env.bars[("BEL", "1d")] = self.daily_bar("2026-11-18", 402, 410, 398, 408)
            snap = swing_shadow.daily("2026-11-18", [self.signal(d="2026-11-17")])
            self.assertFalse(any("BEL" in m for m in snap["moves"]))           # bought within 10 days: not again

    def test_stop_first_on_a_wide_day(self):
        with Env(datetime(2026, 11, 16, 15, 50)) as env:
            env.bars[("BEL", "1d")] = self.daily_bar("2026-11-16", 402, 450, 375, 400)
            snap = swing_shadow.daily("2026-11-16", [self.signal()])
            self.assertTrue(any("same day at its stop" in m for m in snap["moves"]))


class ShadowViewTest(unittest.TestCase):
    def test_shadow_command_and_export(self):
        with Env(datetime(2026, 11, 16, 15, 50)) as env:
            env.bars[("BEL", "1d")] = [{"time": "2026-11-16 09:15", "open": 402, "high": 410, "low": 398,
                                        "close": 408, "volume": 1}]
            swing_shadow.daily("2026-11-16", [{"d": "2026-11-13", "symbol": "BEL", "close": 400.0,
                                               "return_pct": 4.2, "volume_ratio": 3.1, "stop": 380.0,
                                               "news_category": "order", "news_title": "Order win"}])
            storage.put_json("shadow/longterm/latest.json", {"week": 2, "equity": 101_500.0, "return_pct": 1.5,
                                                             "nifty_return_pct": 0.8})
            st = engine.load()
            env.text("/shadow")
            tg.poll(st)
            engine.answer_requests(st)
            msg = env.sent[-1]
            for part in ("Tested on paper", "Swing (news breakouts) since 2026-11-16", "holding BEL",
                         "Long-term (weekly quality picks), week 2: +Rs 1,500 (+1.50%) vs NIFTYBEES +0.80%",
                         "gold.shadow_pools_daily"):
                self.assertIn(part, msg)
            env.prices["BEL"] = 410.0
            rows = [json.loads(x) for x in export.build_portfolio("2026-11-16").splitlines()]
            shadow = [r for r in rows if r["record_type"] == "shadow_pool"]
            self.assertEqual([r["pool"] for r in shadow], ["swing_shadow"])
            self.assertTrue(any(r["record_type"] == "shadow_holding" and r["symbol"] == "BEL" for r in rows))
            self.assertFalse(any(r["record_type"] == "pool" and "shadow" in r["pool"] for r in rows))


if __name__ == "__main__":
    unittest.main()
