"""Shadow models: setups match the live scanner, nothing looks ahead, the stop variants and the walk-forward."""
import copy
import os
import random
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path[:0] = [os.path.join(os.path.dirname(__file__), "..", "trader"), os.path.join(os.path.dirname(__file__), "..")]

import exits  # noqa: E402
import fees  # noqa: E402
import market  # noqa: E402
import scanner  # noqa: E402
from models import intraday, walkforward  # noqa: E402


def make_bars(seed, days=6, start=500.0, drift=0.0):
    rnd = random.Random(seed)
    out, px, day, n = [], start, datetime(2026, 10, 5), 0
    while n < days:
        if day.weekday() < 5:
            t = day.replace(hour=9, minute=15)
            px *= 1 + rnd.gauss(0, 0.008)
            for _ in range(75):
                o = px
                px *= 1 + rnd.gauss(drift, 0.004)
                out.append({"time": t.strftime("%Y-%m-%d %H:%M"), "open": o,
                            "high": max(o, px) * (1 + abs(rnd.gauss(0, 0.0015))),
                            "low": min(o, px) * (1 - abs(rnd.gauss(0, 0.0015))), "close": px,
                            "volume": rnd.lognormvariate(10, 0.7)})
                t += timedelta(minutes=5)
            n += 1
        day += timedelta(days=1)
    return out


