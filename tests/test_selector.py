"""Selector (paper): picks tomorrow's exit rule, auto bar and entry from recent shadow results, walk-forward."""
import unittest
from datetime import date, datetime

from helpers import Env

import engine
import selector
import storage


def day_with(ev_rows):
    for r in ev_rows:
        engine.journal(**r)


def exit_row(pid, actual, **rules):
    note = ";".join(f"{k}={v}" for k, v in {"current": actual, **rules}.items())
    return {"event": "exit_variants", "event_id": f"exitvar-{pid}", "proposal_id": pid, "pnl_rs": actual, "note": note}


class Choose(unittest.TestCase):
    def ev(self, family, data):
        e = {f: {} for f in selector.FAMILIES}
        e[family] = data
        return e

    def test_needs_enough_trades(self):
        window = [self.ev("exit", {"actual": [-100.0] * 5, "breakeven": [300.0] * 5})]
        self.assertEqual(selector.choose("exit", window, "actual")[0], "actual")

    def test_switches_on_a_clear_edge_only(self):
        window = [self.ev("exit", {"actual": [-100.0] * 40, "breakeven": [200.0] * 40})]
        pick, why = selector.choose("exit", window, "actual")
        self.assertEqual(pick, "breakeven")
        self.assertIn("beat", why)
        close = [self.ev("exit", {"actual": [100.0] * 40, "breakeven": [120.0] * 40})]
        self.assertEqual(selector.choose("exit", close, "actual")[0], "actual")   # Rs 20 a trade: stay

    def test_bar_compares_per_day_totals(self):
        window = [self.ev("bar", {"50": [-300.0] * 10, "60": [100.0] * 4, "70": [50.0]}) for _ in range(5)]
        self.assertEqual(selector.choose("bar", window, "50")[0], "60")


class Run(unittest.TestCase):
    def test_scores_yesterdays_pick_then_picks_tomorrow(self):
        with Env(datetime(2026, 11, 23, 15, 25)) as env:
            storage.put_json(selector.KEY, {"picks": {"exit": "breakeven", "bar": "60", "entry": "orb5_2r"}})
            day_with([exit_row("a", -400.0, breakeven=-20.0), exit_row("b", 300.0, breakeven=150.0)])
            out = selector.run(today=date(2026, 11, 23))
            self.assertEqual(out["selector_made"]["exit"], (130.0, -100.0))     # breakeven vs the live result
            rows = storage.read_rows("journal/2026-11-23.csv")
            self.assertTrue(any(r["event"] == "selector" for r in rows))
            self.assertIn("Selector (paper)", selector.lines(out)[0])

    def test_no_trading_day_does_nothing(self):
        with Env(datetime(2026, 11, 22, 15, 25)):
            self.assertEqual(selector.run(today=date(2026, 11, 22)), {})


if __name__ == "__main__":
    unittest.main()
