"""Run the long-term pool backtest on lakehouse data and report in plain words.

  python backtest/run_longterm.py [--no-send] [--universe=traded]

--universe=traded picks from the 250 most traded NSE stocks of Apr-Jun 2025 (what you could have
known on 1 Jul 2025) instead of today's Nifty 200 list, which favours companies that joined the
index because their price already rose. Industries come from today's list where known.

Scenario A: pick 10 companies on 1 Jul 2025 (FY25 figures, published by then), hold to today.
Scenario B: the same, then re-pick on 1 Jul 2026 with FY26 figures (sell what dropped out).
Both against NIFTYBEES over the same days (fully invested, and 90% invested like the pool).
Saves backtests/longterm/<date>.json in the state bucket; sends a Telegram summary.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "trader"), HERE]

import longterm  # noqa: E402
import market  # noqa: E402
import run_swing  # noqa: E402
import storage  # noqa: E402
import swing  # noqa: E402

FUND_SQL = ("SELECT symbol, metric, period_type, CAST(period_end AS STRING) AS period_end, value "
            "FROM stocks.silver.fundamentals")
MEMBERS_SQL = "SELECT symbol, industry FROM stocks.silver.index_members"
BARS_SQL = """
SELECT symbol, CAST(trade_date AS STRING) AS d, open, high, low, close, prev_close, volume
FROM stocks.silver.nse_eq_daily
WHERE series = 'EQ' AND trade_date >= '2025-05-01' AND symbol IN ({symbols})
ORDER BY symbol, trade_date
"""
SELECTIONS = [("2025-07-01", "2025-03-31"), ("2026-07-01", "2026-03-31")]
TRADED_SQL = """
SELECT symbol, percentile_approx(turnover, 0.5) AS median_turnover, count(*) AS sessions
FROM stocks.silver.nse_eq_daily
WHERE series = 'EQ' AND trade_date BETWEEN '2025-04-01' AND '2025-06-30'
GROUP BY symbol HAVING sessions >= 40 ORDER BY median_turnover DESC LIMIT 250
"""


def load(universe="nifty200"):
    f = lambda v: float(v) if v not in (None, "") else None  # noqa: E731
    fund = [(r["symbol"], r["metric"], r["period_type"], r["period_end"], f(r["value"])) for r in run_swing.sql_all(FUND_SQL)]
    members = {r["symbol"]: r["industry"] for r in run_swing.sql_all(MEMBERS_SQL)}
    coverage = None
    if universe == "traded":
        traded = [r["symbol"] for r in run_swing.sql_all(TRADED_SQL) if not r["symbol"].endswith("BEES")]
        have = {r[0] for r in fund}
        coverage = {"universe": len(traded), "with_figures": sum(1 for s in traded if s in have)}
        fund = [r for r in fund if r[0] in set(traded)]
        industries = {s: members.get(s) for s in traded}
        symbols = sorted(set(traded)) + ["NIFTYBEES"]
        quoted = ",".join("'" + s.replace("'", "") + "'" for s in symbols)
        data = {}
        for r in run_swing.sql_all(BARS_SQL.format(symbols=quoted)):
            data.setdefault(r["symbol"], []).append(
                (r["d"], f(r["open"]), f(r["high"]), f(r["low"]), f(r["close"]), f(r["prev_close"]), f(r["volume"])))
        return fund, industries, {s: swing.adjust(rows) for s, rows in data.items()}, coverage
    symbols = sorted(set(members) or {r[0] for r in fund}) + ["NIFTYBEES"]
    quoted = ",".join("'" + s.replace("'", "") + "'" for s in symbols)
    data = {}
    for r in run_swing.sql_all(BARS_SQL.format(symbols=quoted)):
        data.setdefault(r["symbol"], []).append(
            (r["d"], f(r["open"]), f(r["high"]), f(r["low"]), f(r["close"]), f(r["prev_close"]), f(r["volume"])))
    series = {s: swing.adjust(rows) for s, rows in data.items()}
    if members:
        fund = [r for r in fund if r[0] in members]
    return fund, members, series, coverage


def first_trading_day(series, on_or_after):
    return min((b["date"] for b in series.get("NIFTYBEES", []) if b["date"] >= on_or_after), default=None)


def close_before(series, sym, d):
    bars = [b for b in series.get(sym, []) if b["date"] < d]
    return bars[-1]["close"] if bars else None


def pick(fund, members, series, sel_date, fy_end):
    day = first_trading_day(series, sel_date)
    prices = {s: close_before(series, s, day) for s in series}
    out = longterm.select(longterm.metrics_asof(fund, fy_end), prices, members)
    return day, out


def run(fund, members, series):
    end = max(b["date"] for b in series["NIFTYBEES"])
    picks = [pick(fund, members, series, sd, fy) for sd, fy in SELECTIONS]
    a = longterm.simulate(series, [(picks[0][0], picks[0][1]["picks"])], end)
    b = longterm.simulate(series, [(d, p["picks"]) for d, p in picks if d], end)
    start = picks[0][0]
    out = {"start": start, "end": end, "selections": []}
    for d, p in picks:
        out["selections"].append({"date": d, "eligible": len(p["eligible"]),
                                  "picks": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()
                                             if k in ("symbol", "industry", "score", "quality", "pe", "roce", "roa",
                                                      "profit_cagr", "weight_pct")} for r in p["picks"]]})
    for name, sim in (("hold", a), ("rebalance", b)):
        vals = [v for _, v in sim["curve"]]
        out[name] = {"return_pct": round((vals[-1] / longterm.DEFAULTS["capital"] - 1) * 100, 2),
                     "max_drawdown_pct": swing.max_drawdown(vals), "holdings": sim["holdings"], "cash": sim["cash"],
                     "trades": len(sim["trades"])}
    out["nifty"] = longterm.benchmark(series, start, end)
    out["nifty90"] = longterm.benchmark(series, start, end, invested_pct=90.0)
    for k in ("nifty", "nifty90"):
        if out[k]:
            out[k].pop("curve")
    return out


def message(r):
    import engine

    h, rb, n, n90 = r["hold"], r["rebalance"], r["nifty"] or {}, r["nifty90"] or {}
    first = r["selections"][0]
    cov = r.get("coverage")
    who = ("the 250 most traded NSE stocks of Apr-Jun 2025 (no hindsight)" if cov else "today's Nifty 200 companies")
    lines = [f"[MOCK] Long-term pool backtest: Rs 1,00,000 from {first['date']} to {r['end']}",
             f"Rules: {who} passing the quality tests, top 10 (max 2 per industry), 90% invested in "
             f"equal parts, costs included. {first['eligible']} companies qualified on {first['date']}."]
    if cov:
        lines.append(f"Company figures were available for {cov['with_figures']} of those {cov['universe']} stocks.")
    lines += ["",
             f"- Buy and hold the 10 picks: {h['return_pct']:+.1f}% ({engine.inr(1e5 * h['return_pct'] / 100, sign=True)}), "
             f"deepest fall {h['max_drawdown_pct']}%",
             f"- Re-pick on {r['selections'][1]['date']} with FY26 figures: {rb['return_pct']:+.1f}%, deepest fall "
             f"{rb['max_drawdown_pct']}%",
             f"- Just holding NIFTYBEES: {n.get('return_pct', 'n/a')}% (90% invested like the pool: "
             f"{n90.get('return_pct', 'n/a')}%), deepest fall {n.get('max_drawdown_pct', 'n/a')}%", "",
             "Picks on " + first["date"] + ": " + ", ".join(p["symbol"] for p in first["picks"]), ""]
    best = max(h["return_pct"], rb["return_pct"])
    bench = n90.get("return_pct")
    lines.append("In plain words:")
    if bench is not None and best > bench:
        lines.append(f"- The quality picks beat holding the index by {best - bench:.1f} points over these months: "
                     "a good sign, worth the shadow weeks.")
    else:
        lines.append("- The quality picks did NOT beat holding the index over these months. Worth rethinking the rules "
                     "before any buying.")
    lines.append("- One stretch of about 16 months, today's Nifty 200 list and possibly restated figures: these "
                 "flatter the result a little, so treat it as a sanity check, not proof.")
    return "\n".join(lines)


def main(argv):
    t0 = time.monotonic()
    universe = "traded" if "--universe=traded" in argv else "nifty200"
    fund, members, series, coverage = load(universe)
    print(f"Loaded {len(fund)} figures, {len(members)} members, {len(series)} price series in {time.monotonic() - t0:.0f}s")
    r = run(fund, members, series)
    r["universe"], r["coverage"] = universe, coverage
    print(json.dumps(r, indent=1, default=str)[:6000])
    text = message(r)
    print(text)
    storage.put_json(f"backtests/longterm/{market.now_ist():%Y-%m-%d}-{universe}.json", r)
    if "--no-send" not in argv:
        import telegram_bot as tg

        tg.send(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
