"""Pre-market plan built from stocks_lakehouse gold-table rows."""
import unittest

import helpers  # noqa: F401  (sets sys.path)

import brief
import config

from datetime import date

TODAY = date(2026, 11, 16)          # Monday
SESSIONS = {"NSE": "2026-11-13", "US": "2026-11-13"}


def live(**over):
    base = {"india_vix": (24.3, 6.0), "sp500": (6500.0, -1.6), "nasdaq": (23000.0, -2.1), "brent": (72.0, 0.5),
            "usd_inr": (88.4, 0.2), "INFY": (21.0, -3.2)}
    base.update(over)
    return {k: {"date": "2026-11-13", "close": v[0], "change_pct": v[1]} for k, v in base.items() if v}


def scan_row(sym, list_type="strict", **over):
    r = {"symbol": sym, "trade_date": "2026-11-13", "list_type": list_type, "rank": 1.0, "close": 1234.5,
         "setup": "20-day breakout", "stance": "buy-on-dip", "rsi14": 58.0, "adx14": 24.0, "vol_ratio": 2.1,
         "deliv_per": 50.0, "fno": True, "entry_low": 1220.0, "entry_high": 1240.0, "trigger": 1220.0,
         "stop": 1180.0, "target1": 1300.0, "target2": 1420.0, "rr": 2.4, "misses": "",
         "regime_close": 268.0, "regime_rsi": 57.0, "regime_sma20": 265.0, "regime_sma50": 260.0,
         "regime_sma200": 250.0}
    r.update(over)
    return r


FRESH = [{"source": "nse_prices", "latest_date": "2026-11-13"},
         {"source": "technical_scanner", "latest_date": "2026-11-13"}]

SAMPLE = {
    "freshness": FRESH,
    "summary": [
        {"us_move": "US down big", "sessions": 31.0, "avg_india_gap_pct": -0.42, "pct_india_up_days": 38.0,
         "pct_gap_same_direction_as_us": 81.0},
    ],
    "unusual_in": [
        {"symbol": "PENNY", "trade_date": "2026-11-13", "close": 12.0, "return_pct": 9.0, "volume_ratio": 9.0},
        {"symbol": "TATAPOWER", "trade_date": "2026-11-13", "close": 410.0, "return_pct": 3.1, "volume_ratio": 3.4},
    ],
    "scanner": [scan_row("TATAPOWER"), scan_row("TCS", "near-miss", misses="volume under 1.5x")],
}


def plan(data=None, lv=None, sessions=SESSIONS, today=TODAY, failed=()):
    return brief.build_plan(data if data is not None else SAMPLE, failed, live=live() if lv is None else lv,
                            sessions=sessions, today=today)


