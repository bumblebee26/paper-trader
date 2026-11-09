"""Ledgers for the delivery pools: swing (days to weeks) and long-term (months to years).

The intraday pool keeps its own ledger (state/ledger.json, engine.py). Each delivery pool has
state/pools/<pool>.json with its cash and holdings, and its own journal at
journal/pools/<pool>/<date>.csv. A pool never spends another pool's cash.

Costs are modelled for delivery trades: STT, exchange, stamp and GST as a percentage per side,
a depository charge per sell, and slippage. These are approximations, not a broker's contract note.

Also here: the daily portfolio snapshot across all pools, with the 12% circuit breaker.
"""
import csv
import io
import math

import config
import market
import sectors
import storage

POOLS = ("swing", "long_term")
LIMITS = {
    # max holdings, max % of the pool in one stock, max holdings per sector, max % of the pool per sector
    "swing": {"max_holdings": 5, "max_position_pct": 25.0, "max_per_sector": 2, "max_sector_pct": 100.0},
    "long_term": {"max_holdings": 12, "max_position_pct": 15.0, "max_per_sector": 99, "max_sector_pct": 25.0},
}
JOURNAL_COLUMNS = ["timestamp_ist", "pool", "event", "symbol", "qty", "price", "fees_rs", "value_rs",
                   "pnl_rs", "cash_after", "reason", "note"]
BREAKER_PCT = 12.0  # every pool stops new buys when total equity is this far below its peak


def capital(pool):
    return {"swing": config.SWING_CAPITAL, "long_term": config.LONG_TERM_CAPITAL}[pool]


def _key(pool):
    if pool not in POOLS:
        raise ValueError(f"unknown pool {pool}")
    return f"state/pools/{pool}.json"


def load(pool):
    p = storage.get_json(_key(pool))
    if not p:
        p = {"pool": pool, "capital": capital(pool), "cash": capital(pool), "holdings": {}, "realized": 0.0}
    if p["capital"] != capital(pool):  # resized in config: move cash by the same amount, keep P&L
        p["cash"] = round(p["cash"] + capital(pool) - p["capital"], 2)
        p["capital"] = capital(pool)
    return p


def save(p):
    storage.put_json(_key(p["pool"]), p)


def _journal(pool, event, **fields):
    now = market.now_ist()
    row = {c: "" for c in JOURNAL_COLUMNS}
    row.update(timestamp_ist=now.strftime("%Y-%m-%d %H:%M:%S"), pool=pool, event=event,
               **{k: v for k, v in fields.items() if k in JOURNAL_COLUMNS})
    key = f"journal/pools/{pool}/{now:%Y-%m-%d}.csv"
    existing = storage.backend().get(key) or ""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=JOURNAL_COLUMNS)
    if not existing:
        w.writeheader()
    w.writerow(row)
    storage.backend().put(key, existing + buf.getvalue())


def costs(side, value):
    """Delivery charges for one side of a trade (approximate)."""
    fees = value * config.DELIVERY_COST_PCT / 100
    if side == "SELL":
        fees += config.DP_CHARGE_RS
    return round(fees, 2)


def holdings_value(p, prices=None):
    total = 0.0
    for sym, h in p["holdings"].items():
        price = (prices or {}).get(sym, h.get("last_price") or h["avg_cost"])
        total += h["qty"] * price
    return round(total, 2)


def equity(p, prices=None):
    return round(p["cash"] + holdings_value(p, prices), 2)


def buy_check(p, symbol, qty, price, other_pools=()):
    """None if the buy fits the pool's limits, else the reason in plain words."""
    lim = LIMITS[p["pool"]]
    value = qty * price * (1 + config.SLIPPAGE_PCT / 100)
    eq = equity(p)
    if qty <= 0:
        return "quantity must be at least 1"
    if value + costs("BUY", value) > p["cash"]:
        return f"not enough cash in the {p['pool']} pool (Rs {p['cash']:,.0f} free)"
    held = p["holdings"].get(symbol)
    if not held and len(p["holdings"]) >= lim["max_holdings"]:
        return f"already {lim['max_holdings']} holdings, the pool's maximum"
    after = value + (held["qty"] * held["avg_cost"] if held else 0)
    if after > eq * lim["max_position_pct"] / 100:
        return f"{symbol} would be over {lim['max_position_pct']:g}% of the pool"
    sec = sectors.sector(symbol)
    same = [s for s in p["holdings"] if sectors.sector(s) == sec and s != symbol]
    if len(same) >= lim["max_per_sector"]:
        return f"already {len(same)} {sec} holdings ({', '.join(same)})"
    sec_value = sum(p["holdings"][s]["qty"] * p["holdings"][s]["avg_cost"] for s in same) + after
    if sec_value > eq * lim["max_sector_pct"] / 100:
        return f"{sec} would be over {lim['max_sector_pct']:g}% of the pool"
    combined = after + sum(o["holdings"].get(symbol, {}).get("qty", 0) * o["holdings"].get(symbol, {}).get("avg_cost", 0)
                           for o in other_pools)
    total_eq = eq + sum(equity(o) for o in other_pools)
    if other_pools and combined > total_eq * 15 / 100:
        return f"{symbol} would be over 15% of the swing and long-term pools combined"
    if breaker_on():
        return "portfolio circuit breaker is on (total value 12% below its peak); send /resume all after reviewing"
    return None


