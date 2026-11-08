"""Daily backup feed to the stocks_lakehouse: the day's journal as JSON lines.

  S3:          exports/date=YYYY-MM-DD/events.jsonl
  Databricks:  /Volumes/stocks/bronze/landing/trades/date=YYYY-MM-DD/events.jsonl

One file per day, overwritten on a re-run (the lakehouse keeps the newest copy of each
event_id). The last line is a ledger snapshot (balance and realized P&L at export time),
so the lakehouse can reconcile its own P&L against the S3 ledger.

Pushed once a day (15:45 IST, guardian Lambda) so the lakehouse pipeline isn't woken
up every few minutes during market hours.
"""
import json
import urllib.parse
from datetime import date, timedelta

import config
import storage
from http_client import log, request

NUMERIC = {"entry", "stop", "target", "qty", "risk_rs", "rr", "fill_price", "exit_price", "pnl_rs",
           "fees_rs", "balance", "starting_capital", "realized_today"}
FIELDS = ["event_id", "proposal_id", "timestamp_ist", "event", "symbol", "setup", "side", "entry", "stop",
          "target", "qty", "risk_rs", "rr", "reasons", "user_reply", "fill_price", "exit_price",
          "exit_reason", "pnl_rs", "fees_rs", "note", "balance", "starting_capital", "realized_today"]


def _typed(row):
    out = {}
    for f in FIELDS:
        v = row.get(f, "")
        if v in ("", None):
            out[f] = None
        elif f in NUMERIC:
            try:
                out[f] = float(v)
            except (TypeError, ValueError):
                out[f] = None
        else:
            out[f] = str(v)
    return out


def _snapshot(day):
    """Balance at the end of `day`: today's live ledger, or the saved day report."""
    state = storage.get_json(storage.LEDGER_KEY) or {}
    if state.get("day") == day:
        bal, realized, start = state["balance"], state.get("realized_today", 0.0), state["starting_capital"]
        when = f"{day} 23:59:59"
    else:
        rep = storage.get_json(f"reports/{day}.json")
        if not rep:
            return None
        bal, realized = rep["balance_rs"], rep["realized_today_rs"]
        start = rep["balance_rs"] - rep["total_pnl_since_start_rs"]
        when = f"{day} 23:59:59"
    return {"event_id": f"snapshot-{day}", "timestamp_ist": when, "event": "ledger_snapshot",
            "balance": round(bal, 2), "starting_capital": round(start, 2),
            "realized_today": round(realized, 2), "note": "ledger at export time"}


def build(day):
    """Return the JSONL text for `day`, or None if nothing happened that day."""
    rows = storage.read_rows(f"journal/{day}.csv")
    if not rows:
        return None
    events = [_typed(r) for r in rows]
    snap = _snapshot(day)
    if snap:
        events.append(_typed(snap))
    return "\n".join(json.dumps(e, separators=(",", ":")) for e in events) + "\n"


def _push(relative, body):
    host = config.secret("DATABRICKS_URL", "DATABRICKS_URL_PARAM").rstrip("/")
    token = config.secret("DATABRICKS_TOKEN", "DATABRICKS_TOKEN_PARAM")
    if not (host and token):
        raise RuntimeError("Databricks URL/token not configured")
    volume = config.LANDING_VOLUME_PATH.rstrip("/")
    path = f"{volume}/{relative}"
    auth = {"Authorization": f"Bearer {token}"}
    quote = lambda p: urllib.parse.quote(p, safe="/=")  # noqa: E731
    request("PUT", f"{host}/api/2.0/fs/directories{quote(path.rsplit('/', 1)[0])}", headers=auth)
    request("PUT", f"{host}/api/2.0/fs/files{quote(path)}?overwrite=true",
            headers={**auth, "Content-Type": "application/octet-stream"}, data=body.encode(), timeout=120)
    return path


def export_day(day, push=True):
    body = build(day)
    if body is None:
        return {"date": day, "skipped": "no journal"}
    storage.backend().put(f"exports/date={day}/events.jsonl", body)
    result = {"date": day, "events": body.count("\n")}
    if push:
        result["volume_path"] = _push(f"trades/date={day}/events.jsonl", body)
        log.info("Exported %s events for %s to %s", result["events"], day, result["volume_path"])
    return result


def export_range(start, end, push=True):
    d, last, out = date.fromisoformat(start), date.fromisoformat(end), []
    while d <= last:
        if d.weekday() < 5:
            out.append(export_day(d.isoformat(), push=push))
        d += timedelta(days=1)
    return out