class BriefTest(unittest.TestCase):
    def test_india_first_plan(self):
        p = plan()
        self.assertEqual(p["india_vix"], 24.3)
        self.assertEqual(p["risk_per_trade_pct"], round(config.RISK_PER_TRADE_PCT * 0.75, 2))
        self.assertEqual(p["nifty_regime"], "uptrend")
        self.assertEqual(p["bias"], "uptrend")                  # the US -1.6% is supporting only: no shock
        self.assertEqual(p["bias_side"], "BUY")
        self.assertEqual(p["global_supporting"]["sp500_pct"], -1.6)
        self.assertIsNone(p["global_supporting"]["shock"])
        self.assertEqual([u["symbol"] for u in p["unusual_volume_nse"]], ["TATAPOWER"])  # penny stock dropped
        self.assertEqual(p["adr_moves_pct"], {"INFY": -3.2})
        self.assertEqual([s["symbol"] for s in p["scanner"]["strict"]], ["TATAPOWER"])
        self.assertEqual([s["symbol"] for s in p["scanner"]["near_miss"]], ["TCS"])
        self.assertEqual(p["missing"], [])
        self.assertEqual(p["source"], "stocks_lakehouse gold tables")

    def test_global_shock_overrides_an_opposite_bias(self):
        p = plan(lv=live(sp500=(6300.0, -2.6)))
        self.assertEqual(p["global_supporting"]["shock"], "down")
        self.assertEqual(p["bias"], "uptrend, global shock down")
        self.assertIsNone(p["bias_side"])                        # trend up, shock down: no side favoured
        self.assertEqual(p["global_supporting"]["india_after_similar_shocks"]["sessions"], 31)

    def test_stale_sources_are_left_out_not_reused(self):
        stale = {**SAMPLE, "freshness": [{"source": "nse_prices", "latest_date": "2026-11-12"},
                                         {"source": "technical_scanner", "latest_date": "2026-11-12"}]}
        lv = live()
        for v in lv.values():
            v["date"] = "2026-11-12"
        p = plan(stale, lv)
        self.assertEqual(p["unusual_volume_nse"], [])
        self.assertEqual(p["scanner"]["strict"], [])
        self.assertIsNone(p["india_vix"])
        self.assertEqual(p["risk_per_trade_pct"], config.RISK_PER_TRADE_PCT)
        self.assertEqual(p["adr_moves_pct"], {})
        self.assertIsNone(p["global_supporting"]["sp500_pct"])
        self.assertEqual(p["bias"], "neutral")
        joined = " ".join(p["missing"])
        for word in ("NSE prices for 2026-11-13", "technical scanner", "India VIX", "S&P 500"):
            self.assertIn(word, joined)
        self.assertIn("Left out (not fresh)", brief.plan_message(p, ["TCS"]))

    def test_holiday_is_not_a_gap(self):
        p = plan(sessions={"NSE": "2026-11-13", "US": "2026-11-13"}, today=date(2026, 11, 17))
        self.assertEqual(p["holidays"], {"NSE": ["2026-11-16"], "US": ["2026-11-16"]})
        self.assertEqual(p["missing"], [])                       # Friday is the right day after a Monday holiday

    def test_without_freshness_table_uses_row_dates(self):
        data = {**SAMPLE, "freshness": []}
        data["unusual_in"] = [dict(SAMPLE["unusual_in"][1], trade_date="2026-11-12")]
        self.assertEqual(plan(data, failed=("freshness",))["unusual_volume_nse"], [])

    def test_news_radar_and_tradable_universe(self):
        data = {**SAMPLE,
                "universe": [{"symbol": "GODREJIND"}, {"symbol": "HDFCBANK"}],
                "radar": [{"symbol": "GODREJIND", "company": "Godrej Industries", "industry": "Diversified",
                           "close": 1095.0, "return_pct": 11.88, "volume_ratio": 29.6,
                           "reasons": "NSE filing: mna, moved 11.9%, 29.6x usual volume",
                           "latest_news_title": "Primary infusion of capital in Godrej Capital"}]}
        p = plan(data)
        self.assertEqual(p["tradable_universe"], ["GODREJIND", "HDFCBANK"])
        self.assertEqual(p["news_radar"][0]["symbol"], "GODREJIND")
        self.assertIn("29.6x usual volume", p["news_radar"][0]["why"])
        self.assertEqual(p["unusual_volume_nse"], [])          # TATAPOWER isn't in the tradable universe

    def test_risk_scales_with_vix(self):
        base = config.RISK_PER_TRADE_PCT
        self.assertEqual(brief.risk_for_vix(None), base)
        self.assertEqual(brief.risk_for_vix(14), base)
        self.assertEqual(brief.risk_for_vix(22), round(base * 0.75, 2))
        self.assertEqual(brief.risk_for_vix(35), round(base * 0.5, 2))

    def test_unavailable_lakehouse_degrades_to_defaults(self):
        def broken(_sql):
            raise RuntimeError("Databricks not configured")
        data, failed = brief.fetch(broken)
        p = brief.build_plan(data, failed)
        self.assertEqual(p["source"], "unavailable")
        self.assertEqual(p["bias"], "neutral")
        self.assertEqual(p["risk_per_trade_pct"], config.RISK_PER_TRADE_PCT)
        self.assertIn("Lakehouse data unavailable", brief.plan_message(p, ["TCS"]))

    def test_message_is_short(self):
        msg = brief.plan_message(plan(), ["RELIANCE", "TATAPOWER"])
        self.assertIn("India VIX 24.3", msg)
        self.assertIn("Scanner strict picks: TATAPOWER", msg)
        self.assertLess(len(msg.splitlines()), 10)

    def test_scanner_status(self):
        p = plan()
        self.assertEqual(brief.scanner_status(p, "TATAPOWER"), "strict")
        self.assertEqual(brief.scanner_status(p, "TCS"), "near")
        self.assertEqual(brief.scanner_status(p, "INFY"), "none")
        self.assertEqual(brief.scanner_status({}, "INFY"), "none")

    def test_fetch_live_reads_calendars_and_quotes(self):
        def daily(sym, rng):
            px = {"^NSEI": 24000, "^GSPC": 6500, "^INDIAVIX": 14.0}.get(sym, 100.0)
            return [{"time": "2026-11-12 09:15", "close": px * 0.99}, {"time": "2026-11-13 09:15", "close": px},
                    {"time": "2026-11-16 09:15", "close": px * 1.5}]       # today's unfinished bar is ignored
        sessions, lv = brief.fetch_live(TODAY, daily)
        self.assertEqual(sessions, {"NSE": "2026-11-13", "US": "2026-11-13"})
        self.assertEqual(lv["india_vix"]["close"], 14.0)
        self.assertAlmostEqual(lv["sp500"]["change_pct"], 1.01, places=2)


if __name__ == "__main__":
    unittest.main()
