"""Long-term pool backtest: the phase 4 selection rules applied as of a past date, held to today.

Selection (same rules as stocks.gold.longterm_candidates):
  tests   capital  return on capital >= 15% (banks/lenders, i.e. no EBIT: return on assets >= 1.2%)
          balance  debt < 0.5x equity (not for banks)
          cash     operating cash flow positive in all but at most one year (not for banks)
          growth   revenue and profit growing > 10% a year
          price    P/E in the cheaper half of the companies checked
  eligible: 3+ tests available, quality score (passed / available) >= 0.6, positive P/E
  score   = 100 x quality + min(max(profit growth, 0), 30) / 3 - P/E percentile / 5
  picks   = top 10, at most 2 per industry; 90% invested in equal parts, 10% cash

"As of" a date uses only fiscal years that had ended by the 31 March before it (results are out by
June), and the price on that date. Growth is measured over the years available (2 years when only
FY23-FY25 exist). Two-quarter profit falls can't be checked historically (quarterly data is too short).
Honest limits: Yahoo's figures may include later restatements, and the universe is today's Nifty 200
(companies that dropped out since are missing), both of which flatter the result a little.
"""
import math
from collections import defaultdict
from datetime import date

import swing

DEFAULTS = {"capital": 100_000.0, "picks": 10, "per_industry": 2, "invested_pct": 90.0, "max_weight_pct": 15.0,
            "cost_pct": 0.12, "dp_charge": 16.0, "slippage_pct": 0.05}


def metrics_asof(fund_rows, fy_end):
    """fund_rows: [(symbol, metric, period_type, period_end 'YYYY-MM-DD', value)].
    Annual figures with period_end <= fy_end. Returns {symbol: metrics}."""
    annual = defaultdict(lambda: defaultdict(dict))  # symbol -> period_end -> metric -> value
    for sym, metric, ptype, pend, value in fund_rows:
        if ptype == "12M" and pend <= fy_end and value is not None:
            annual[sym][pend][metric] = value
    out = {}
    for sym, by_end in annual.items():
        ends = sorted(by_end)
        if not ends:
            continue
        last = by_end[ends[-1]]
        first = by_end[ends[0]]
        years = (date.fromisoformat(ends[-1]) - date.fromisoformat(ends[0])).days / 365.25
        fin = last.get("EBIT") is None

        def cagr(key):
            a, b = first.get(key), last.get(key)
            if years >= 1 and a and b and a > 0 and b > 0:
                return ((b / a) ** (1 / years) - 1) * 100
            return None

        ocf = [by_end[e].get("OperatingCashFlow") for e in ends[-4:] if by_end[e].get("OperatingCashFlow") is not None]
        out[sym] = {
            "fy_end": ends[-1], "is_financial": fin,
            "roce": (last["EBIT"] / last["InvestedCapital"] * 100) if not fin and last.get("InvestedCapital") else None,
            "roa": (last["NetIncome"] / last["TotalAssets"] * 100) if last.get("NetIncome") is not None and last.get("TotalAssets") else None,
            "de": (last["TotalDebt"] / last["StockholdersEquity"]) if last.get("TotalDebt") is not None and last.get("StockholdersEquity") else None,
            "ocf_pos": sum(1 for v in ocf if v > 0), "ocf_years": len(ocf),
            "rev_cagr": cagr("TotalRevenue"), "profit_cagr": cagr("NetIncome"),
            "eps": last.get("DilutedEPS"),
        }
    return out


def select(metrics, prices, industries, **overrides):
    """prices: {symbol: price on the selection date}. Returns ranked eligible companies with picks marked."""
    cfg = {**DEFAULTS, **overrides}
    rows = []
    for sym, m in metrics.items():
        price = prices.get(sym)
        pe = price / m["eps"] if price and m["eps"] and m["eps"] > 0 else None
        rows.append({"symbol": sym, **m, "price": price, "pe": pe})
    pes = sorted(r["pe"] for r in rows if r["pe"] is not None)
    for r in rows:
        if r["pe"] is None:
            r["pe_pct"] = None
        else:
            below = sum(1 for p in pes if p < r["pe"])
            r["pe_pct"] = below / (len(pes) - 1) * 100 if len(pes) > 1 else 0.0
        fin = r["is_financial"]
        tests = {
            "capital": (r["roa"] >= 1.2 if r["roa"] is not None else None) if fin else
                       (r["roce"] >= 15 if r["roce"] is not None else None),
            "balance": None if fin or r["de"] is None else r["de"] < 0.5,
            "cash": None if fin or r["ocf_years"] < 3 else r["ocf_pos"] >= r["ocf_years"] - 1,
            "growth": (r["rev_cagr"] > 10 and r["profit_cagr"] > 10)
                      if r["rev_cagr"] is not None and r["profit_cagr"] is not None else None,
            "price": None if r["pe_pct"] is None else r["pe_pct"] <= 50,
        }
        avail = [v for v in tests.values() if v is not None]
        r["tests_available"], r["tests_passed"] = len(avail), sum(avail)
        r["quality"] = r["tests_passed"] / r["tests_available"] if avail else 0.0
        r["score"] = (100 * r["quality"] + min(max(r["profit_cagr"] or 0, 0), 30) / 3
                      - (r["pe_pct"] if r["pe_pct"] is not None else 50) / 5)
        r["industry"] = industries.get(r["symbol"]) or r["symbol"]
    eligible = [r for r in rows if r["tests_available"] >= 3 and r["quality"] >= 0.6 and r["pe"]]
    eligible.sort(key=lambda r: (-r["score"], r["symbol"]))
    per_ind, picks = defaultdict(int), []
    for r in eligible:
        if len(picks) < cfg["picks"] and per_ind[r["industry"]] < cfg["per_industry"]:
            per_ind[r["industry"]] += 1
            picks.append(r)
    weight = min(cfg["invested_pct"] / len(picks), cfg["max_weight_pct"]) if picks else 0
    for r in picks:
        r["weight_pct"] = weight
    return {"eligible": eligible, "picks": picks}


