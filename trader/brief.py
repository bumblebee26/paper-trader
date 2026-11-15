"""Pre-market context: the lakehouse's gold tables plus a few live figures, India first.

The trader uses it as the base for the day:
  - risk per trade from India VIX (1%; 0.75% at 20+; 0.5% at 30+), enforced in code by the risk gate
  - the bias from India itself: the Nifty 50's trend (NIFTYBEES vs its 20/50/200-day averages, from the
    technical scanner); the US only in a supporting role, plus a "global shock" when the S&P 500 moved 2%+
  - the technical scanner's strict picks and near-misses, NSE unusual volume and the news radar seed the focus
    list; the five Indian ADRs are evidence for those five stocks only

Freshness rule: every source must have the session it should have (the market's own calendar: the dates of
Yahoo's Nifty 50 and S&P 500 daily candles). A source missing a day that wasn't a holiday is left out and named
in `missing`; an older day is never used in its place.

Databricks access reuses the lakehouse SSM parameters (/stocks/databricks-*). If they aren't configured or a
query fails, the day still runs with the default plan.
"""
import json
import time
from datetime import date, timedelta

import config
from http_client import request, log

NUMERIC = {"BYTE", "SHORT", "INT", "LONG", "FLOAT", "DOUBLE", "DECIMAL"}
ADRS = {"INFY": "INFY", "WIT": "WIPRO", "HDB": "HDFCBANK", "IBN": "ICICIBANK", "RDY": "DRREDDY"}

SHOCK_PCT = 2.0
LIVE = {"^INDIAVIX": "india_vix", "^GSPC": "sp500", "^IXIC": "nasdaq", "BZ=F": "brent", "INR=X": "usd_inr",
        **{a: a for a in ADRS}}
CALENDARS = {"NSE": "^NSEI", "US": "^GSPC"}
FRESHNESS = {"nse_prices": "NSE", "technical_scanner": "NSE"}

