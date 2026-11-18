"""Exit rules on trial: replays on 1-minute candles, the day profit lock, and the reports."""
import unittest
from datetime import date, datetime

from helpers import Env

import engine
import exits
import fees
import storage
import weekly

OPEN = "2026-11-11 10:40:00"


def bars(path, start_min=41):
    """1-minute candles from a list of (low, high, close); open = previous close."""
    out, prev = [], path[0][2]
    for i, (lo, hi, cl) in enumerate(path):
        m = start_min + i
        out.append({"time": f"2026-11-11 {10 + m // 60:02d}:{m % 60:02d}", "open": prev, "high": hi, "low": lo,
                    "close": cl})
        prev = cl
    return out


# BUY at 100, stop 98 (1R = 2), target 104
UP_THEN_DOWN = bars([(99.8, 100.4, 100.2), (100.1, 102.6, 102.4), (100.5, 102.5, 100.8), (99.5, 100.9, 99.6),
                     (97.5, 99.7, 97.8)])
RUNNER = bars([(99.8, 100.4, 100.2), (100.1, 102.6, 102.4), (102.2, 104.5, 104.4), (104.0, 106.0, 105.8),
               (105.5, 108.2, 108.0), (106.0, 108.1, 106.1)])
FLAT = bars([(99.7, 100.3, 100.1)] * 70)


def run(rule, path, side="BUY", fill=100.0, stop=98.0, target=104.0, qty=100):
    return exits.replay(rule, side, qty, fill, stop, target, OPEN, path)


class ReplayTest(unittest.TestCase):
    def test_reversal_after_1r(self):
        cur, be, half, trail = (run(r, UP_THEN_DOWN) for r in ("current", "breakeven", "half_and_run", "trailing"))
        self.assertLess(cur, -200)                 # full stop-out: about -2R plus costs
        self.assertGreater(be, cur)                # out near the entry instead
        self.assertGreater(half, be)               # half banked at +1R
        self.assertGreater(trail, be)              # trailed 1R behind 102.6

    def test_runner(self):
        cur, half, trail = (run(r, RUNNER) for r in ("current", "half_and_run", "trailing"))
        exit_px = round(104 * 0.9995, 2)
        self.assertAlmostEqual(cur, (exit_px - 100) * 100 - fees.round_trip(100 * 100, exit_px * 100, "BUY"),
                               delta=0.05)                 # target at 104, every order's charges
        self.assertGreater(trail, cur)             # let the winner run past 104
        self.assertLess(half, cur)                 # half sold at 102

    def test_time_stop_frees_a_stuck_trade(self):
        self.assertIsNotNone(run("time_stop", FLAT))
        self.assertLess(abs(run("time_stop", FLAT)), 100)
        with_bars = exits.replay("current", "BUY", 100, 100.0, 98.0, 104.0, OPEN, FLAT)
        self.assertIsNotNone(with_bars)            # current rule: still open at the end, closed at the last price

    def test_short_side_mirrors(self):
        mirrored = [{"time": b["time"], "open": 200 - b["open"], "high": 200 - b["low"], "low": 200 - b["high"],
                     "close": 200 - b["close"]} for b in RUNNER]
        self.assertAlmostEqual(run("trailing", mirrored, side="SELL", fill=100.0, stop=102.0, target=96.0),
                               run("trailing", RUNNER), delta=15)  # slippage differs slightly by price level

    def test_target_1_5r_takes_the_nearer_profit(self):
        cur, near = run("current", RUNNER), run("target_1_5r", RUNNER)
        exit_px = round(103 * 0.9995, 2)                     # 1R = 2 from the fill: target 103 instead of 104
        self.assertAlmostEqual(near, (exit_px - 100) * 100 - fees.round_trip(100 * 100, exit_px * 100, "BUY"),
                               delta=0.05)
        self.assertLess(near, cur)                           # the runner went on to 104
        self.assertEqual(run("target_1_5r", UP_THEN_DOWN), run("current", UP_THEN_DOWN))  # peaked at 102.6
        self.assertEqual(run("target_1_5r", RUNNER, target=102.0), run("current", RUNNER, target=102.0))  # nearer already

    def test_level_target_exits_at_yesterdays_high_first(self):
        level = exits.replay("level_target", "BUY", 100, 100.0, 98.0, 104.0, OPEN, UP_THEN_DOWN, level=102.0)
        self.assertGreater(level, 0)                         # sold at 102 on the way up
        self.assertLess(run("current", UP_THEN_DOWN), 0)     # the current rule rode it back to the stop
        for lvl in (None, 100.5, 105.0):                     # unknown, nearer than +0.5R, past the target
            self.assertEqual(exits.replay("level_target", "BUY", 100, 100.0, 98.0, 104.0, OPEN, UP_THEN_DOWN,
                                          level=lvl), run("current", UP_THEN_DOWN))

    def test_level_target_short_uses_yesterdays_low(self):
        self.assertEqual(exits.level_target("SELL", 100.0, 102.0, 96.0, 98.0), 98.0)
        self.assertEqual(exits.level_target("SELL", 100.0, 102.0, 96.0, 99.5), 96.0)   # within +0.5R
        self.assertEqual(exits.level_target("BUY", 100.0, 98.0, 104.0, 98.0), 104.0)   # below the entry

    def test_day_levels_skip_today(self):
        daily = [{"time": "2026-11-10 09:15", "high": 110.0, "low": 100.0},
                 {"time": "2026-11-11 09:15", "high": 120.0, "low": 105.0}]
        self.assertEqual(exits.day_levels(daily, "2026-11-11"), (110.0, 100.0))
        self.assertEqual(exits.day_levels([], "2026-11-11"), (None, None))

    def test_replay_all_has_every_rule(self):
        out = exits.replay_all({"side": "BUY", "qty": 100, "fill_price": 100.0, "stop": 98.0, "target": 104.0,
                                "opened_at": OPEN}, UP_THEN_DOWN, (102.0, 95.0))
        self.assertEqual(set(out), set(exits.RULES))
        self.assertGreater(out["level_target"], out["current"])

    def test_profit_lock(self):
        events = [("10:00", "10:20", 1300.0), ("10:30", "10:50", 1900.0),    # +3,200: armed (3R = 3,000)
                  ("11:00", "11:20", -1800.0),                              # back to 1,400 <= half of 3,200
                  ("11:30", "11:50", -900.0)]                               # skipped by the lock
        lock = exits.profit_lock(events, 1000.0)
        self.assertEqual(lock, {"skipped": 1, "day_actual": 500.0, "day_with_lock": 1400.0})
        self.assertIsNone(exits.profit_lock(events[:1], 1000.0))           # never armed