def buy(p, symbol, qty, price, reason="", **meta):
    why = buy_check(p, symbol, qty, price)
    if why:
        return {"status": "blocked", "reason": why}
    fill = round(price * (1 + config.SLIPPAGE_PCT / 100), 2)
    value = round(qty * fill, 2)
    fees = costs("BUY", value)
    p["cash"] = round(p["cash"] - value - fees, 2)
    h = p["holdings"].get(symbol)
    if h:
        total_qty = h["qty"] + qty
        h["avg_cost"] = round((h["avg_cost"] * h["qty"] + value + fees) / total_qty, 4)
        h["qty"] = total_qty
    else:
        p["holdings"][symbol] = {"qty": qty, "avg_cost": round((value + fees) / qty, 4),
                                 "opened": market.now_ist().strftime("%Y-%m-%d"), **meta}
    p["holdings"][symbol]["last_price"] = fill  # valued at the market, not at cost incl. fees
    _journal(p["pool"], "bought", symbol=symbol, qty=qty, price=fill, fees_rs=fees, value_rs=value,
             cash_after=p["cash"], reason=reason)
    return {"status": "bought", "symbol": symbol, "qty": qty, "fill": fill, "fees_rs": fees}


def sell(p, symbol, qty=None, price=None, reason=""):
    h = p["holdings"].get(symbol)
    if not h:
        return {"status": "blocked", "reason": f"no {symbol} holding in the {p['pool']} pool"}
    qty = min(qty or h["qty"], h["qty"])
    fill = round(price * (1 - config.SLIPPAGE_PCT / 100), 2)
    value = round(qty * fill, 2)
    fees = costs("SELL", value)
    pnl = round(value - fees - qty * h["avg_cost"], 2)
    p["cash"] = round(p["cash"] + value - fees, 2)
    p["realized"] = round(p.get("realized", 0.0) + pnl, 2)
    h["qty"] -= qty
    if h["qty"] == 0:
        del p["holdings"][symbol]
    _journal(p["pool"], "sold", symbol=symbol, qty=qty, price=fill, fees_rs=fees, value_rs=value, pnl_rs=pnl,
             cash_after=p["cash"], reason=reason)
    return {"status": "sold", "symbol": symbol, "qty": qty, "fill": fill, "pnl_rs": pnl}


# ------------------------------------------------------------ portfolio --
PORTFOLIO_KEY = "state/portfolio.json"


def breaker_on():
    return bool((storage.get_json(PORTFOLIO_KEY) or {}).get("breaker"))


def snapshot(intraday_state, prices=None):
    """Record today's total equity across pools; trip the circuit breaker 12% below the peak.
    Returns the snapshot. Called once a day after the close (guardian report)."""
    pools = {name: load(name) for name in POOLS}
    values = {"intraday": round(intraday_state["balance"], 2),
              **{name: equity(p, prices) for name, p in pools.items()}}
    total = round(sum(values.values()), 2)
    port = storage.get_json(PORTFOLIO_KEY) or {"peak": total, "history": [], "breaker": False}
    port["peak"] = max(port.get("peak", total), total)
    drawdown = round((port["peak"] - total) / port["peak"] * 100, 2) if port["peak"] else 0.0
    tripped = drawdown >= BREAKER_PCT and not port.get("breaker")
    if drawdown >= BREAKER_PCT:
        port["breaker"] = True
    day = market.now_ist().strftime("%Y-%m-%d")
    port["history"] = [h for h in port.get("history", []) if h["date"] != day][-400:]
    port["history"].append({"date": day, "total": total, **values, "drawdown_pct": drawdown})
    storage.put_json(PORTFOLIO_KEY, port)
    return {"date": day, "total": total, "values": values, "peak": port["peak"], "drawdown_pct": drawdown,
            "breaker": port["breaker"], "tripped_now": tripped}


def reset_breaker():
    port = storage.get_json(PORTFOLIO_KEY) or {}
    if port.get("breaker"):
        port["breaker"] = False
        storage.put_json(PORTFOLIO_KEY, port)
        return True
    return False


def summary_lines(intraday_state):
    lines = []
    for name, label, phase in (("swing", "Swing", "phase 3, planned 7 Dec"),
                               ("long_term", "Long-term", "phase 4, planned 21 Dec")):
        p = load(name)
        eq = equity(p)
        state = (f"{len(p['holdings'])} holdings, cash Rs {p['cash']:,.0f}" if p["holdings"]
                 else f"not trading yet ({phase})")
        lines.append((label, eq, p["capital"], state))
    return lines
