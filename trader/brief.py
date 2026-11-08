"""Pre-market context from the stocks_lakehouse gold tables (the same data as its 08:45 brief).

The trader uses it as the base for the day:
  - risk per trade scales down when VIX is elevated (enforced in code by the risk gate)
  - the US move bucket + India's historical opening gap after such days set the bias
  - NSE unusual-volume names and ADR moves seed the focus list

Databricks access reuses the lakehouse SSM parameters (/stocks/databricks-*). If they
aren't configured or a query fails, the day still runs with the default plan.
"""
import json
import time

import config
from http_client import request, log

NUMERIC = {"BYTE", "SHORT", "INT", "LONG", "FLOAT", "DOUBLE", "DECIMAL"}
US_PROXY = "SPY"
BIG_MOVE_PCT = 1.0
ADRS = {"INFY": "INFY", "WIT": "WIPRO", "HDB": "HDFCBANK", "IBN": "ICICIBANK", "RDY": "DRREDDY"}

QUERIES = {
    "us_bars": f"""
        SELECT b.trade_date, b.symbol, (b.close / b.prev_close - 1) * 100 AS return_pct
        FROM stocks.silver.daily_bars b
        WHERE b.market = 'US'
          AND b.trade_date = (SELECT max(trade_date) FROM stocks.silver.trading_days WHERE market = 'US')
          AND b.symbol IN ('{US_PROXY}', 'QQQ', {", ".join(f"'{a}'" for a in ADRS)})
    """,
    "macro": """
        SELECT instrument, obs_date, value, change_pct, change_abs
        FROM stocks.gold.global_snapshot
        WHERE instrument IN ('VIXCLS', 'DEXINUS', 'DCOILBRENTEU', 'NIKKEI225')
    """,
    "summary": "SELECT * FROM stocks.gold.nifty_after_us_moves_summary",
    "unusual_in": """
        SELECT symbol, trade_date, close, return_pct, volume_ratio
        FROM stocks.gold.unusual_volume
        WHERE market = 'IN'
          AND trade_date = (SELECT max(trade_date) FROM stocks.silver.trading_days WHERE market = 'IN')
        ORDER BY volume_ratio DESC LIMIT 15
    """,
}


# ----------------------------------------------------------- Databricks SQL --
def _db_config():
    url = config.secret("DATABRICKS_URL", "DATABRICKS_URL_PARAM").rstrip("/")
    token = config.secret("DATABRICKS_TOKEN", "DATABRICKS_TOKEN_PARAM")
    wh = config.secret("DATABRICKS_WAREHOUSE_ID", "DATABRICKS_WAREHOUSE_PARAM")
    if not (url and token and wh):
        raise RuntimeError("Databricks not configured")
    return url, token, wh