class ReportTest(unittest.TestCase):
    def test_day_and_week_reports_show_the_trial(self):
        with Env(datetime(2026, 11, 11, 10, 40)) as env:
            st = engine.load()
            env.prices["HDFCBANK"] = 1600.0
            r = engine.propose(st, "HDFCBANK", "BUY", "ORB long", 1600.0, 1584.0, 1632.0, ["a"], "x")
            env.tap("yes", r["proposal_id"])
            engine.wait_decision(st, r["proposal_id"], 1)
            engine.execute(st, r["proposal_id"])
            env.bars[("HDFCBANK", "1m")] = bars([(1599, 1601, 1600.5), (1600, 1618, 1617), (1600, 1617.5, 1601),
                                                 (1583, 1602, 1584)])
            env.clock = datetime(2026, 11, 11, 10, 46)
            engine.close(st, "HDFCBANK", 1584.0, "stop_loss")
            env.clock = datetime(2026, 11, 11, 15, 20)
            summary = engine.report(st)
            self.assertEqual(summary["exit_rules_trades"], 1)
            self.assertGreater(summary["exit_rules_rs"]["breakeven"], summary["exit_rules_rs"]["current"])
            self.assertIn("Exit rules on trial (1 trades", env.sent[-1])
            rows = [x for x in storage.read_rows("journal/2026-11-11.csv") if x["event"] == "exit_variants"]
            self.assertEqual(len(rows), 1)
            engine.report(st, send=False)                                  # a re-run repeats the row...
            w = weekly.build(date(2026, 11, 13))
            self.assertEqual(w["exit_rules"]["trades"], 1)                 # ...but the week counts it once
            self.assertIn("Exit rules on trial", weekly.message(w, st))
            self.assertIn("target 1.5R", weekly.message(w, st))

    def test_week_counts_rows_from_before_the_new_rules(self):
        with Env(datetime(2026, 11, 13, 16, 0)) as env:
            st = engine.load()
            old = "current=-200.0;breakeven=-10.0;half_and_run=50.0;trailing=-10.0;time_stop=-200.0;no_stop=-300.0;" \
                  "wide_1_5=-150.0;wide_2=-120.0;close_stop=-100.0"
            new = old.replace("current=-200.0", "current=300.0") + ";target_1_5r=200.0;level_target=250.0"
            engine.journal("exit_variants", event_id="exitvar-a", proposal_id="a", symbol="TCS", pnl_rs=-200.0, note=old)
            env.clock = datetime(2026, 11, 13, 16, 1)
            engine.journal("exit_variants", event_id="exitvar-b", proposal_id="b", symbol="INFY", pnl_rs=300.0, note=new)
            w = weekly.build(date(2026, 11, 13))
            ex = w["exit_rules"]
            self.assertEqual(ex["trades"], 2)                              # the old row still counts
            self.assertEqual(ex["counts"]["target_1_5r"], 1)
            self.assertEqual(ex["totals"]["target_1_5r"] - ex["base"]["target_1_5r"], -100.0)  # vs current, same trade
            text = "\n".join(weekly.paper_lines(w))
            self.assertIn("target 1.5R -Rs 100 (1 trades)", text)


if __name__ == "__main__":
    unittest.main()