QUERIES = {
    "freshness": "SELECT source, CAST(latest_date AS STRING) AS latest_date FROM stocks.gold.data_freshness",
    "summary": "SELECT * FROM stocks.gold.nifty_after_us_moves_summary",
    "radar": """
        SELECT symbol, company, industry, close, return_pct, volume_ratio, avg_turnover_cr, reasons,
               latest_news_category, latest_news_title, CAST(trade_date AS STRING) AS trade_date
        FROM stocks.gold.intraday_universe
        WHERE flagged
        ORDER BY news_count DESC, abs(return_pct) DESC LIMIT 12
    """,
    "universe": "SELECT symbol, company FROM stocks.gold.intraday_universe WHERE eligible",
    "unusual_in": """
        SELECT symbol, CAST(trade_date AS STRING) AS trade_date, close, return_pct, volume_ratio
        FROM stocks.gold.unusual_volume
        WHERE market = 'IN'
          AND trade_date = (SELECT max(trade_date) FROM stocks.silver.trading_days WHERE market = 'IN')
        ORDER BY volume_ratio DESC LIMIT 15
    """,
    "scanner": """
        SELECT symbol, CAST(trade_date AS STRING) AS trade_date, list_type, rank, close, setup, stance, rsi14,
               adx14, vol_ratio, deliv_per, fno, entry_low, entry_high, trigger, stop, target1, target2, rr, misses,
               regime_close, regime_rsi, regime_sma20, regime_sma50, regime_sma200
        FROM stocks.gold.technical_screen
        WHERE list_type IN ('strict', 'near-miss')
        ORDER BY list_type DESC, rank
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


def _previous_weekday(d):
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def fetch_live(today=None, daily=None):
    """Yahoo: each market's last completed session (the calendar) and the live figures, each with its date.
    Returns ({"NSE": date, "US": date}, {key: {"date", "close", "change_pct"}}). Anything unavailable is left out."""
    import market

    daily = daily or market.daily_raw
    today = today or market.now_ist().date()
    cut = today.isoformat()

    def last_two(sym):
        bars = [b for b in daily(sym, "1mo") if b["time"][:10] < cut]
        return bars[-2:] if len(bars) >= 2 else bars

    sessions, live = {}, {}
    for mkt, sym in CALENDARS.items():
        try:
            bars = last_two(sym)
            sessions[mkt] = bars[-1]["time"][:10] if bars else None
        except Exception as err:  # noqa: BLE001
            log.warning("calendar %s unavailable: %s", sym, err)
            sessions[mkt] = None
    for sym, key in LIVE.items():
        try:
            bars = last_two(sym)
        except Exception as err:  # noqa: BLE001
            log.warning("live %s unavailable: %s", sym, err)
            continue
        if len(bars) == 2 and bars[0]["close"]:
            live[key] = {"date": bars[1]["time"][:10], "close": bars[1]["close"],
                         "change_pct": round((bars[1]["close"] / bars[0]["close"] - 1) * 100, 2)}
    return sessions, live


def expected_sessions(today, sessions):
    """{"NSE": (date, holidays, from_calendar), "US": ...}: the session each source should have."""
    out = {}
    for mkt in CALENDARS:
        iso = (sessions or {}).get(mkt)
        if iso:
            exp = date.fromisoformat(iso)
            hols, d = [], _previous_weekday(today)
            while d > exp:
                hols.append(d.isoformat())
                d = _previous_weekday(d)
            out[mkt] = (exp, sorted(hols), True)
        else:
            out[mkt] = (_previous_weekday(today), [], False)
    return out


# ------------------------------------------------------------------- plan ---
def risk_for_vix(vix):
    """Risk per trade (% of balance) from India VIX, never above the configured default."""
    base = config.RISK_PER_TRADE_PCT
    if vix is None:
        return base
    if vix >= 30:
        return round(base * 0.5, 2)
    if vix >= 20:
        return round(base * 0.75, 2)
    return base


def _regime(row):
    """NIFTYBEES vs its 20/50/200-day averages -> ("uptrend" | "downtrend" | "mixed", words)."""
    if not row or row.get("regime_close") is None:
        return None, None
    c = row["regime_close"]
    above = [n for n, k in (("20", "regime_sma20"), ("50", "regime_sma50"), ("200", "regime_sma200"))
             if row.get(k) is not None and c > row[k]]
    if len(above) == 3:
        return "uptrend", "Nifty 50 above its 20/50/200-day averages"
    if not above:
        return "downtrend", "Nifty 50 below its 20/50/200-day averages"
    return "mixed", f"Nifty 50 above only its {'/'.join(above)}-day average(s)"


def _pick(r):
    keep = ("symbol", "close", "setup", "stance", "rsi14", "adx14", "vol_ratio", "deliv_per", "fno", "entry_low",
            "entry_high", "trigger", "stop", "target1", "target2", "rr", "misses")
    return {k: (round(r[k], 2) if isinstance(r.get(k), float) else r.get(k)) for k in keep}


def build_plan(data, failed=(), live=None, sessions=None, today=None):
    """The day's plan. live/sessions come from fetch_live(); without them (tests, Yahoo down) the plan uses the
    lakehouse alone and says which live figures are missing."""
    today = today or date.today()
    live = live or {}
    exp = expected_sessions(today, sessions or {})
    nse_day, us_day = exp["NSE"][0].isoformat(), exp["US"][0].isoformat()
    latest = {r["source"]: (r.get("latest_date") or "")[:10] for r in data.get("freshness", [])}
    have_freshness = "freshness" not in failed and bool(latest)

    def fresh_source(src):
        # without the freshness table (older lakehouse, or the query failed) fall back to the rows' own dates
        return latest.get(src, "") >= nse_day if have_freshness else None

    def fresh_rows(rows, src):
        ok = fresh_source(src)
        if ok is None:
            return [r for r in rows if (r.get("trade_date") or nse_day)[:10] >= nse_day]
        return rows if ok else []

    def live_fresh(key, day):
        r = live.get(key)
        return r if r and r["date"] >= day else None

    missing = []
    for src, label in (("nse_prices", "NSE prices"), ("technical_scanner", "technical scanner")):
        if fresh_source(src) is False:
            missing.append(f"{label} for {nse_day} (latest {latest.get(src) or 'none'})")

    # risk: India VIX (the Indian market's own fear gauge)
    vix_row = live_fresh("india_vix", nse_day)
    vix = vix_row["close"] if vix_row else None
    if not vix_row:
        missing.append("India VIX")

    # global, supporting only
    spx, ndx = live_fresh("sp500", us_day), live_fresh("nasdaq", us_day)
    for key, label in (("sp500", "S&P 500"), ("nasdaq", "Nasdaq"), ("brent", "Brent"), ("usd_inr", "USD/INR")):
        if not live_fresh(key, us_day):
            missing.append(label)
    spy = spx["change_pct"] if spx else None
    shock = None
    if spy is not None and abs(spy) >= SHOCK_PCT:
        shock = "down" if spy < 0 else "up"
    stats = next((r for r in data.get("summary", []) if r.get("us_move") == f"US {shock} big"), None) if shock else None

    # India: the Nifty 50's own trend, from the scanner
    scan = fresh_rows(data.get("scanner", []), "technical_scanner")
    regime, regime_words = _regime(scan[0] if scan else None)
    bias_side = {"uptrend": "BUY", "downtrend": "SELL"}.get(regime)
    bias = regime or "neutral"
    if shock:
        bias += f", global shock {shock}"
        bias_side = bias_side if bias_side == ("SELL" if shock == "down" else "BUY") else None

    universe = sorted({r["symbol"] for r in data.get("universe", [])})
    unusual = [
        {"symbol": r["symbol"], "volume_x": round(r["volume_ratio"], 1), "ret_pct": round(r["return_pct"] or 0, 2),
         "close": r["close"]}
        for r in fresh_rows(data.get("unusual_in", []), "nse_prices")
        if (r.get("close") or 0) >= 100 and (not universe or r["symbol"] in universe)  # tradable names only
    ][:8]
    radar = [{"symbol": r["symbol"], "company": r.get("company"), "industry": r.get("industry"),
              "close": r.get("close"), "ret_pct": r.get("return_pct"), "volume_x": r.get("volume_ratio"),
              "why": r.get("reasons"), "news": (r.get("latest_news_title") or "")[:160] or None}
             for r in fresh_rows(data.get("radar", []), "nse_prices")]
    adr_moves = {nse: live[adr]["change_pct"] for adr, nse in ADRS.items() if live_fresh(adr, us_day)}
    strict = [_pick(r) for r in scan if r.get("list_type") == "strict"][:10]
    near = [_pick(r) for r in scan if r.get("list_type") == "near-miss"][:10]

    return {
        "nse_session": nse_day, "us_session": us_day,
        "holidays": {m: e[1] for m, e in exp.items() if e[1]},
        "missing": missing,
        "india_vix": vix,
        "india_vix_change_pct": vix_row["change_pct"] if vix_row else None,
        "vix": vix,   # the risk gate and the advisor's mood read this key
        "risk_per_trade_pct": risk_for_vix(vix),
        "nifty_regime": regime, "nifty_regime_words": regime_words,
        "bias": bias, "bias_side": bias_side,
        "global_supporting": {
            "us_date": us_day if spx else None,
            "sp500_pct": spy, "nasdaq_pct": ndx["change_pct"] if ndx else None,
            "brent": (live_fresh("brent", us_day) or {}).get("close"),
            "usd_inr": (live_fresh("usd_inr", us_day) or {}).get("close"),
            "shock": shock,
            "india_after_similar_shocks": None if not stats else {
                "sessions": int(stats.get("sessions") or 0),
                "avg_gap_pct": round(stats["avg_india_gap_pct"], 2) if stats.get("avg_india_gap_pct") is not None else None},
            "note": "supporting only: not used for the bias unless it is a 2%+ shock",
        },
        "scanner": {"date": nse_day if scan else None, "strict": strict, "near_miss": near,
                    "note": "indicative levels from swing points and ATR; never a guaranteed return"},
        "unusual_volume_nse": unusual,
        "news_radar": radar,
        "tradable_universe": universe,
        "tradable_names": {r["symbol"]: r.get("company") for r in data.get("universe", []) if r.get("company")},
        "adr_moves_pct": adr_moves,
        "failed_queries": list(failed),
        "source": "stocks_lakehouse gold tables" if len(failed) < len(QUERIES) else "unavailable",
    }


def scanner_status(plan, symbol):
    """'strict' / 'near' / 'none' for this stock on the morning's scanner ('none' also when it didn't run)."""
    sc = (plan or {}).get("scanner") or {}
    if any(p.get("symbol") == symbol for p in sc.get("strict") or []):
        return "strict"
    if any(p.get("symbol") == symbol for p in sc.get("near_miss") or []):
        return "near"
    return "none"


def plan_message(plan, focus):
    lines = ["[MOCK] Trader plan for today"]
    if plan.get("source") == "unavailable":
        lines.append("Lakehouse data unavailable: default risk, watchlist only.")
    vix = plan.get("india_vix", plan.get("vix"))
    if vix is not None:
        lines.append(f"India VIX {vix:.1f} -> risk {plan['risk_per_trade_pct']}% per trade")
    elif plan.get("risk_per_trade_pct") is not None:
        lines.append(f"India VIX not available -> default risk {plan['risk_per_trade_pct']}% per trade")
    if plan.get("nifty_regime_words"):
        lines.append(f"India: {plan['nifty_regime_words']} ({plan['nifty_regime']})")
    sc = plan.get("scanner") or {}
    if sc.get("date"):
        names = ", ".join(p["symbol"] for p in sc.get("strict") or []) or "none today"
        lines.append(f"Scanner strict picks: {names}")
    g = plan.get("global_supporting") or {}
    if g.get("sp500_pct") is not None:
        lines.append(f"Global (supporting): S&P {g['sp500_pct']:+.2f}%"
                     + (f" - SHOCK {g['shock']}" if g.get("shock") else ""))
    lines.append(f"Bias: {plan.get('bias', 'neutral')}")
    if plan.get("missing"):
        lines.append("Left out (not fresh): " + "; ".join(plan["missing"]))
    for mkt, days in (plan.get("holidays") or {}).items():
        lines.append(f"{mkt} holiday: {', '.join(days)}")
    lines.append("Focus: " + ", ".join(focus))
    return "\n".join(lines)
