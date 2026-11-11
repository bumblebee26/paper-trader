"""The independent reviewer: only code-gathered facts in, one plain verdict out, never blocks."""
import subprocess
import unittest
from unittest import mock

from helpers import Env

import engine
import reviewer


class FakeRun:
    def __init__(self, stdout="", code=0, raise_=None):
        self.stdout, self.code, self.raise_, self.prompts, self.argv = stdout, code, raise_, [], None

    def __call__(self, argv, **kw):
        if self.raise_:
            raise self.raise_
        self.argv = argv
        self.prompts.append(argv[2])
        return subprocess.CompletedProcess(argv, self.code, stdout=self.stdout, stderr="boom")


def on():
    return mock.patch.multiple(reviewer, enabled=lambda: True)


class ReviewerTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(reviewer.parse('ok {"yes_pct": 30, "verdict": "no", "why": "Against  the market."}'),
                         {"yes_pct": 30, "verdict": "NO", "why": "Against the market."})
        self.assertEqual(reviewer.parse('{"yes_pct": 150, "why": "x"}')["yes_pct"], 100)
        self.assertEqual(reviewer.parse('{"yes_pct": 55}')["verdict"], "UNSURE")
        self.assertIsNone(reviewer.parse("no json here"))
        self.assertIsNone(reviewer.parse('{"verdict": "YES"}'))

    def test_off_by_default(self):
        self.assertFalse(reviewer.enabled())  # REVIEWER is only set in the session workflow

    def test_review_shapes_the_suggestion_and_never_sees_the_pitch(self):
        run = FakeRun('{"yes_pct": 20, "verdict": "NO", "why": "Third bank buy today while the market falls."}')
        with Env() as env, on(), mock.patch.object(subprocess, "run", run):
            env.prices["HDFCBANK"] = 1600.0
            st = engine.load()
            r = engine.propose(st, "HDFCBANK", "BUY", "ORB long", 1600.0, 1592.0, 1616.0,
                               ["SECRET-REASON broke OR high"], "x", summary="SECRET-SUMMARY looks great",
                               ai_yes_pct=80)
            msg = env.sent[-1]
            self.assertIn("Independent reviewer NO 20%: Third bank buy today while the market falls.", msg)
            self.assertIn("- Live checks 60%", msg)
            self.assertIn("- Trading agent 80%", msg)
            self.assertIn("Why 53% YES", msg)
            prompt = run.prompts[0]
            self.assertIn("Trade: BUY", prompt)
            self.assertIn("HDFCBANK", prompt)
            self.assertNotIn("SECRET", prompt)       # no proposer reasons or summary
            self.assertNotIn("80", prompt.split("FACTS")[1].split("Reply")[0].replace("1,600", ""))
            self.assertIn("--tools", run.argv)
            self.assertEqual(run.argv[run.argv.index("--tools") + 1], "")
            p = st["proposals"][r["proposal_id"]]
            self.assertEqual(p["review"]["verdict"], "NO")

    def test_earlier_losses_are_in_the_facts(self):
        run = FakeRun('{"yes_pct": 40, "verdict": "UNSURE", "why": "x"}')
        with Env() as env, on(), mock.patch.object(subprocess, "run", run):
            env.prices["HDFCBANK"] = 1600.0
            st = engine.load()
            r = engine.propose(st, "HDFCBANK", "BUY", "ORB long", 1600.0, 1592.0, 1616.0, ["a"], "x")
            env.tap("yes", r["proposal_id"])
            engine.wait_decision(st, r["proposal_id"], 1)
            engine.execute(st, r["proposal_id"])
            engine.close(st, "HDFCBANK", 1590.0, "stop_loss")
            engine.propose(st, "ICICIBANK", "BUY", "VWAP reclaim", 1580.0, 1575.0, 1590.0, ["a"], "x")
            self.assertIn("Today's closed trades: BUY HDFCBANK (ORB long) stop_loss", run.prompts[-1])

    def test_failures_never_block(self):
        for run in (FakeRun("", code=1), FakeRun("garbage"),
                    FakeRun(raise_=subprocess.TimeoutExpired("claude", 90))):
            with Env() as env, on(), mock.patch.object(subprocess, "run", run):
                st = engine.load()
                r = engine.propose(st, "TCS", "SELL", "VWAP rejection", 3000.0, 3010.0, 2980.0, ["a"], "x")
                self.assertEqual(r["status"], "pending")
                self.assertIn("Independent reviewer: unavailable this time (not counted)", env.sent[-1])


if __name__ == "__main__":
    unittest.main()
