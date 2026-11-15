"""The lakehouse's technical scanner in the trader: shadow pool 2, /screen, the 09:45 check, the Swing buy
button, the focus bonus, the advisor's check, the proposal note, and market-wide news."""
import json
import unittest
from datetime import datetime
from unittest import mock

from helpers import Env

import brief
import custom
import engine
import pools
import screen
import storage
import swing_tech
import telegram_bot as tg

DAY = datetime(2026, 11, 16, 10, 40)    # Monday; Friday 13 Nov is the scan's session


def row(sym="BEL", list_type="strict", **over):
    r = {"symbol": sym, "company": f"{sym} Ltd.", "trade_date": "2026-11-13", "list_type": list_type, "rank": 1.0,
         "close": 400.0, "setup": "20-day breakout", "stance": "buy-on-dip", "rsi14": 58.0, "macd": 4.1,
         "macd_signal": 3.2, "macd_cross_up": True, "adx14": 24.0, "adx_ok": True, "above20": True,
         "above50": True, "above200": True, "vol_ratio": 2.1, "mfi14": 60.0, "deliv_per": 51.0, "fno": True,
         "entry_low": 395.0, "entry_high": 402.0, "trigger": 395.0, "stop": 380.0, "stop_basis": "swing low",
         "target1": 446.0, "target2": 468.0, "rr": 2.0, "misses": "", "filters_passed": 6.0}
    r.update(over)
    return r


def plan_with_scan(strict=("BEL",), near=("TCS",)):
    return {"risk_per_trade_pct": 1.0, "bias": "uptrend", "bias_side": "BUY",
            "scanner": {"date": "2026-11-13", "strict": [{"symbol": s, "stop": 380.0, "target2": 468.0} for s in strict],
                        "near_miss": [{"symbol": s} for s in near]}}


class ShadowPoolTwoTest(unittest.TestCase):
    def bar(self, day, o, h, low, c):
        return [{"time": f"{day} 09:15", "open": o, "high": h, "low": low, "close": c, "volume": 1}]

    def test_entry_rules_never_chase(self):
        dip = {"stance": "buy-on-dip", "entry_low": 395.0, "entry_high": 402.0}
        self.assertEqual(swing_tech.entry_price(dip, {"open": 398, "high": 405, "low": 396}), 398)   # opened inside
        self.assertEqual(swing_tech.entry_price(dip, {"open": 410, "high": 412, "low": 401}), 402.0)  # dipped in
        self.assertIsNone(swing_tech.entry_price(dip, {"open": 410, "high": 420, "low": 405}))       # never dipped
        self.assertIsNone(swing_tech.entry_price(dip, {"open": 390, "high": 401, "low": 385}))       # broke down
        brk = {"stance": "breakout-only", "trigger": 450.0}
        self.assertEqual(swing_tech.entry_price(brk, {"open": 445, "high": 455, "low": 444}), 450.0)
        self.assertEqual(swing_tech.entry_price(brk, {"open": 452, "high": 455, "low": 449}), 452)    # gapped over
        self.assertIsNone(swing_tech.entry_price(brk, {"open": 440, "high": 449, "low": 438}))
        self.assertIsNone(swing_tech.entry_price({"stance": "wait/avoid"}, {"open": 1, "high": 1, "low": 1}))

    def test_buy_then_t2_exit_and_separate_ledger(self):
        sig = {"d": "2026-11-13", "symbol": "BEL", "setup": "20-day breakout", "stance": "buy-on-dip",
               "entry_low": 395.0, "entry_high": 402.0, "trigger": 395.0, "stop": 380.0, "target1": 446.0,
               "target2": 468.0, "rr": 2.0}
        with Env(datetime(2026, 11, 16, 15, 50)) as env:
            env.bars[("NIFTYBEES", "1d")] = self.bar("2026-11-16", 270, 272, 269, 271)
            env.bars[("BEL", "1d")] = self.bar("2026-11-16", 405, 410, 400, 408)
            env.bars[("FAR", "1d")] = self.bar("2026-11-16", 500, 510, 499, 505)
            snap = swing_tech.daily("2026-11-16", [sig, {**sig, "symbol": "FAR"}])
            self.assertTrue(any(m.startswith("BUY") and "BEL" in m for m in snap["moves"]))
            self.assertTrue(any("NO ENTRY FAR" in m for m in snap["moves"]))
            h = pools.load("swing_tech_shadow")["holdings"]["BEL"]
            self.assertEqual((h["stop"], h["target"]), (380.0, 468.0))
            self.assertEqual(pools.load("swing_shadow")["holdings"], {})          # the news pool is untouched
            env.clock = datetime(2026, 11, 17, 15, 50)
            env.bars[("BEL", "1d")] = self.bar("2026-11-17", 409, 470, 405, 466)
            snap = swing_tech.daily("2026-11-17", [])
            self.assertTrue(any("SELL BEL" in m and "T2" in m for m in snap["moves"]))
            self.assertIn("technical scanner", swing_tech.lines(snap)[0])

    def test_time_limit_is_fifteen_days(self):
        self.assertEqual(swing_tech.HOLD_DAYS, 15)
        self.assertIn("swing_tech_shadow", pools.SHADOW_POOLS)


