"""Momentum shadows (from 18 Nov): swing momentum + news (daily, ATR trailing stop) and long-term momentum (monthly
top 15). Paper only: the real pools are never touched."""
import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "jobs"))

from helpers import Env  # noqa: E402

import longterm_shadow as ls  # noqa: E402
import pools  # noqa: E402
import swing_momentum as sm  # noqa: E402


def bar(day, o, h, low, c):
    return [{"time": f"{day} 09:15", "open": o, "high": h, "low": low, "close": c, "volume": 1}]


def sig(sym, rank, d="2026-11-17", atr=10.0, **over):
    s = {"d": d, "symbol": sym, "company": sym + " Ltd", "swing_rank": rank, "swing_score": 90 - rank, "atr14": atr,
         "close": 500.0, "ret_1m_pct": 12.0, "ret_3m_pct": 25.0, "pct_of_52w_high": 97.0, "news_pos_5d": 1,
         "latest_good_news": "order: bags Rs 500 cr order", "scanner_strict": "true"}
    s.update(over)
    return s


class SwingMomentum(unittest.TestCase):
    def test_entry_sizing_trailing_and_exit(self):
        with Env(datetime(2026, 11, 18, 15, 50)) as env:
            env.bars[("NIFTYBEES", "1d")] = bar("2026-11-18", 270, 271, 269, 270)
            env.bars[("AAA", "1d")] = bar("2026-11-18", 500, 512, 498, 510)
            snap = sm.daily("2026-11-18", [sig("AAA", 1)])
            h = pools.load(sm.POOL)["holdings"]["AAA"]
            # 2% of Rs 60,000 at risk over a 2 x ATR (Rs 20) stop = 60 shares; the 1/6 cap (16.5%) = 19 shares
            self.assertEqual(h["qty"], 19)
            self.assertEqual(h["stop"], 490.0)                      # trail: 510 close - 20 = 490 (> 480 initial)
            self.assertIn("BUY 19 AAA", snap["moves"][0])
            self.assertIn("news: order: bags Rs 500 cr order", snap["moves"][0])
            self.assertIn("scanner strict pick", snap["moves"][0])
            env.clock = datetime(2026, 11, 19, 15, 50)
            env.bars[("AAA", "1d")] = bar("2026-11-19", 512, 540, 511, 538)
            sm.daily("2026-11-19", [])
            self.assertEqual(pools.load(sm.POOL)["holdings"]["AAA"]["stop"], 518.0)   # trails up with the close
            env.clock = datetime(2026, 11, 20, 15, 50)
            env.bars[("AAA", "1d")] = bar("2026-11-20", 530, 531, 515, 520)
            snap = sm.daily("2026-11-20", [])
            self.assertTrue(any("SELL AAA" in m and "trailing stop" in m for m in snap["moves"]))
            self.assertEqual(pools.load("swing")["holdings"], {})    # the real pool is untouched

    def test_best_ranked_first_and_six_holdings_max(self):
        with Env(datetime(2026, 11, 18, 15, 50)) as env:
            env.bars[("NIFTYBEES", "1d")] = bar("2026-11-18", 270, 271, 269, 270)
            names = [f"S{i}" for i in range(1, 9)]
            for n in names:
                env.bars[(n, "1d")] = bar("2026-11-18", 100, 103, 99, 102)
            snap = sm.daily("2026-11-18", [sig(n, i, atr=2.0) for i, n in reversed(list(enumerate(names, 1)))])
            held = list(pools.load(sm.POOL)["holdings"])
            self.assertEqual(held, names[:6])
            self.assertEqual(sum(m.startswith("BUY") for m in snap["moves"]), 6)

    def test_concentrated_copy_holds_top3_bigger(self):
        with Env(datetime(2026, 11, 18, 15, 50)) as env:
            env.bars[("NIFTYBEES", "1d")] = bar("2026-11-18", 270, 271, 269, 270)
            names = [f"S{i}" for i in range(1, 9)]
            for n in names:
                env.bars[(n, "1d")] = bar("2026-11-18", 100, 103, 99, 102)
            sigs = [sig(n, i, atr=2.0) for i, n in enumerate(names, 1)]
            sm.daily("2026-11-18", sigs)
            snap = sm.daily("2026-11-18", sigs, sm.CONC_POOL)
            conc = pools.load(sm.CONC_POOL)["holdings"]
            self.assertEqual(list(conc), ["S1", "S2", "S3"])
            # 3% of Rs 60,000 at risk over a Rs 4 stop = 450 shares; the one-third cap (32.5%) = 195 shares at Rs 100
            self.assertEqual(conc["S1"]["qty"], 195)
            self.assertEqual(len(pools.load(sm.POOL)["holdings"]), 6)               # the diversified copy is unchanged
            self.assertIn("concentrated", sm.lines(snap)[0])
            self.assertEqual(len(sm.all_lines()), 2)

    def test_old_ranks_are_not_traded(self):
        with Env(datetime(2026, 11, 18, 15, 50)) as env:
            env.bars[("AAA", "1d")] = bar("2026-11-18", 500, 512, 498, 510)
            snap = sm.daily("2026-11-18", [sig("AAA", 1, d="2026-11-13")])
            self.assertEqual(snap["moves"], [])