def _call(method, path, body=None):
    url, token, _ = _db_config()
    data = json.dumps(body).encode() if body is not None else None
    raw = request(method, url + path, data=data, timeout=60,
                  headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    return json.loads(raw)


def sql(statement, max_wait=240):
    """Run one statement; return rows as dicts (numbers as float)."""
    resp = _call("POST", "/api/2.0/sql/statements", {
        "warehouse_id": _db_config()[2], "statement": statement, "wait_timeout": "30s",
        "on_wait_timeout": "CONTINUE", "format": "JSON_ARRAY", "disposition": "INLINE"})
    deadline = time.monotonic() + max_wait
    while resp["status"]["state"] in ("PENDING", "RUNNING"):
        if time.monotonic() > deadline:
            raise TimeoutError("Databricks statement still running")
        time.sleep(5)
        resp = _call("GET", f"/api/2.0/sql/statements/{resp['statement_id']}")
    if resp["status"]["state"] != "SUCCEEDED":
        raise RuntimeError(f"Databricks statement {resp['status']['state']}: {resp['status'].get('error')}")
    cols = resp["manifest"]["schema"]["columns"]
    rows = []
    for raw in (resp.get("result") or {}).get("data_array") or []:
        rows.append({c["name"]: (float(v) if v is not None and c.get("type_name") in NUMERIC else v)
                     for c, v in zip(cols, raw)})
    return rows


def fetch(runner=sql):
    """All queries; a failed one becomes [] and is listed in `failed`."""
    data, failed = {}, []
    for name, statement in QUERIES.items():
        try:
            data[name] = runner(statement)
        except Exception as err:  # noqa: BLE001 - keep going, the plan degrades gracefully
            log.warning("brief query %s failed: %s", name, err)
            data[name], failed = [], failed + [name]
    return data, failed


# ------------------------------------------------------------------- plan ---
def bucket_for(ret):
    if ret is None:
        return None
    return "US up big" if ret >= BIG_MOVE_PCT else "US down big" if ret <= -BIG_MOVE_PCT else "US flat"


def risk_for_vix(vix):
    """Risk per trade (% of balance) from VIX, never above the configured default."""
    base = config.RISK_PER_TRADE_PCT
    if vix is None:
        return base
    if vix >= 30:
        return round(base * 0.5, 2)
    if vix >= 20:
        return round(base * 0.75, 2)
    return base


def build_plan(data, failed=()):
    bars = {r["symbol"]: r for r in data.get("us_bars", [])}
    macro = {r["instrument"]: r for r in data.get("macro", [])}
    spy = bars.get(US_PROXY, {}).get("return_pct")
    bucket = bucket_for(spy)
    stats = next((r for r in data.get("summary", []) if r.get("us_move") == bucket), None)
    vix = (macro.get("VIXCLS") or {}).get("value")

    avg_gap = stats.get("avg_india_gap_pct") if stats else None
    if avg_gap is None or abs(avg_gap) < 0.15:
        bias = "neutral"
    else:
        bias = "gap-up tendency" if avg_gap > 0 else "gap-down tendency"

    unusual = [
        {"symbol": r["symbol"], "volume_x": round(r["volume_ratio"], 1), "ret_pct": round(r["return_pct"] or 0, 2),
         "close": r["close"]}
        for r in data.get("unusual_in", []) if (r.get("close") or 0) >= 100
    ][:8]
    adr_moves = {nse: round(bars[adr]["return_pct"], 2) for adr, nse in ADRS.items() if adr in bars}

    return {
        "us_date": next(iter(bars.values()), {}).get("trade_date"),
        "spy_return_pct": round(spy, 2) if spy is not None else None,
        "nasdaq_return_pct": round(bars["QQQ"]["return_pct"], 2) if "QQQ" in bars else None,
        "us_bucket": bucket,
        "india_after_similar_days": None if not stats else {
            "sessions": int(stats.get("sessions") or 0),
            "avg_gap_pct": round(avg_gap, 2) if avg_gap is not None else None,
            "pct_closed_higher": stats.get("pct_india_up_days"),
            "pct_gap_same_way_as_us": stats.get("pct_gap_same_direction_as_us"),
        },
        "vix": vix,
        "usd_inr": (macro.get("DEXINUS") or {}).get("value"),
        "brent": (macro.get("DCOILBRENTEU") or {}).get("value"),
        "nikkei_change_pct": (macro.get("NIKKEI225") or {}).get("change_pct"),
        "bias": bias,
        "risk_per_trade_pct": risk_for_vix(vix),
        "unusual_volume_nse": unusual,
        "adr_moves_pct": adr_moves,
        "failed_queries": list(failed),
        "source": "stocks_lakehouse gold tables" if len(failed) < len(QUERIES) else "unavailable",
    }


def plan_message(plan, focus):
    lines = ["[MOCK] Trader plan for today"]
    if plan.get("source") == "unavailable":
        lines.append("Lakehouse data unavailable: default risk, watchlist only.")
    else:
        if plan.get("spy_return_pct") is not None:
            lines.append(f"US: S&P {plan['spy_return_pct']:+.2f}% ({plan['us_bucket']})")
        s = plan.get("india_after_similar_days")
        if s and s.get("sessions"):
            lines.append(f"India after similar days: avg gap {s['avg_gap_pct']:+.2f}% ({s['sessions']} sessions)")
        if plan.get("vix") is not None:
            lines.append(f"VIX {plan['vix']:.1f} -> risk {plan['risk_per_trade_pct']}% per trade")
        lines.append(f"Bias: {plan['bias']}")
    lines.append("Focus: " + ", ".join(focus))
    return "\n".join(lines)