class ScreenTest(unittest.TestCase):
    def test_status_words(self):
        r = row()
        self.assertEqual(screen.status(r, 398.0, None)[0], "in zone")
        self.assertEqual(screen.status(r, 379.0, None)[0], "invalidated")
        self.assertEqual(screen.status(r, 420.0, None)[0], "extended")
        self.assertEqual(screen.status(r, 405.0, None)[0], "above zone")
        b = row(stance="breakout-only", trigger=410.0, entry_low=410.4, entry_high=416.0)
        self.assertEqual(screen.status(b, 411.0, None)[0], "triggered")
        self.assertEqual(screen.status(b, 404.0, None)[0], "waiting")

    def test_screen_list_and_one_symbol(self):
        with Env(DAY):
            with mock.patch.object(screen, "_sql", return_value=[row(), row("TCS", "near-miss", misses="RSI outside 50-65")]):
                text = screen.text()
            self.assertIn("NSE close Fri 13 Nov", text)
            self.assertIn("BEL Rs 400.00 · 20-day breakout · buy-on-dip", text)
            self.assertIn("TCS Rs 400.00: missed RSI outside 50-65", text)
            self.assertIn("never a guaranteed return", text)
            with mock.patch.object(screen, "_sql", return_value=[row("TCS", "none", misses="volume under 1.5x")]):
                one = screen.text(symbol="tcs")
            self.assertIn("TCS · NSE close Fri 13 Nov: not on the list (6/6 filters)", one)
            self.assertIn("missed: volume under 1.5x", one)

    def test_stale_scan_is_not_shown(self):
        with Env(DAY):
            with mock.patch.object(screen, "_sql", return_value=[row(trade_date="2026-11-12")]):
                text = screen.text()
            self.assertIn("hasn't run yet", text)
            self.assertNotIn("BEL Rs", text)

    def test_expected_session(self):
        with Env(DAY):
            self.assertEqual(screen.expected_session(), "2026-11-13")
            self.assertEqual(screen.expected_session(datetime(2026, 11, 16, 21, 30)), "2026-11-16")
            self.assertEqual(screen.expected_session(datetime(2026, 11, 11, 9, 0)), "2026-11-09")  # 10 Nov holiday

    def test_confirm_at_0945(self):
        with Env(datetime(2026, 11, 16, 9, 45)) as env:
            env.prices.update(BEL=398.0, HAL=379.0)
            with mock.patch.object(screen, "_sql", return_value=[row(), row("HAL")]), \
                 mock.patch.object(engine, "opening_rvol", return_value={"rvol": 2.4}):
                out = screen.confirm()
            self.assertEqual([p["status"] for p in out["picks"]], ["in zone", "invalidated"])
            self.assertIn("BEL in zone", env.sent[-1])
            self.assertIn("opening volume 2.4x", env.sent[-1])
            ev = [r for r in storage.read_rows("journal/2026-11-16.csv") if r["event"] == "scanner_check"]
            self.assertIn("BEL:in_zone", ev[-1]["note"])

    def test_screen_command(self):
        with Env(DAY) as env:
            st = engine.load()
            env.text("/screen")
            env.text("/screen tcs")
            tg.poll(st)
            self.assertEqual(st["requests"], ["screen", "screen:TCS"])