def rank(r, sym, close=1000.0):
    return {"symbol": sym, "company": sym + " Ltd", "industry": "IT", "lt_rank": r, "lt_score": 3 - r / 10,
            "close": close, "ret_6m_pct": 30.0, "ret_12m_pct": 60.0, "vol_1y_pct": 25.0, "d": "2026-11-20"}


class LongTermMomentum(unittest.TestCase):
    def test_monthly_rebalance_top15_keep_top30(self):
        with Env(datetime(2026, 11, 21, 10, 15), capital=40_000):
            ranks = [rank(i, f"M{i}") for i in range(1, 41)]
            nifty = [{"d": "2026-11-20", "close": 270.0}]
            s1 = ls.momentum_month("2026-11-21", ranks, nifty)
            p = pools.load(ls.MOM_POOL)
            self.assertTrue(s1["rebalanced"])
            self.assertEqual(sorted(p["holdings"], key=lambda s: int(s[1:])), [f"M{i}" for i in range(1, 16)])
            self.assertEqual(p["holdings"]["M1"]["qty"], 6)          # ~Rs 6,533 each of Rs 1 lakh, Rs 1,000 a share
            s2 = ls.momentum_month("2026-11-28", ranks, nifty)       # same month: valued only
            self.assertFalse(s2["rebalanced"])
            self.assertEqual(s2["moves"], [])
            moved = [rank(i, f"M{i}") for i in range(16, 41)] + [rank(40 + i, f"M{i}") for i in range(1, 16)]
            moved = [dict(r, lt_rank=n) for n, r in enumerate(sorted(moved, key=lambda r: r["lt_rank"]), 1)]
            s3 = ls.momentum_month("2026-12-05", moved, nifty)       # new month: M1-M15 now ranked 26-40
            self.assertTrue(s3["rebalanced"])
            sold = {m.split()[1].rstrip(":") for m in s3["moves"] if m.startswith("SELL")}
            self.assertEqual(sold, {f"M{i}" for i in range(6, 16)})  # ranks 31-40 sold; 26-30 kept
            self.assertEqual(pools.load("long_term")["holdings"], {})
            self.assertIn("rebalanced this month", ls.momentum_message(s3))

    def test_concentrated_top5_keep_top10(self):
        with Env(datetime(2026, 11, 21, 10, 15), capital=40_000):
            ranks = [rank(i, f"M{i}") for i in range(1, 41)]
            nifty = [{"d": "2026-11-20", "close": 270.0}]
            s1 = ls.momentum_month("2026-11-21", ranks, nifty, ls.MOM_CONC_POOL)
            p = pools.load(ls.MOM_CONC_POOL)
            self.assertEqual(sorted(p["holdings"], key=lambda s: int(s[1:])), [f"M{i}" for i in range(1, 6)])
            self.assertEqual(p["holdings"]["M1"]["qty"], 19)         # ~Rs 19,600 each of Rs 1 lakh, Rs 1,000 a share
            self.assertIn("top 5, concentrated", ls.momentum_message(s1))
            # next month M1-M5 are ranked 8-12: 8-10 kept, 11-12 sold, the two freed places go to the new top ranks
            order = [f"N{i}" for i in range(1, 8)] + [f"M{i}" for i in range(1, 6)] + [f"N{i}" for i in range(8, 30)]
            moved = [rank(n, sym) for n, sym in enumerate(order, 1)]
            s2 = ls.momentum_month("2026-12-05", moved, nifty, ls.MOM_CONC_POOL)
            sold = {m.split()[1].rstrip(":") for m in s2["moves"] if m.startswith("SELL")}
            self.assertEqual(sold, {"M4", "M5"})
            self.assertEqual(set(pools.load(ls.MOM_CONC_POOL)["holdings"]), {"M1", "M2", "M3", "N1", "N2"})
            self.assertEqual(pools.load(ls.MOM_POOL)["holdings"], {})   # the top-15 copy wasn't run here


if __name__ == "__main__":
    unittest.main()
