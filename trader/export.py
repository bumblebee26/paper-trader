"""Daily backup feed to the stocks_lakehouse: the day's journal as JSON lines.

  S3:          exports/date=YYYY-MM-DD/events.jsonl
  Databricks:  /Volumes/stocks/bronze/landing/trades/date=YYYY-MM-DD/events.jsonl

One file per day, overwritten on a re-run (the lakehouse keeps the newest copy of each
event_id). The last line is a ledger snapshot (balance and realized P&L at export time),
so the lakehouse can reconcile its own P&L against the S3 ledger.

Pushed once a day (15:45 IST, guardian Lambda) so the lakehouse pipeline isn't woken
up every few minutes during market hours.
"""
import hashlib
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
    context = ""
    if state.get("day") == day:
        bal, realized, start = state["balance"], state.get("realized_today", 0.0), state["starting_capital"]
        when = f"{day} 23:59:59"
        context = _context(state)
    else:
        rep = storage.get_json(f"reports/{day}.json")
        if not rep:
            return None
        bal, realized = rep["balance_rs"], rep["realized_today_rs"]
        start = rep["balance_rs"] - rep["total_pnl_since_start_rs"]
        when = f"{day} 23:59:59"
    return {"event_id": f"snapshot-{day}", "timestamp_ist": when, "event": "ledger_snapshot",
            "balance": round(bal, 2), "starting_capital": round(start, 2),
            "realized_today": round(realized, 2), "note": "ledger at export time" + context}


def _context(state):
    """The day's settings for the dashboard's header (k=v pairs after the note): auto mode, India VIX from the
    morning plan, and the risk per trade."""
    plan = state.get("plan") or {}
    vix = plan.get("india_vix", plan.get("vix"))
    pairs = {"auto_mode": "on" if state.get("auto_mode", config.AUTO_MODE_DEFAULT) else "off",
             "vix": f"{float(vix):.1f}" if vix is not None else None,
             "risk_pct": f"{float(state.get('day_risk_pct', config.RISK_PER_TRADE_PCT)):g}"}
    return "".join(f";{k}={v}" for k, v in pairs.items() if v is not None)


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


# ---------------------------------------------------------------- portfolio --
# One file per day with every pool and every holding, so the lakehouse dashboard can show
# current holdings, allocation and the value of each pool over time.
#   S3:          exports/portfolio/date=YYYY-MM-DD/portfolio.jsonl
#   Databricks:  /Volumes/stocks/bronze/landing/portfolio/date=YYYY-MM-DD/portfolio.jsonl
def build_portfolio(day):
    import pools
    import sectors

    rows = []
    state = storage.get_json(storage.LEDGER_KEY) or {}
    if state:
        bal = float(state.get("balance", 0.0))
        rows.append({"record_type": "pool", "snapshot_date": day, "pool": "intraday",
                     "capital": float(state.get("starting_capital", config.STARTING_CAPITAL)),
                     "cash": bal, "holdings_value": 0.0, "equity": bal})
        for sym, pos in (state.get("positions") or {}).items():
            rows.append({"record_type": "holding", "snapshot_date": day, "pool": "intraday", "symbol": sym,
                         "sector": sectors.sector(sym), "qty": float(pos.get("qty", 0)),
                         "avg_cost": float(pos.get("fill_price", 0)), "last_price": float(pos.get("fill_price", 0)),
                         "value": float(pos.get("qty", 0)) * float(pos.get("fill_price", 0)), "pnl": 0.0,
                         "opened": day})
    for name in pools.POOLS:
        p = pools.refresh_prices(pools.load(name))
        pools.save(p)
        hv = pools.holdings_value(p)
        rows.append({"record_type": "pool", "snapshot_date": day, "pool": name, "capital": float(p["capital"]),
                     "cash": round(p["cash"], 2), "holdings_value": hv, "equity": round(p["cash"] + hv, 2)})
        for sym, h in p["holdings"].items():
            last = float(h.get("last_price") or h["avg_cost"])
            value = round(h["qty"] * last, 2)
            cost = round(h["qty"] * h["avg_cost"], 2)
            rows.append({"record_type": "holding", "snapshot_date": day, "pool": name, "symbol": sym,
                         "sector": "Index fund" if sym in pools.INDEX_FUNDS else sectors.sector(sym),
                         "qty": float(h["qty"]), "avg_cost": round(h["avg_cost"], 4), "last_price": last,
                         "value": value, "pnl": round(value - cost, 2), "opened": h.get("opened")})
    # Shadow pools (rules tested on paper): their own record types, so they never mix into the portfolio totals
    for name in pools.SHADOW_POOLS:
        p = pools.load(name)
        if not (p.get("meta") or {}).get("start_date") and not p["holdings"]:
            continue
        p = pools.refresh_prices(p)
        pools.save(p)
        hv = pools.holdings_value(p)
        rows.append({"record_type": "shadow_pool", "snapshot_date": day, "pool": name, "capital": float(p["capital"]),
                     "cash": round(p["cash"], 2), "holdings_value": hv, "equity": round(p["cash"] + hv, 2)})
        for sym, h in p["holdings"].items():
            last = float(h.get("last_price") or h["avg_cost"])
            rows.append({"record_type": "shadow_holding", "snapshot_date": day, "pool": name, "symbol": sym,
                         "sector": sectors.sector(sym), "qty": float(h["qty"]), "avg_cost": round(h["avg_cost"], 4),
                         "last_price": last, "value": round(h["qty"] * last, 2),
                         "pnl": round(h["qty"] * (last - h["avg_cost"]), 2), "opened": h.get("opened")})
    return "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows)


