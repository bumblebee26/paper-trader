"""Pre-market plan built from stocks_lakehouse gold-table rows."""
import unittest

import helpers  # noqa: F401  (sets sys.path)

import brief
import config

SAMPLE = {
    "us_bars": [
        {"trade_date": "2026-11-06", "symbol": "SPY", "return_pct": -1.6},
        {"trade_date": "2026-11-06", "symbol": "QQQ", "return_pct": -2.1},
        {"trade_date": "2026-11-06", "symbol": "INFY", "return_pct": -3.2},
    ],
    "macro": [
        {"instrument": "VIXCLS", "obs_date": "2026-11-06", "value": 24.3, "change_pct": 18.0, "change_abs": 3.7},
        {"instrument": "DEXINUS", "obs_date": "2026-11-06", "value": 88.4, "change_pct": 0.2, "change_abs": 0.2},
    ],
    "summary": [
        {"us_move": "US down big", "sessions": 31.0, "avg_india_gap_pct": -0.42, "pct_india_up_days": 38.0,
         "pct_gap_same_direction_as_us": 81.0},
        {"us_move": "US flat", "sessions": 300.0, "avg_india_gap_pct": 0.03, "pct_india_up_days": 52.0,
         "pct_gap_same_direction_as_us": 50.0},
    ],
    "unusual_in": [
        {"symbol": "PENNY", "trade_date": "2026-11-06", "close": 12.0, "return_pct": 9.0, "volume_ratio": 9.0},
        {"symbol": "TATAPOWER", "trade_date": "2026-11-06", "close": 410.0, "return_pct": 3.1, "volume_ratio": 3.4},
    ],
}


class BriefTest(unittest.TestCase):
    def test_plan_from_lakehouse_rows(self):
        plan = brief.build_plan(SAMPLE)
        self.assertEqual(plan["us_bucket"], "US down big")
        self.assertEqual(plan["bias"], "gap-down tendency")
        self.assertEqual(plan["india_after_similar_days"]["sessions"], 31)
        self.assertEqual(plan["risk_per_trade_pct"], round(config.RISK_PER_TRADE_PCT * 0.75, 2))
        self.assertEqual([u["symbol"] for u in plan["unusual_volume_nse"]], ["TATAPOWER"])  # penny stock dropped
        self.assertEqual(plan["adr_moves_pct"], {"INFY": -3.2})
        self.assertEqual(plan["source"], "stocks_lakehouse gold tables")

    def test_risk_scales_with_vix(self):
        base = config.RISK_PER_TRADE_PCT
        self.assertEqual(brief.risk_for_vix(None), base)
        self.assertEqual(brief.risk_for_vix(14), base)
        self.assertEqual(brief.risk_for_vix(35), round(base * 0.5, 2))

    def test_unavailable_lakehouse_degrades_to_defaults(self):
        def broken(_sql):
            raise RuntimeError("Databricks not configured")
        data, failed = brief.fetch(broken)
        plan = brief.build_plan(data, failed)
        self.assertEqual(plan["source"], "unavailable")
        self.assertEqual(plan["bias"], "neutral")
        self.assertEqual(plan["risk_per_trade_pct"], config.RISK_PER_TRADE_PCT)
        self.assertIn("Lakehouse data unavailable", brief.plan_message(plan, ["TCS"]))

    def test_message_is_short(self):
        msg = brief.plan_message(brief.build_plan(SAMPLE), ["RELIANCE", "TATAPOWER"])
        self.assertIn("VIX 24.3", msg)
        self.assertLess(len(msg.splitlines()), 10)


if __name__ == "__main__":
    unittest.main()
