"""NSE pre-open auction (from 24 Nov 2026; information only).

09:00-09:08 IST NSE collects orders and sets each stock's opening price (the IEP, indicative equilibrium price).
From 25 Nov the morning session starts at 08:55 and its wait_for_open tool reads NSE's pre-open data for F&O stocks
(most of the Nifty 200) twice: at 09:04 (indicative, orders can still change; saved only, never sent) and after the
random close at ~09:09:30 (final; sent with the day plan). Before 09:00 nothing is read. It
keeps the tradable ones and records, per stock: IEP, gap vs yesterday's close and the buy/sell quantity ratio. It is
saved to S3 (preopen/YYYY-MM-DD.json) and shown in the plan message and the 09:25 ranking. It is NOT given to the
trading agent or the reviewer and changes no decision until a backtest shows it helps (docs/DECISIONS.md section
14). NSE often blocks cloud servers: then the day has no pre-open line, and nothing else changes.
"""
import market
import storage
from http_client import get_json, log

URL = "https://www.nseindia.com/api/market-data-pre-open?key=FO"
HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0 Safari/537.36"),
           "Accept": "application/json, */*", "Accept-Language": "en-US,en;q=0.9",
           "Referer": "https://www.nseindia.com/market-data/pre-open-market-cm-and-emerge-market"}
TOP = 5


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse(payload, tradable=None) -> dict:
    """{symbol: {iep, prev_close, gap_pct, buy_qty, sell_qty, buy_sell}} from NSE's response."""
    out = {}
    for row in (payload or {}).get("data") or []:
        meta = row.get("metadata") or {}
        pre = (row.get("detail") or {}).get("preOpenMarket") or {}
        sym = str(meta.get("symbol") or "").upper()
        if not sym or (tradable is not None and sym not in tradable):
            continue
        iep = _num(meta.get("iep") or pre.get("IEP"))
        prev = _num(meta.get("previousClose"))
        if not iep or not prev:
            continue
        buy, sell = _num(pre.get("totalBuyQuantity")), _num(pre.get("totalSellQuantity"))
        out[sym] = {"iep": iep, "prev_close": prev, "gap_pct": round((iep / prev - 1) * 100, 2),
                    "buy_qty": buy, "sell_qty": sell,
                    "buy_sell": round(buy / sell, 2) if buy and sell else None,
                    "matched_qty": _num(meta.get("finalQuantity") or pre.get("finalQuantity"))}
    return out


def key(day=None, kind="final") -> str:
    day = day or market.now_ist().strftime("%Y-%m-%d")
    return f"preopen/{day}.json" if kind == "final" else f"preopen/{day}_{kind}.json"


def capture(tradable=None, fetch=None, kind="final") -> dict:
    """Read NSE's pre-open data and save it under `kind` ('final' or 'indicative'); {} if NSE can't be read or the
    auction hasn't started (before 09:00 IST the page still shows yesterday's)."""
    if market.now_ist().strftime("%H:%M") < "09:00":
        return {}
    try:
        payload = (fetch or (lambda: get_json(URL, headers=HEADERS, retries=2, timeout=15)))()
    except Exception as err:  # noqa: BLE001 - information only: never block the session
        log.warning("NSE pre-open unavailable: %s", err)
        return {}
    stocks = parse(payload, tradable)
    if stocks:
        storage.put_json(key(kind=kind), {"read_at": market.now_ist().strftime("%Y-%m-%d %H:%M:%S"), "source": URL,
                                 "stocks": stocks})
    return stocks


def today(kind="final") -> dict:
    """Today's saved pre-open data ({} if none)."""
    try:
        return (storage.get_json(key(kind=kind)) or {}).get("stocks") or {}
    except Exception:  # noqa: BLE001
        return {}


def lines(stocks) -> list:
    """Plan-message lines: the biggest gaps up and down, with the buy/sell ratio."""
    if not stocks:
        return ["Pre-open (NSE 09:00-09:08): not available today (information only)."]

    def fmt(s, r):
        ratio = f", buy/sell {r['buy_sell']:g}x" if r.get("buy_sell") else ""
        return f"{s} {r['gap_pct']:+.1f}%{ratio}"

    ranked = sorted(stocks.items(), key=lambda kv: kv[1]["gap_pct"])
    up = [fmt(s, r) for s, r in reversed(ranked[-TOP:]) if r["gap_pct"] > 0]
    down = [fmt(s, r) for s, r in ranked[:TOP] if r["gap_pct"] < 0]
    out = [f"Pre-open (NSE auction, {len(stocks)} stocks; information only):"]
    out.append("Gaps up: " + ("; ".join(up) or "none"))
    out.append("Gaps down: " + ("; ".join(down) or "none"))
    return out


def note(stocks, symbol) -> str:
    """' · pre-open +1.2%, buy/sell 1.8x' for one stock, or ''."""
    r = (stocks or {}).get(symbol.upper())
    if not r:
        return ""
    return f" · pre-open {r['gap_pct']:+.1f}%" + (f", buy/sell {r['buy_sell']:g}x" if r.get("buy_sell") else "")
