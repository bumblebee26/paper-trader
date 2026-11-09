"""AWS Lambda entry points (deployed by infra/aws, zip of this folder, stdlib + boto3 only).

kicker   Starts a GitHub Actions trading session at an exact IST time (GitHub's own cron can
         start 10-60 minutes late). Event: {"phase": "morning" | "afternoon"}.
responder Every 2 minutes, only while no session is live: answers Telegram commands (/help,
         /ledger, /plan, /pause, /resume) from the S3 ledger, so the bot replies any time.
guardian Backstop that needs no Claude. Event {"action": "squareoff"} at 15:12 IST closes
         any open dummy positions if no live session is running; {"action": "report"} at
         15:40 IST sends the day report if the session didn't; {"action": "export"} at 15:45
         IST copies the day's journal to the lakehouse (add "date", or "start"/"end", to
         re-export past days); {"action": "park"} at 15:14 IST puts the swing pool's idle
         cash into NIFTYBEES (does nothing once invested); {"action": "weekly"} on Fridays at 17:55 IST sends the weekly report.
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
    if action == "park":
        import pools
        import telegram_bot as tg

        out = pools.park("swing")
        if out["status"] == "bought":
            tg.send(f"[MOCK] Swing pool: bought {out['qty']} NIFTYBEES at Rs {out['fill']:,.2f} "
                    f"(cash left Rs {out['cash_left']:,.0f}).\nIn plain words: the swing rules failed their backtest, "
                    "so as you decided, this Rs 60,000 now simply follows the Nifty 50 until a better swing idea "
                    "passes a test. /portfolio shows how it's doing.")
        return out
    if action == "weekly":
        import weekly

        state = engine.load()
        try:
            return {"weekly": weekly.run(state)["week"]}
        finally:
            engine.save(state)
    if action == "export":
        import export

        ev = event or {}
        if ev.get("start"):
            return {"exports": export.export_range(ev["start"], ev.get("end", ev["start"]))}
        day = ev.get("date") or engine._today()
        out = export.export_day(day)
        try:
            out.update(export.export_portfolio(day))
        except Exception as err:  # noqa: BLE001 - the journal export must not fail because of this
            log.warning("portfolio export failed: %s", err)
            out["portfolio_error"] = str(err)
        return out
    raise ValueError(f"unknown action {action}")


def responder(event, context):
    """Answer Telegram commands outside sessions. Never runs alongside a session: Telegram allows
    one reader, and the session's own polling answers commands while it's live."""
    import telegram_bot as tg

    if engine.session_active():
        return {"skipped": "session live"}
    state = engine.load()
    try:
        try:
            tg.poll(state, wait=0)
        except tg.TelegramError as err:  # e.g. a session started a moment ago
            log.info("poll skipped: %s", err)
            return {"skipped": str(err)}
        exits = state.pop("exit_requests", [])
        if exits:
            if state["positions"]:
                tg.send("[MOCK] The market session isn't running, so I can't close at a live price now. "
                        "Open positions are closed by 15:10 on trading days.")
            else:
                tg.send("[MOCK] No open positions; nothing to close.")
        state["exit_requests"] = []
        answered = engine.answer_requests(state)
        return {"answered": answered + (1 if exits else 0)}
    finally:
        engine.save(state)
