"""Free NSE market data via Yahoo Finance. Prices may lag a few minutes."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


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


def candles(symbol: str, interval: str = "5m", days: int = 1):
    """Return a list of dicts (IST times) for the last `days` trading days."""
    import yfinance as yf

    df = yf.Ticker(to_yahoo(symbol)).history(period=f"{max(days, 1)}d", interval=interval)
    if df is None or df.empty:
        return []
    df.index = df.index.tz_convert(IST)
    return [
        {
            "time": ts.strftime("%Y-%m-%d %H:%M"),
            "open": round(float(r.Open), 2),
            "high": round(float(r.High), 2),
            "low": round(float(r.Low), 2),
            "close": round(float(r.Close), 2),
            "volume": int(r.Volume),
        }
        for ts, r in df.iterrows()
    ]


def last_price(symbol: str):
    """Latest 1-minute close and its timestamp."""
    rows = candles(symbol, "1m", 1)
    if not rows:
        return None, None
    return rows[-1]["close"], rows[-1]["time"]


def daily(symbol: str, days: int = 5):
    return candles(symbol, "1d", days)


def is_stale(ts: str, max_minutes: int = 15) -> bool:
    t = datetime.strptime(ts, "%Y-%m-%d %H:%M").replace(tzinfo=IST)
    return now_ist() - t > timedelta(minutes=max_minutes)
