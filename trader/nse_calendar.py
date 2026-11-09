"""NSE trading holidays, fetched live from NSE's own holiday list (nothing hard-coded).

Source: https://www.nseindia.com/api/holiday-master?type=trading (the "CM", cash market, list).
Each trading session's claim on the GitHub runner fetches it and caches it in S3 at
state/nse_holidays.json; the Lambdas (guardian, responder) only read that cache, so a slow or blocked
NSE never delays them. Elsewhere the cache is refreshed when it is missing or more than 7 days old. If NSE and the cache both fail, every weekday
counts as a trading day: the session then notices the empty market by 09:30, as before.
"""
import os
from datetime import datetime, timedelta

import market
import storage
from http_client import get_json, log

URL = "https://www.nseindia.com/api/holiday-master?type=trading"
KEY = "state/nse_holidays.json"
MAX_AGE_DAYS = 7
HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0 Safari/537.36"),
           "Accept": "application/json, */*", "Accept-Language": "en-US,en;q=0.9",
           "Referer": "https://www.nseindia.com/resources/exchange-communication-holidays"}
_cache = None


def reset_cache():
    global _cache
    _cache = None


def parse(payload) -> dict:
    """{'2026-11-10': 'Diwali-Balipratipada', ...} from NSE's response (weekends included as listed)."""
    out = {}
    for row in (payload or {}).get("CM", []):
        try:
            day = datetime.strptime(row["tradingDate"], "%d-%b-%Y").strftime("%Y-%m-%d")
        except (KeyError, ValueError):
            continue
        out[day] = (row.get("description") or "NSE holiday").rstrip("*").strip()
    return out


def fetch() -> dict:
    return parse(get_json(URL, headers=HEADERS, retries=2, timeout=15))


def refresh():
    """Fetch the list from NSE and cache it. Returns the holidays, or None if NSE couldn't be read."""
    global _cache
    try:
        days = fetch()
    except Exception as err:  # noqa: BLE001 - never block a session on the calendar
        log.warning("NSE holiday list unavailable: %s", err)
        return None
    if not days:
        log.warning("NSE holiday list was empty; keeping the cache")
        return None
    storage.put_json(KEY, {"fetched_at": market.now_ist().strftime("%Y-%m-%d %H:%M:%S"), "source": URL,
                           "holidays": days})
    _cache = days
    return days


def holidays() -> dict:
    """The cached list (refreshed from NSE when missing or older than a week); {} if unavailable."""
    global _cache
    if _cache is not None:
        return _cache
    cached = storage.get_json(KEY) or {}
    fresh = False
    if cached.get("fetched_at"):
        t = datetime.strptime(cached["fetched_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=market.IST)
        fresh = market.now_ist() - t < timedelta(days=MAX_AGE_DAYS)
    in_lambda = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
    if not fresh and not in_lambda and refresh() is not None:
        return _cache
    _cache = cached.get("holidays") or {}
    return _cache
