"""Company news from the lakehouse (NSE RSS, fetched every minute in market hours by stocks-fetch-news).

Reads <news volume>/date=YYYY-MM-DD/latest.jsonl through the Databricks Files API: no SQL
warehouse, no pipeline run. Today's and the previous day's files are merged and cached for
1 minute. Any failure returns None ("news unavailable"), never an exception.
"""
import json
import re
import time
import urllib.parse
from datetime import datetime, timedelta

import config
import market
from http_client import log, request

CACHE_SEC = 60   # the lakehouse fetches NSE news every minute during market hours
_cache = {"at": 0.0, "items": None, "age_min": None, "loaded": False}
FAIL_CACHE_SEC = 120  # after a failure, try again in 2 minutes, not on every call
DATE_IN_TEXT = re.compile(r"(\d{1,2}-[A-Za-z]{3}-\d{4})")
CATEGORY_WORDS = {
    "results": "results", "board_meeting": "board meeting", "order": "order or contract",
    "management": "management change", "regulatory": "regulatory matter", "rating": "credit rating",
    "pledge": "share pledge", "insider": "insider or major-holder trade", "dividend_split_bonus":
    "dividend, split or bonus", "mna": "acquisition or merger", "meeting": "analyst or investor meeting",
    "corporate_action": "corporate action", "other": "announcement",
}
WORRYING = {"regulatory", "pledge", "rating"}


def _read(day):
    host = config.secret("DATABRICKS_URL", "DATABRICKS_URL_PARAM").rstrip("/")
    token = config.secret("DATABRICKS_TOKEN", "DATABRICKS_TOKEN_PARAM")
    if not (host and token):
        raise RuntimeError("Databricks not configured")
    path = f"{config.NEWS_VOLUME_PATH.rstrip('/')}/date={day}/latest.jsonl"
    url = f"{host}/api/2.0/fs/files{urllib.parse.quote(path, safe='/=')}"
    body = request("GET", url, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def items():
    """All news items of today and the previous day, newest first, or None if unavailable."""
    if _cache["loaded"] and time.time() - _cache["at"] < (CACHE_SEC if _cache["items"] is not None
                                                          else FAIL_CACHE_SEC):
        return _cache["items"]
    now = market.now_ist()
    prev = now - timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= timedelta(days=1)
    out, ok = [], False
    for d in (now, prev):
        try:
            out += _read(d.strftime("%Y-%m-%d"))
            ok = True
        except Exception as err:  # noqa: BLE001 - weekends/holidays have no file
            log.info("news for %s unavailable: %s", d.date(), err)
    if not ok:
        _cache.update(at=time.time(), items=None, age_min=None, loaded=True)
        return None
    seen, merged = set(), []
    for it in sorted(out, key=lambda i: i.get("published_ist") or i.get("fetched_at_ist") or "", reverse=True):
        if it.get("id") not in seen:
            seen.add(it.get("id"))
            merged.append(it)
    newest = max((i.get("fetched_at_ist") or "" for i in merged), default="")
    age = None
    if newest:
        t = datetime.strptime(newest, "%Y-%m-%d %H:%M:%S").replace(tzinfo=market.IST)
        age = int((now - t).total_seconds() // 60)
    _cache.update(at=time.time(), items=merged, age_min=age, loaded=True)
    return merged


def feed_age_min():
    """Minutes since the news feed was last fetched (None if unknown)."""
    items()
    return _cache["age_min"]


def for_symbol(symbol, limit=5):
    """News for one NSE symbol, newest first. None if the feed is unavailable, [] if none."""
    all_items = items()
    if all_items is None:
        return None
    return [i for i in all_items if (i.get("symbol") or "").upper() == symbol.upper()][:limit]


def when(item):
    t = item.get("published_ist") or item.get("fetched_at_ist") or ""
    try:
        return f"{datetime.strptime(t, '%Y-%m-%d %H:%M:%S'):%d %b %H:%M}"
    except ValueError:
        return "recently"


def describe(item):
    what = CATEGORY_WORDS.get(item.get("category"), "announcement")
    desc = " ".join((item.get("description") or item.get("title") or "").split())
    if len(desc) > 140:
        desc = desc[:137] + "..."
    return f"{when(item)}, {what}: {desc}"


def meeting_soon(item, days=2):
    """For a board-meeting item, the meeting date if it is within `days` from today."""
    if item.get("category") != "board_meeting":
        return None
    m = DATE_IN_TEXT.search(f"{item.get('title', '')} {item.get('description', '')}")
    if not m:
        return None
    try:
        d = datetime.strptime(m.group(1), "%d-%b-%Y").date()
    except ValueError:
        return None
    today = market.now_ist().date()
    return d if today <= d <= today + timedelta(days=days) else None


def lines_for(symbol, limit=3):
    """Telegram-ready lines for a proposal ('News: ...'), with links."""
    found = for_symbol(symbol, limit)
    if found is None:
        return ["News: unavailable right now (NSE feed not reachable)."]
    if not found:
        return ["News: no NSE filings for this company today or yesterday."]
    return ["News (NSE filings):"] + [f"- {describe(i)} {i.get('link', '')}".rstrip() for i in found]


def compact(symbol) -> str:
    """One line for the compact message (links are in the full reasons)."""
    found = for_symbol(symbol, 3)
    if found is None:
        return "News: NSE feed unavailable right now"
    if not found:
        return "News: no NSE filings today or yesterday"
    worrying = [i for i in found if i.get("category") in WORRYING]
    first = describe((worrying or found)[0])
    first = first if len(first) <= 100 else first[:100].rsplit(" ", 1)[0] + "…"
    return f"News{' ⚠️' if worrying else ''}: {first}" + (f" (+{len(found) - 1} more)" if len(found) > 1 else "")


def reset_cache():
    _cache.update(at=0.0, items=None, age_min=None, loaded=False)