class SetupsMatchTheScanner(unittest.TestCase):
    def test_same_signals_bar_by_bar(self):
        far = datetime(2030, 1, 1, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        checked = 0
        for seed in range(6):
            bars = make_bars(seed)
            mine = {(s["date"], s["time"], s["setup"]): s for s in intraday.signals(bars)}
            last_day = bars[-1]["time"][:10]
            with mock.patch.object(market, "now_ist", lambda: far):
                for k, b in enumerate(bars):
                    if b["time"][:10] == bars[0]["time"][:10] or not ("09:30" <= b["time"][11:16] < "14:30"):
                        continue
                    info = scanner.analyse("X", bars[max(0, k - 400):k + 1])
                    want = {(b["time"][:10], b["time"][11:16], s["setup"]): s for s in info["signals"]}
                    got = {key: s for key, s in mine.items() if key[:2] == (b["time"][:10], b["time"][11:16])}
                    self.assertEqual(set(want), set(got), b["time"])
                    for key in want:
                        self.assertAlmostEqual(want[key]["stop"], got[key]["stop"], places=2)
                        checked += 1
            self.assertTrue(last_day)
        self.assertGreater(checked, 20)

    def test_no_look_ahead(self):
        bars = make_bars(3)
        before = intraday.signals(bars)
        sig = before[len(before) // 2]
        cut = next(n for n, b in enumerate(bars) if b["time"] == f"{sig['date']} {sig['time']}")
        later = copy.deepcopy(bars)
        for b in later[cut + 1:]:                      # scramble everything after the signal candle
            b["close"] *= 1.07
            b["high"] *= 1.09
            b["volume"] *= 5
        after = {(s["date"], s["time"], s["setup"]): s["features"] for s in intraday.signals(later)}
        self.assertEqual(after[(sig["date"], sig["time"], sig["setup"])], sig["features"])


class StopVariants(unittest.TestCase):
    def bars(self, lows, closes, start="2026-11-17 10:00"):
        t = datetime.strptime(start, "%Y-%m-%d %H:%M")
        out = []
        for lo, c in zip(lows, closes):
            out.append({"time": t.strftime("%Y-%m-%d %H:%M"), "open": c, "high": c + 0.5, "low": lo, "close": c})
            t += timedelta(minutes=1)
        return out

    def net(self, qty, exit_price):
        px = exits._exit_fill(exit_price, "BUY")
        return (px - 100.0) * qty - fees.order(100.0 * qty, "BUY") - fees.order(px * qty, "SELL")

    def test_wick_stops_current_but_not_close_stop_or_no_stop(self):
        # stop 99, a wick to 98.8 at 10:02 that closes back at 100, then a rally to the 104 target
        lows = [100, 99.8, 98.8, 100, 101, 102, 103.6]
        closes = [100.2, 100, 100, 101, 102, 103, 104.2]
        bars = self.bars(lows, closes)
        cur = exits.replay("current", "BUY", 100, 100.0, 99.0, 102.0, "2026-11-17 09:59:00", bars)
        close_stop = exits.replay("close_stop", "BUY", 100, 100.0, 99.0, 102.0, "2026-11-17 09:59:00", bars)
        no_stop = exits.replay("no_stop", "BUY", 100, 100.0, 99.0, 102.0, "2026-11-17 09:59:00", bars)
        self.assertLess(cur, 0)
        self.assertGreater(close_stop, 0)
        self.assertGreater(no_stop, 0)

    def test_close_stop_hard_stop_and_5min_close(self):
        crash = self.bars([99.5, 97.5], [99.6, 97.8])                     # through 2x the distance: out at 98
        crash[1]["open"] = 99.4                                             # falls during the candle, no gap
        r = exits.replay("close_stop", "BUY", 100, 100.0, 99.0, 102.0, "2026-11-17 09:59:00", crash)
        r_cur = exits.replay("current", "BUY", 100, 100.0, 99.0, 102.0, "2026-11-17 09:59:00", crash)
        self.assertLess(r, r_cur)                                           # hard stop is further away
        self.assertAlmostEqual(r, self.net(100, 98.0), delta=1)             # out at the 2x hard stop
        slow = self.bars([99.5] * 5, [99.6, 99.4, 99.2, 99.1, 98.9])       # the 10:00-10:04 candle closes 98.9
        r = exits.replay("close_stop", "BUY", 100, 100.0, 99.0, 102.0, "2026-11-17 09:59:00", slow)
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r, self.net(100, 98.9), delta=1)            # out at the 5-minute close

    def test_wide_stop_keeps_rupee_risk(self):
        bars = self.bars([99.4, 98.1], [99.5, 98.2])
        r2 = exits.replay("wide_2", "BUY", 100, 100.0, 99.0, 102.0, "2026-11-17 09:59:00", bars)
        r1 = exits.replay("current", "BUY", 100, 100.0, 99.0, 102.0, "2026-11-17 09:59:00", bars)
        self.assertLess(r1, 0)
        self.assertLess(r2, 0)               # 50 shares, stop 98: the 98.1 low doesn't reach it
        self.assertAlmostEqual(r2, self.net(50, 98.2), delta=1)             # still open: valued at the last price


class WalkForward(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = {f"S{i}": make_bars(i, days=22, start=300 + 20 * i) for i in range(10)}
        cls.nifty = make_bars(99, days=22, start=24000)
        cls.data = intraday.dataset(cls.bars, cls.nifty)
        cls.days = {s: intraday.by_day(b) for s, b in cls.bars.items()}

    def vol_fn(self):
        return lambda s, p, k: intraday.vol_stop_r(s, self.days[s["symbol"]][s["date"]], p, k)

    def test_labels(self):
        d = next(x for x in self.data if x["checkpoints"])
        self.assertIn("current", d["results"])
        self.assertLess(d["checkpoints"][0]["features"]["open_r"], 0)
        self.assertIsNotNone(d["fwd_range_pct"])
        for x in self.data:
            self.assertLessEqual(intraday.blocked_by_costs(x["entry"], abs(x["entry"] - x["stop"]), x["side"]), False)

    def test_only_earlier_days_train_each_day(self):
        with mock.patch.object(walkforward, "MIN_TRAIN_DAYS", 12), mock.patch.object(walkforward, "REFIT_DAYS", 3):
            seen = []
            real_fit = walkforward.fit

            def spy(train, *a):
                seen.append(max(d["date"] for d in train))
                return real_fit(train, *a)

            with mock.patch.object(walkforward, "fit", spy):
                dec, notes = walkforward.walk_forward(self.data, self.vol_fn())
        for note, last_train in zip(notes, seen):
            self.assertLess(last_train, note["refit_on"])
        days = sorted({d["date"] for d in self.data})
        self.assertEqual(min(d["date"] for d in dec), days[12])
        s = walkforward.summarise(dec)
        for rule in ("current", "filter", "early_cut", "let_run", "combined", "no_stop", "wide_2", "close_stop"):
            self.assertIn(rule, s)
        self.assertEqual(s["current"]["signals"], len(dec))

    def test_real_trade_scored_like_a_signal(self):
        sig = next(d for d in self.data if d["setup"].startswith("ORB"))
        hh, mm = int(sig["time"][:2]), int(sig["time"][3:5]) + 5
        fill_time = f"{hh + mm // 60:02d}:{mm % 60:02d}"
        trade = {"date": sig["date"], "time": fill_time, "setup": sig["setup"], "side": sig["side"],
                 "entry": sig["entry"], "stop": sig["stop"], "target": sig["target"]}
        real = intraday.signal_from_trade(self.bars[sig["symbol"]], self.nifty, trade)
        self.assertEqual(real["time"], sig["time"])
        self.assertEqual(real["features"]["risk_pct"], sig["features"]["risk_pct"])
        self.assertEqual(real["results"]["current"], sig["results"]["current"])


if __name__ == "__main__":
    unittest.main()


class ReportLines(unittest.TestCase):
    def test_lines_from_history(self):
        from helpers import Env

        import model_report
        import storage

        with Env():
            self.assertIn("no results yet", model_report.lines()[0])
            s = {"current": {"total_r": -12.0, "days": 40, "signals": 900}, "filter": {"total_r": 3.5},
                 "early_cut": {"total_r": -4.0}, "no_stop": {"total_r": -20.0}, "_vol_model": {"corr": 0.6}}
            real = [{"scored": True, "actual_rs": -342.0, "filter_takes": False,
                     "rs": {"current": -342.0, "filter": 0.0, "early_cut": -150.0, "let_run": -342.0}}]
            storage.put_json("models/results/history.json", [{"date": "2026-11-18", "summary": s, "real_trades": real}])
            out = model_report.lines()
        self.assertIn("current rules -12R; best trade filter +4R, early cut -4R, no stop -20R", out[0])
        self.assertIn("actual -342 Rs", out[1])
        self.assertIn("trade filter +0", out[1])
        self.assertIn("skipped 1", out[1])
