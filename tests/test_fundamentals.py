"""Weekly fundamentals job: parsing Yahoo's time series, universe, push. No network."""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "jobs"))

from helpers import Env  # noqa: E402

import fundamentals  # noqa: E402

SAMPLE = json.dumps({"timeseries": {"result": [
    {"meta": {"symbol": ["TCS.NS"], "type": ["annualNetIncome"]}, "timestamp": [1],
     "annualNetIncome": [{"asOfDate": "2025-03-31", "periodType": "12M", "currencyCode": "INR",
                          "reportedValue": {"raw": 485530000000.0}},
                         None,
                         {"asOfDate": "2026-03-31", "periodType": "12M", "currencyCode": "INR",
                          "reportedValue": {"raw": 492100000000.0}}]},
    {"meta": {"symbol": ["TCS.NS"], "type": ["quarterlyDilutedEPS"]}, "timestamp": [1],
     "quarterlyDilutedEPS": [{"asOfDate": "2026-09-30", "periodType": "3M", "currencyCode": "INR",
                              "reportedValue": {"raw": 38.37}}]},
    {"meta": {"symbol": ["TCS.NS"], "type": ["annualEBIT"]}},
], "error": None}})


class FundamentalsTest(unittest.TestCase):
    def test_parse(self):
        rows = fundamentals.parse("TCS", SAMPLE, "2026-11-14 07:15:00")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], {"symbol": "TCS", "source_symbol": "TCS.NS", "metric": "NetIncome",
                                   "period_type": "12M", "period_end": "2025-03-31", "value": 485530000000.0,
                                   "currency": "INR", "source": "yahoo_fundamentals",
                                   "fetched_at_ist": "2026-11-14 07:15:00"})
        self.assertEqual((rows[2]["metric"], rows[2]["period_type"]), ("DilutedEPS", "3M"))

    def test_members_csv(self):
        text = ("Company Name,Industry,Symbol,Series,ISIN Code\n"
                "Tata Consultancy Services Ltd.,Information Technology,TCS,EQ,INE467B01029\n"
                "Mahindra & Mahindra Ltd.,Automobile and Auto Components,M&M,EQ,INE101A01026\n")
        m = fundamentals.parse_members(text)
        self.assertEqual(m["M&M"], {"company": "Mahindra & Mahindra Ltd.", "industry": "Automobile and Auto Components",
                                    "isin": "INE101A01026"})

    def test_run_pushes_figures_and_members(self):
        members = {"TCS": {"company": "TCS", "industry": "Information Technology", "isin": "X"}}
        with Env(capital=40_000):
            with mock.patch.object(fundamentals, "nifty200", return_value=members), \
                    mock.patch.object(fundamentals, "universe", return_value=["TCS", "M&M"]), \
                    mock.patch.object(fundamentals, "request", return_value=SAMPLE) as req, \
                    mock.patch.object(fundamentals.time, "sleep"), \
                    mock.patch("export._push", return_value="/Volumes/x") as push:
                fundamentals.main(["x"])
            self.assertIn("M%26M.NS", req.call_args_list[1].args[1])
            (rel, body), (rel2, body2) = (c.args for c in push.call_args_list)
            self.assertEqual(rel, "fundamentals/date=2026-11-09/fundamentals.jsonl")
            self.assertEqual(body.count("\n"), 6)
            self.assertEqual(rel2, "index_members/date=2026-11-09/nifty200.jsonl")
            self.assertIn('"industry":"Information Technology"', body2)

    def test_mostly_failing_pushes_nothing(self):
        with Env(capital=40_000):
            with mock.patch.object(fundamentals, "nifty200", return_value={}), \
                    mock.patch.object(fundamentals, "universe", return_value=["A", "B", "C"]), \
                    mock.patch.object(fundamentals, "request", side_effect=RuntimeError("HTTP 429")), \
                    mock.patch.object(fundamentals.time, "sleep"), \
                    mock.patch("export._push") as push:
                with self.assertRaises(SystemExit):
                    fundamentals.main(["x"])
            push.assert_not_called()


if __name__ == "__main__":
    unittest.main()