def export_portfolio(day, push=True):
    body = build_portfolio(day)
    storage.backend().put(f"exports/portfolio/date={day}/portfolio.jsonl", body)
    result = {"date": day, "portfolio_rows": body.count("\n")}
    if push:
        result["portfolio_path"] = _push(f"portfolio/date={day}/portfolio.jsonl", body)
    return result


# ------------------------------------------------------------- pool trades --
# Every buy and sell in the delivery pools (real swing and long-term, and every shadow pool), from their own journals
# (journal/pools/<pool>/<date>.csv), so the lakehouse can show the trades placed and their P&L.
#   S3:          exports/pool_trades/date=YYYY-MM-DD/trades.jsonl
#   Databricks:  /Volumes/stocks/bronze/landing/pool_trades/date=YYYY-MM-DD/trades.jsonl
POOL_TRADE_NUMERIC = ("qty", "price", "fees_rs", "value_rs", "pnl_rs", "cash_after")


def _pool_trade(row, pool, kind):
    out = {"pool": pool, "kind": kind}
    for f in ("timestamp_ist", "event", "symbol", "reason", "note"):
        out[f] = row.get(f) or None
    for f in POOL_TRADE_NUMERIC:
        v = row.get(f)
        try:
            out[f] = float(v) if v not in (None, "") else None
        except ValueError:
            out[f] = None
    key = "|".join(str(out[k]) for k in ("pool", "timestamp_ist", "event", "symbol", "qty", "price"))
    out["trade_id"] = hashlib.sha1(key.encode()).hexdigest()[:20]
    return out


def build_pool_trades(day):
    """JSONL of the day's pool buys and sells (every pool), or None if no pool traded that day."""
    import pools

    rows = []
    for names, kind in ((pools.POOLS, "real"), (pools.SHADOW_POOLS, "shadow")):
        for name in names:
            for r in storage.read_rows(f"journal/pools/{name}/{day}.csv"):
                if r.get("event") in ("bought", "sold"):
                    rows.append(_pool_trade(r, name, kind))
    if not rows:
        return None
    return "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows)


def export_pool_trades(day, push=True):
    body = build_pool_trades(day)
    if body is None:
        return {"date": day, "pool_trades": 0}
    storage.backend().put(f"exports/pool_trades/date={day}/trades.jsonl", body)
    result = {"date": day, "pool_trades": body.count("\n")}
    if push:
        result["pool_trades_path"] = _push(f"pool_trades/date={day}/trades.jsonl", body)
    return result


POOL_TRADES_FROM = "2026-11-18"  # the ledger reset: the first run sends everything since then, once
_BACKFILL_MARK = "exports/pool_trades/backfilled"


def pool_trades_start(start):
    """The usual start (the last few days), or the ledger reset the first time pool trades are exported."""
    if storage.backend().get(_BACKFILL_MARK):
        return start
    storage.backend().put(_BACKFILL_MARK, POOL_TRADES_FROM)
    return min(start, POOL_TRADES_FROM)


def export_pool_trades_range(start, end, push=True):
    """Every day from start to end, weekends included (a pool can be parked or rebalanced on any run)."""
    d, last, out = date.fromisoformat(start), date.fromisoformat(end), []
    while d <= last:
        out.append(export_pool_trades(d.isoformat(), push=push))
        d += timedelta(days=1)
    return [r for r in out if r["pool_trades"]]
