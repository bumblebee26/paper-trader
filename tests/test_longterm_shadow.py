"""Long-term shadow week: staggered buys, faster on dips, sells what stops qualifying, never touches the real pool."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "jobs"))

from helpers import Env  # noqa: E402

import longterm_shadow as ls  # noqa: E402
import pools  # noqa: E402


def cand(rank, sym, close, pick=True, industry="IT"):
    return {"rank": rank, "is_pick": "true" if pick else "false", "symbol": sym, "company": sym + " Ltd",
            "industry": industry, "score": 100 - rank, "target_weight_pct": 9.0 if pick else None,
            "reasons": f"Return on capital 2{rank}%.", "close": close, "price_date": "2026-11-13"}


def nifty(close, high):
    return [{"d": "2026-11-13", "close": close}, {"d": "2026-06-01", "close": high}]


class ShadowTest(unittest.TestCase):
    def test_one_pick_a_week_then_two_on_a_dip(self):
        with Env(capital=40_000) as env:
            cands = [cand(i, f"S{i}", 1000.0) for i in range(1, 11)]
            s1 = ls.week("2026-11-14", cands, nifty(280, 285))                  # 1.8% below high
            self.assertEqual([m.split()[1] for m in s1["moves"]], ["S1"])
            self.assertIn("BUY S1 (S1 Ltd, IT): 8 shares", s1["moves"][0])
            self.assertIn("Why: Return on capital 21%.", s1["moves"][0])
            s2 = ls.week("2026-11-21", cands, nifty(265, 285))                  # 7% below: two picks
            self.assertEqual([m.split()[1] for m in s2["moves"]], ["S2", "S3"])
            self.assertEqual(s2["week"], 2)
            self.assertEqual(pools.load("long_term")["holdings"], {})          # the real pool is untouched
            text = ls.message(s2)
            self.assertIn("shadow week 2 (nothing real is bought)", text)
            self.assertIn("buying two picks this week", text)
            self.assertIn("NIFTYBEES since the first shadow week: -5.36%", text)

    def test_sell_when_it_stops_qualifying_and_trim(self):
        with Env(capital=40_000):
            ls.week("2026-11-14", [cand(1, "A", 1000.0)], nifty(280, 285))
            # A drops out of the eligible list
            s = ls.week("2026-11-21", [cand(1, "B", 500.0)], nifty(280, 285))
            self.assertTrue(s["moves"][0].startswith("SELL A: it no longer passes the quality rules"))
            self.assertIn("BUY B", s["moves"][1])
            # B triples: trimmed back to 15%
            s = ls.week("2026-11-28", [cand(1, "B", 2500.0)], nifty(280, 285))
            self.assertTrue(any(m.startswith("TRIM B") for m in s["moves"]))
            self.assertNotIn("long_term_shadow", [n for n in pools.POOLS])


if __name__ == "__main__":
    unittest.main()
