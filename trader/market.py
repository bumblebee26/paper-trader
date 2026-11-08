"""Free NSE prices from Yahoo Finance's chart endpoint (standard library only).

Prices can lag a few minutes. Symbols: RELIANCE -> RELIANCE.NS, NIFTY -> ^NSEI.
"""
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from http_client import get_json

IST = ZoneInfo("Asia/Kolkata")
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval={interval}&range={range}"


def now_ist() -> datetime:
    return datetime.now(IST)


def to_yahoo(symbol: str) -> str:
    s = symbol.strip().upper()
    aliases = {"NIFTY": "^NSEI", "NIFTY50": "^NSEI", "BANKNIFTY": "^NSEBANK"}
    if s in aliases:
        return aliases[s]
    if s.startswith("^") or s.endswith(".NS") or s.endswith(".BO"):
        return s
    return s + ".NS"


def _parse(payload):
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return []
    r = result[0]
    stamps = r.get("timestamp") or []
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    rows = []
    for i, ts in enumerate(stamps):
        o, h, l, c = (q.get(k, [None] * len(stamps))[i] for k in ("open", "high", "low", "close"))
        if None in (o, h, l, c):
            continue  # Yahoo leaves gaps as nulls
        v = (q.get("volume") or [0] * len(stamps))[i] or 0
        t = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(IST)
        rows.append({"time": t.strftime("%Y-%m-%d %H:%M"), "open": round(o, 2), "high": round(h, 2),
                     "low": round(l, 2), "close": round(c, 2), "volume": int(v)})
    return rows


def candles(symbol: str, interval: str = "5m", days: int = 1):
    rng = f"{max(days, 1)}d"
    url = CHART_URL.format(symbol=urllib.parse.quote(to_yahoo(symbol)), interval=interval, range=rng)
    return _parse(get_json(url))


def last_price(symbol: str):
    rows = candles(symbol, "1m", 1)
    if not rows:
        return None, None
    return rows[-1]["close"], rows[-1]["time"]


def is_stale(ts: str, max_minutes: int = 15) -> bool:
    t = datetime.strptime(ts, "%Y-%m-%d %H:%M").replace(tzinfo=IST)
    return now_ist() - t > timedelta(minutes=max_minutes)
