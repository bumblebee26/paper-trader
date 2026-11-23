"""Expiry calendar (from 24 Nov): Telegram reminders before a token or the AWS free plan runs out, so nothing stops
silently. Checked once a day by the guardian's 15:45 export run.

The dates below are estimates from when each piece was set up (7-8 Nov 2026). Correct them, or add more, without a
code change: put a JSON list in S3 at config/expiries.json, e.g.
  [{"name": "Databricks pusher token", "expires": "2027-05-06", "renew": "..."}]
Nothing secret is stored here: only names and dates.
"""
from datetime import date

import storage
import telegram_bot as tg

DEFAULTS = [
    {"name": "Databricks pusher token (180 days)", "expires": "2027-05-06",
     "renew": "create a new token in Databricks and put it in SSM /stocks/databricks-pusher-token (lakehouse runbook)"},
    {"name": "AWS free plan (6 months, or sooner if the $200 credits run out)", "expires": "2027-05-06",
     "renew": "upgrade the account to a paid plan in the AWS console (Billing), or everything is suspended"},
    {"name": "Claude token for the trading sessions (1 year)", "expires": "2027-11-07",
     "renew": "run `claude setup-token` and update CLAUDE_CODE_OAUTH_TOKEN in the GitHub environment 'trading'"},
    {"name": "GitHub dispatch token for the kicker (1 year)", "expires": "2027-11-08",
     "renew": "new fine-grained token (Actions: read and write on paper-trader), then put it in SSM "
              "/stocks-trader/github-dispatch-token"},
]
REMIND_AT = (30, 7, 3, 1)
SENT_KEY = "state/expiry_sent.json"


def calendar():
    return storage.get_json("config/expiries.json") or DEFAULTS


def due(today: date, items=None):
    """[(item, days left)] that need a reminder today: at REMIND_AT days before, and every day once expired."""
    out = []
    for it in items if items is not None else calendar():
        left = (date.fromisoformat(it["expires"]) - today).days
        if left in REMIND_AT or left <= 0:
            out.append((it, left))
    return out


def check(today: date, send=True):
    """Send today's reminders once (the export runs twice a day)."""
    sent = storage.get_json(SENT_KEY) or {}
    if sent.get("day") == today.isoformat():
        return {"skipped": "already checked today"}
    items = due(today)
    if items and send:
        lines = ["[MOCK] Renewal reminder (so nothing stops by itself):"]
        for it, left in items:
            when = f"in {left} day{'s' if left != 1 else ''}" if left > 0 else "today" if left == 0 else \
                f"{-left} day{'s' if left != -1 else ''} ago"
            lines.append(f"- {it['name']}: expires {when} ({date.fromisoformat(it['expires']):%d %b %Y}). "
                         f"To renew: {it['renew']}.")
        tg.send("\n".join(lines))
    storage.put_json(SENT_KEY, {"day": today.isoformat(), "reminded": [it["name"] for it, _ in items]})
    return {"reminders": len(items)}
