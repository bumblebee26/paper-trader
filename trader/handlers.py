"""AWS Lambda entry points (deployed by infra/aws, zip of this folder, stdlib + boto3 only).

kicker   Starts a GitHub Actions trading session at an exact IST time (GitHub's own cron can
         start 10-60 minutes late). Event: {"phase": "morning" | "afternoon"}.
guardian Backstop that needs no Claude. Event {"action": "squareoff"} at 15:12 IST closes
         any open dummy positions if no live session is running; {"action": "report"} at
         15:40 IST sends the day report if the session didn't.
"""
import json
import os

import config
import engine
from http_client import log, request


def kicker(event, context):
    # Settings use DISPATCH_* names: GitHub runners define GITHUB_REF / GITHUB_TOKEN themselves.
    phase = (event or {}).get("phase", "morning")
    token = config.secret("DISPATCH_TOKEN", "DISPATCH_TOKEN_PARAM")
    if not token:
        raise RuntimeError("GitHub token not set in SSM")
    repo = os.environ["DISPATCH_REPO"]
    workflow = os.environ.get("DISPATCH_WORKFLOW", "trading-session.yml")
    ref = os.environ.get("DISPATCH_REF", "main")
    request(
        "POST",
        f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"},
        data=json.dumps({"ref": ref, "inputs": {"phase": phase}}).encode(),
        log_url=f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
    )
    log.info("Dispatched %s session on %s@%s", phase, repo, ref)
    return {"dispatched": phase}


def guardian(event, context):
    action = (event or {}).get("action", "squareoff")
    if action == "squareoff":
        if engine.session_alive():
            log.info("Live session is running; it squares off itself")
            return {"skipped": "session alive"}
        state = engine.load()
        try:
            if not state["positions"]:
                return {"skipped": "no open positions"}
            out = engine.monitor(state)
            return {"squared_off": out["events"]}
        finally:
            engine.save(state)
    if action == "report":
        if engine.report_exists():
            return {"skipped": "report already sent"}
        state = engine.load()
        try:
            if state.get("trades_today", 0) == 0 and not state.get("proposals"):
                return {"skipped": "no activity today"}
            return {"report": engine.report(state, send=True)}
        finally:
            engine.save(state)
    raise ValueError(f"unknown action {action}")
