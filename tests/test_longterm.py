"""Long-term backtest: metrics as of a date, selection rules, simulation vs the index. Synthetic data."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backtest"))

from test_backtest import series as bars_for  # noqa: E402

import longterm  # noqa: E402
import run_longterm  # noqa: E402
import swing  # noqa: E402


def fund(sym, ebit, ic, ni, ta, debt, eq, ocf, rev, eps, fin=False):
    rows = []
    for k, fy in enumerate(("2023-03-31", "2024-03-31", "2025-03-31", "2026-03-31")):
        g = 1.2 ** k
        vals = {"NetIncome": ni * g, "TotalAssets": ta, "TotalDebt": debt, "StockholdersEquity": eq,
                "OperatingCashFlow": ocf, "TotalRevenue": rev * g, "DilutedEPS": eps * g}
        if not fin:
            vals.update(EBIT=ebit * g, InvestedCapital=ic)
        rows += [(sym, m, "12M", fy, v) for m, v in vals.items()]
    return rows


class SelectTest(unittest.TestCase):
    def setUp(self):
        self.rows = (fund("GOOD", 30, 100, 20, 200, 10, 100, 25, 100, 10)        # ROCE 30%, low debt, growing
                     + fund("DEBT", 30, 100, 20, 200, 90, 100, 25, 100, 10)      # heavy debt
                     + fund("LOSS", 30, 100, -5, 200, 10, 100, 25, 100, -1)      # loss-making
                     + fund("BANK", 0, 0, 2, 100, 0, 10, 1, 10, 5, fin=True)      # ROA 2%
                     + fund("GOOD2", 30, 100, 20, 200, 10, 100, 25, 100, 10)
                     + fund("GOOD3", 30, 100, 20, 200, 10, 100, 25, 100, 10))

    def test_asof_uses_only_past_years(self):
        m = longterm.metrics_asof(self.rows, "2025-03-31")
        self.assertEqual(m["GOOD"]["fy_end"], "2025-03-31")
        self.assertAlmostEqual(m["GOOD"]["profit_cagr"], 20.0, places=1)
        self.assertTrue(m["BANK"]["is_financial"])

    def test_rules_and_industry_cap(self):
        m = longterm.metrics_asof(self.rows, "2025-03-31")
        prices = {"GOOD": 200, "DEBT": 200, "LOSS": 50, "BANK": 50, "GOOD2": 400, "GOOD3": 300}
        out = longterm.select(m, prices, {"GOOD": "IT", "GOOD2": "IT", "GOOD3": "IT", "BANK": "Banks"})
        names = [p["symbol"] for p in out["picks"]]
        self.assertNotIn("LOSS", names)                      # no positive P/E
        self.assertEqual(sum(1 for n in names if n.startswith("GOOD")), 2)   # max 2 per industry
        self.assertIn("BANK", names)
        self.assertAlmostEqual(out["picks"][0]["weight_pct"], 15.0)          # 90% / few picks, capped at 15%


class SimulateTest(unittest.TestCase):
    def test_hold_vs_index(self):
        up = [100 * 1.002 ** i for i in range(300)]
        flat = [250.0] * 300
        series = {"UP": swing.adjust(bars_for(up)), "NIFTYBEES": swing.adjust(bars_for(flat))}
        start = series["NIFTYBEES"][10]["date"]
        end = series["NIFTYBEES"][-1]["date"]
        sim = longterm.simulate(series, [(start, [{"symbol": "UP", "weight_pct": 15.0}])], end)
        self.assertGreater(sim["curve"][-1][1], 100_000)
        self.assertIn("UP", sim["holdings"])
        bench = longterm.benchmark(series, start, end)
        self.assertAlmostEqual(bench["return_pct"], 0.0, places=1)

    def test_message(self):
        r = {"start": "2025-07-01", "end": "2026-11-06",
             "selections": [{"date": "2025-07-01", "eligible": 40, "picks": [{"symbol": "TCS"}]},
                            {"date": "2026-07-01", "eligible": 38, "picks": []}],
             "hold": {"return_pct": 12.0, "max_drawdown_pct": 9.0}, "rebalance": {"return_pct": 10.0, "max_drawdown_pct": 8.0},
             "nifty": {"return_pct": 4.0, "max_drawdown_pct": 9.6}, "nifty90": {"return_pct": 3.6}}
        text = run_longterm.message(r)
        self.assertIn("Buying and holding the picks beat holding the index by 8.4 points", text)
        self.assertIn("Re-picking once a year beat holding the index by 6.4 points", text)
        self.assertIn("Picks on 2025-07-01: TCS", text)
        self.assertIn("today's Nifty 200 companies", text)
        r["coverage"] = {"universe": 250, "with_figures": 190}
        text = run_longterm.message(r)
        self.assertIn("the 250 most traded NSE stocks of Apr-Jun 2025 (no hindsight)", text)
        self.assertIn("available for 190 of those 250 stocks", text)


if __name__ == "__main__":
    unittest.main()
