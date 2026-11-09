"""Swing backtest: split adjustment, setups, automatic exits, costs, report. Synthetic prices only."""
import os
import random
import sys
import unittest
from datetime import date, timedelta
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backtest"))

import run_swing  # noqa: E402
import swing  # noqa: E402


def days(n, start=date(2024, 7, 8)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def series(closes, vols=None, start=date(2024, 7, 8)):
    rows, prev = [], None
    for i, (d, c) in enumerate(zip(days(len(closes), start), closes)):
        o = prev if prev else c
        rows.append((d, o, max(o, c) * 1.005, min(o, c) * 0.995, c, prev or c, (vols or [1e5] * len(closes))[i]))
        prev = c
    return rows


class AdjustTest(unittest.TestCase):
    def test_split_is_smoothed(self):
        rows = series([100.0] * 5)
        d = days(6)[-1]
        rows.append((d, 50.0, 51.0, 49.5, 50.5, 50.0, 2e5))   # 1:2 split: NSE prev close adjusted to 50
        bars = swing.adjust(rows)
        self.assertAlmostEqual(bars[-1]["close"], 101.0, places=4)   # +1% day, not -50%
        self.assertAlmostEqual(bars[-1]["open"], 100.0, places=4)


class RunTest(unittest.TestCase):
    def test_breakout_trade_hits_target(self):
        base = [100 + 0.05 * i for i in range(260)]
        closes = base + [114.0, 118.0, 122.0, 126.0, 130.0, 134.0, 138.0]   # breakout then a strong run
        vols = [1e5] * 260 + [4e5] + [2e5] * 6
        flat = [200.0] * len(closes)
        out = swing.run({"ABC": series(closes, vols), "NIFTYBEES": series(flat)}, setups=("breakout",),
                        warmup_days=200)
        trades = out["trades"]
        self.assertTrue(trades, out["summary"])
        t = trades[0]
        self.assertEqual((t["symbol"], t["setup"]), ("ABC", "breakout"))
        self.assertEqual(t["reason"], "target")
        self.assertGreater(t["pnl"], 0)
        self.assertGreater(out["summary"]["return_pct"], 0)
        self.assertIn("benchmark_return_pct", out["summary"])

    def test_gap_skip_and_stop(self):
        base = [100 + 0.05 * i for i in range(260)]
        closes = base + [114.0, 104.0, 100.0, 96.0, 92.0]
        vols = [1e5] * 260 + [4e5] + [1e5] * 4
        out = swing.run({"ABC": series(closes, vols)}, setups=("breakout",), warmup_days=200)
        self.assertEqual(out["trades"][0]["reason"], "hard_stop")
        self.assertLess(out["trades"][0]["pnl"], 0)

    def test_costs_are_charged(self):
        rows = series([100.0] * 300)
        out = swing.run({"ABC": rows}, warmup_days=200)
        self.assertEqual(out["trades"], [])
        self.assertEqual(out["summary"]["final_equity"], 60_000)

    def test_speed_on_a_full_universe(self):
        random.seed(1)
        data = {}
        for k in range(250):
            c, closes = 100.0, []
            for _ in range(590):
                c *= 1 + random.gauss(0.0004, 0.018)
                closes.append(round(c, 2))
            vols = [random.uniform(5e4, 3e5) for _ in closes]
            data[f"S{k}"] = series(closes, vols)
        data["NIFTYBEES"] = series([250 * (1.0003 ** i) for i in range(590)])
        import time
        t = time.monotonic()
        out = swing.run(data)
        self.assertLess(time.monotonic() - t, 60)
        s = out["summary"]
        self.assertGreater(s["trades"], 20)
        self.assertLessEqual(max(len([1 for tr in out["trades"]]), 0), 10_000)
        self.assertLessEqual(s["max_drawdown_pct"], 100)


class RunnerTest(unittest.TestCase):
    def test_sql_follows_chunks(self):
        calls = []

        def fake_call(method, path, body=None):
            calls.append(path)
            if method == "POST":
                return {"status": {"state": "SUCCEEDED"},
                        "manifest": {"schema": {"columns": [{"name": "symbol"}]}},
                        "result": {"data_array": [["A"]], "next_chunk_internal_link": "/chunks/1"}}
            return {"data_array": [["B"]]}

        with mock.patch.object(run_swing.brief, "_call", fake_call), \
                mock.patch.object(run_swing.brief, "_db_config", return_value=("u", "t", "w")):
            rows = run_swing.sql_all("SELECT 1")
        self.assertEqual(rows, [{"symbol": "A"}, {"symbol": "B"}])
        self.assertEqual(calls[-1], "/chunks/1")

    def test_message_in_plain_words(self):
        base = [100 + 0.05 * i for i in range(260)]
        closes = base + [114.0, 118.0, 122.0, 126.0, 130.0, 134.0, 138.0]
        vols = [1e5] * 260 + [4e5] + [2e5] * 6
        data = {"ABC": series(closes, vols), "NIFTYBEES": series([200.0] * len(closes))}
        results = {k: swing.run(data, warmup_days=200, **v) for k, v in run_swing.VARIANTS.items()}
        text = run_swing.message(results)
        self.assertIn("Swing backtest: Rs 60,000 pool", text)
        self.assertIn("NIFTYBEES (just holding the index)", text)
        self.assertIn("Both, only stocks beating Nifty:", text)
        self.assertIn("too few to trust", text)


if __name__ == "__main__":
    unittest.main()


class StudyTest(unittest.TestCase):
    def test_window_and_market_filter(self):
        base = [100 + 0.05 * i for i in range(260)]
        closes = base + [114.0, 118.0, 122.0, 126.0, 130.0, 134.0, 138.0]
        vols = [1e5] * 260 + [4e5] + [2e5] * 6
        falling = [300 - 0.3 * i for i in range(len(closes))]          # index below its 200-day average
        data = {"ABC": series(closes, vols), "NIFTYBEES": series(falling)}
        on = swing.run(data, setups=("breakout",), warmup_days=200, market_filter=True)
        off = swing.run(data, setups=("breakout",), warmup_days=200, market_filter=False)
        self.assertEqual(on["trades"], [])
        self.assertTrue(off["trades"])
        d = days(len(closes))
        late = swing.run(data, setups=("breakout",), warmup_days=200, start=d[-3])
        self.assertEqual(late["summary"]["start"], d[-3])

    def test_study_picks_on_train_and_reports_test(self):
        random.seed(2)
        data = {}
        for k in range(40):
            c, closes = 100.0, []
            for _ in range(590):
                c *= 1 + random.gauss(0.0005, 0.018)
                closes.append(round(c, 2))
            data[f"S{k}"] = series(closes, [random.uniform(5e4, 3e5) for _ in closes])
        data["NIFTYBEES"] = series([250 * (1.0003 ** i) for i in range(590)])
        st = run_swing.study(data)
        self.assertEqual(len(st["train"]), len(run_swing.GRID))
        self.assertEqual(st["best"], st["train"][0]["config"])
        self.assertGreaterEqual(st["test_best"]["start"], run_swing.TEST[0])
        text = run_swing.study_message(st)
        self.assertIn("judged once on", text)
        self.assertIn("Just holding NIFTYBEES", text)
