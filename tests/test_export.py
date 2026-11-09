"""Daily backup feed: journal -> JSON lines (+ ledger snapshot) -> S3 copy + Databricks volume."""
import json
import os
import unittest
from datetime import datetime
from unittest import mock

from helpers import Env, minute_bars

import engine
import export
import handlers
import storage


def trade_day(env):
    """One approved winning trade and one rejected proposal."""
    env.prices["RELIANCE"] = 1452.0
    st = engine.load()
    pid = engine.propose(st, "RELIANCE", "BUY", "ORB long", 1452.0, 1441.5, 1473.0, ["a"], "x")["proposal_id"]
    env.tap("yes", pid)
    engine.wait_decision(st, pid, 1)
    engine.execute(st, pid)
    pid2 = engine.propose(st, "TCS", "SELL", "VWAP rejection", 3000.0, 3020.0, 2960.0, ["b"], "y")["proposal_id"]
    env.tap("no", pid2)
    engine.wait_decision(st, pid2, 1)
    env.bars[("RELIANCE", "1m")] = minute_bars(datetime(2026, 11, 9, 10, 41), [(1458, 1475, 1457, 1474)])
    env.bars[("TCS", "1m")] = minute_bars(datetime(2026, 11, 9, 10, 41), [(2990, 2995, 2950, 2955)])
    env.at(10, 45)
    engine.monitor(st)
    engine.save(st)
    return st, pid, pid2


class ExportTest(unittest.TestCase):
    def test_jsonl_has_ids_types_and_snapshot(self):
        with Env() as env:
            st, pid, _ = trade_day(env)
            lines = [json.loads(x) for x in export.build("2026-11-09").splitlines()]
            events = [e["event"] for e in lines]
            self.assertEqual(events[-1], "ledger_snapshot")
            self.assertEqual(lines[-1]["balance"], round(st["balance"], 2))
            self.assertEqual(len({e["event_id"] for e in lines}), len(lines))  # unique ids
            fill = next(e for e in lines if e["event"] == "filled")
            exit_ = next(e for e in lines if e["event"] == "exited")
            self.assertEqual(fill["proposal_id"], pid)
            self.assertEqual(exit_["proposal_id"], pid)  # round trip can be joined
            self.assertIsInstance(fill["fill_price"], float)
            self.assertEqual(fill["fees_rs"], 20.0)
            self.assertIsNone(fill["exit_price"])  # empty -> null, not ""
            # The lakehouse reconciliation: exit P&L minus entry fees == ledger's realized P&L.
            computed = sum(e["pnl_rs"] for e in lines if e["event"] == "exited") - fill["fees_rs"]
            self.assertAlmostEqual(computed, lines[-1]["realized_today"], places=2)

    def test_passed_outcome_has_fixed_id(self):
        with Env() as env:
            st, _, pid2 = trade_day(env)
            env.at(15, 30)
            engine.report(st, send=False)
            engine.report(st, send=False)  # re-run
            rows = [r for r in storage.read_rows("journal/2026-11-09.csv") if r["event"] == "passed_outcome"]
            self.assertEqual({r["event_id"] for r in rows}, {f"passed-{pid2}"})
            self.assertGreater(float(rows[0]["pnl_rs"]), 0)  # the skipped short would have won

    def test_guardian_export_pushes_to_volume(self):
        with Env() as env:
            trade_day(env)
            calls = []
            with mock.patch.dict(os.environ, {"DATABRICKS_URL": "https://dbc-x.cloud.databricks.com",
                                              "DATABRICKS_TOKEN": "dapi-test"}), \
                    mock.patch.object(export, "request", lambda *a, **k: calls.append((a, k)) or b""):
                out = handlers.guardian({"action": "export"}, None)
            self.assertEqual(out["volume_path"], "/Volumes/stocks/bronze/landing/trades/date=2026-11-09/events.jsonl")
            self.assertTrue(calls[1][0][1].endswith("/trades/date=2026-11-09/events.jsonl?overwrite=true"))
            self.assertTrue(storage.exists("exports/date=2026-11-09/events.jsonl"))

    def test_no_journal_no_push(self):
        with Env():
            self.assertEqual(export.export_day("2026-11-10"), {"date": "2026-11-10", "skipped": "no journal"})


if __name__ == "__main__":
    unittest.main()


class PortfolioExportTest(unittest.TestCase):
    def test_pools_and_holdings(self):
        import json as _json
        from unittest import mock
        import pools
        with Env(capital=40_000) as env:
            env.prices["NIFTYBEES"] = 280.0
            pools.park("swing")
            env.prices["NIFTYBEES"] = 285.0
            engine.save(engine.load())
            with mock.patch.object(export, "_push", return_value="/Volumes/x") as push:
                out = export.export_portfolio("2026-11-10")
            rows = [_json.loads(x) for x in push.call_args.args[1].splitlines()]
            self.assertEqual(push.call_args.args[0], "portfolio/date=2026-11-10/portfolio.jsonl")
            pools_ = {r["pool"]: r for r in rows if r["record_type"] == "pool"}
            self.assertEqual(set(pools_), {"intraday", "swing", "long_term"})
            self.assertEqual(pools_["long_term"]["equity"], 100_000)
            h = [r for r in rows if r["record_type"] == "holding"][0]
            self.assertEqual((h["pool"], h["symbol"], h["sector"], h["last_price"]), ("swing", "NIFTYBEES", "Index fund", 285.0))
            self.assertGreater(h["pnl"], 0)
            self.assertEqual(out["portfolio_rows"], 4)