class SwingBuyButtonTest(unittest.TestCase):
    def test_button_makes_a_scanner_order_that_asks_first(self):
        with Env(DAY) as env:
            st = engine.load()
            env.updates.append({"update_id": 1, "callback_query": {"id": "1", "from": {"id": 8674025639},
                                                                     "data": "sw:BEL:380.00:468.00"}})
            tg.poll(st)
            req = st["requests"][-1]
            self.assertTrue(req.startswith("order:"))
            order = json.loads(req[6:])
            self.assertEqual((order["pool"], order["side"], order["sl"], order["tgt"], order["source"]),
                             ("swing", "BUY", 380.0, 468.0, "scanner"))
            self.assertNotIn("sw", st.get("decisions", {}))            # not mistaken for a YES/NO
            env.prices["BEL"] = 400.0
            stored = custom.preview(st, order)
            self.assertIsNotNone(stored)
            self.assertIn("Swing buy button", env.sent[-1])
            self.assertIn("Tap Confirm", env.sent[-1])

    def test_no_order_below_the_stop(self):
        with Env(DAY) as env:
            st = engine.load()
            env.prices["BEL"] = 379.0
            out = custom.preview(st, {"pool": "swing", "side": "BUY", "symbol": "BEL", "qty": None, "sl": 380.0,
                                      "tgt": 468.0, "source": "scanner"})
            self.assertIsNone(out)
            self.assertIn("setup has failed", env.sent[-1])


class TraderUsesTheScanTest(unittest.TestCase):
    def test_note_and_advisor_penalty_for_shorting_a_pick(self):
        with Env(DAY) as env:
            st = engine.load()
            engine.start_day(st, plan_with_scan(strict=("HDFCBANK",)), ["HDFCBANK"])
            env.prices["HDFCBANK"] = 800.0
            engine.propose(st, "HDFCBANK", "SELL", "ORB short", 800.0, 803.0, 794.0, ["a"], "x")
            self.assertIn("Shorting a strict pick", env.full())
            note = [r for r in storage.read_rows("journal/2026-11-16.csv") if r["event"] == "proposed"][-1]["note"]
            self.assertIn("| scanner strict |", note)

    def test_buy_on_a_pick_gets_credit(self):
        with Env(DAY) as env:
            st = engine.load()
            engine.start_day(st, plan_with_scan(strict=("HDFCBANK",)), ["HDFCBANK"])
            env.prices["HDFCBANK"] = 800.0
            engine.propose(st, "HDFCBANK", "BUY", "ORB long", 800.0, 797.0, 806.0, ["a"], "x")
            self.assertIn("strict pick (daily trend up)", env.full())
            self.assertIn("fits a BUY", env.full())

    def test_in_play_bonus(self):
        with Env(datetime(2026, 11, 16, 9, 26)):
            st = engine.load()
            st["plan"] = plan_with_scan(strict=("BEL",), near=("HAL",))
            st["focus"] = []
            with mock.patch.object(engine, "opening_rvol", side_effect=lambda s: {"symbol": s, "rvol": 1.0, "gap_pct": 0}), \
                 mock.patch.object(engine, "tradable", return_value=True):
                ranked = engine.rank_in_play(st)
            by = {r["symbol"]: r for r in st["in_play"]}
            self.assertEqual(by["BEL"]["score"], 2.0)
            self.assertEqual(by["HAL"]["score"], 1.5)
            self.assertEqual(ranked[0]["symbol"], "BEL")

    def test_market_news_is_context_not_a_stock(self):
        with Env(DAY) as env:
            env.news_files["2026-11-16"] = [
                {"id": "r1", "feed": "rbi", "symbol": None, "category": "market", "title": "RBI keeps repo rate at 5.5%",
                 "published_ist": "2026-11-16 10:30:00", "fetched_at_ist": "2026-11-16 10:31:00"},
                {"id": "m1", "feed": "bs_markets", "symbol": "BEL", "category": "media", "title": "BEL wins Rs 2,000 cr order",
                 "published_ist": "2026-11-16 10:32:00", "fetched_at_ist": "2026-11-16 10:33:00"}]
            st = engine.load()
            engine.save_universe({"tradable_universe": ["BEL"], "news_radar": []})
            lines = engine.market_news(st)
            self.assertEqual(lines, ["RBI 16 Nov 10:30: RBI keeps repo rate at 5.5%"])
            self.assertEqual(engine.market_news(st), [])               # only once
            alerts = engine.news_alerts(st)
            self.assertEqual([a["symbol"] for a in alerts], ["BEL"])    # the named headline joins the scan


class PlanTest(unittest.TestCase):
    def test_reviewer_facts_name_the_scan(self):
        self.assertEqual(brief.scanner_status(plan_with_scan(), "BEL"), "strict")


if __name__ == "__main__":
    unittest.main()