def simulate(series, schedule, end, **overrides):
    """series: {symbol: adjusted bars}; schedule: [(date, picks)] - on each date sell everything not
    in the new picks, then rebalance to equal weights at that day's open. Returns curve and trades."""
    cfg = {**DEFAULTS, **overrides}
    slip = cfg["slippage_pct"] / 100
    idx = {s: {b["date"]: i for i, b in enumerate(bars)} for s, bars in series.items()}
    dates = sorted({b["date"] for bars in series.values() for b in bars if schedule[0][0] <= b["date"] <= end})
    cash, hold, curve, trades = cfg["capital"], {}, [], []
    last_close = {}
    plan = dict(schedule)

    def price(sym, d, field="close"):
        i = idx.get(sym, {}).get(d)
        return series[sym][i][field] if i is not None else None

    for d in dates:
        for s in list(hold) + [p["symbol"] for p in plan.get(d, [])]:
            c = price(s, d)
            if c:
                last_close[s] = c
        if d in plan:
            picks = plan[d]
            names = {p["symbol"] for p in picks}
            for s in list(hold):
                if s not in names:
                    px = (price(s, d, "open") or last_close[s]) * (1 - slip)
                    value = hold[s]["qty"] * px
                    cash += value - value * cfg["cost_pct"] / 100 - cfg["dp_charge"]
                    trades.append({"date": d, "symbol": s, "side": "SELL", "qty": hold[s]["qty"], "price": round(px, 2)})
                    del hold[s]
            equity = cash + sum(h["qty"] * last_close[s] for s, h in hold.items())
            for p in picks:
                s = p["symbol"]
                px = price(s, d, "open")
                if not px:
                    continue
                buy_px = px * (1 + slip)
                target = equity * p["weight_pct"] / 100
                have = hold.get(s, {}).get("qty", 0) * px
                qty = math.floor((target - have) / (buy_px * (1 + cfg["cost_pct"] / 100)))
                if qty > 0 and qty * buy_px * (1 + cfg["cost_pct"] / 100) <= cash:
                    cash -= qty * buy_px * (1 + cfg["cost_pct"] / 100)
                    h = hold.setdefault(s, {"qty": 0, "cost": 0.0})
                    h["qty"] += qty
                    h["cost"] += qty * buy_px
                    trades.append({"date": d, "symbol": s, "side": "BUY", "qty": qty, "price": round(buy_px, 2)})
        equity = cash + sum(h["qty"] * last_close.get(s, 0) for s, h in hold.items())
        curve.append((d, round(equity, 2)))
    holdings = {s: {"qty": h["qty"], "value": round(h["qty"] * last_close.get(s, 0), 2),
                    "gain_pct": round((last_close.get(s, 0) * h["qty"] / h["cost"] - 1) * 100, 1) if h["cost"] else None}
                for s, h in hold.items()}
    return {"curve": curve, "trades": trades, "holdings": holdings, "cash": round(cash, 2)}


def benchmark(series, start, end, capital=100_000.0, invested_pct=100.0, symbol="NIFTYBEES"):
    bars = [b for b in series.get(symbol, []) if start <= b["date"] <= end]
    if not bars:
        return None
    units = capital * invested_pct / 100 / bars[0]["open"]
    cash = capital * (1 - invested_pct / 100)
    curve = [(b["date"], round(cash + units * b["close"], 2)) for b in bars]
    return {"curve": curve, "return_pct": round((curve[-1][1] / capital - 1) * 100, 2),
            "max_drawdown_pct": swing.max_drawdown([v for _, v in curve])}
