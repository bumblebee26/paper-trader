"""Plain-words lines about the shadow models (jobs/model_shadow.py) for /shadow and the weekly report.

Reads models/results/history.json from S3; no ML libraries needed here. Paper only: these models change nothing
until they are reviewed with you (before December) and switched on.
"""
import storage

WORDS = {
    "current": "current rules", "filter": "trade filter", "vol_stop": "volatility stop", "early_cut": "early cut",
    "let_run": "let winners run", "combined": "models combined", "no_stop": "no stop",
    "wide_1_5": "stop 1.5x wider", "wide_2": "stop 2x wider", "close_stop": "stop on a 5-min close",
    "trailing": "trailing stop", "half_and_run": "sell half at +1R", "breakeven": "stop to entry at +1R",
    "time_stop": "60-min time stop",
}


def history():
    try:
        return storage.get_json("models/results/history.json") or []
    except Exception:  # noqa: BLE001 - a report line is extra
        return []


def lines(days=5):
    hist = history()
    if not hist:
        return ["Shadow models: no results yet (first run after the next close)."]
    last = hist[-1]
    s = last.get("summary") or {}
    base = (s.get("current") or {}).get("total_r")
    out = []
    if base is not None:
        ranked = sorted(((k, v["total_r"]) for k, v in s.items() if k in WORDS and k != "current"),
                        key=lambda x: -x[1])
        out.append(f"Shadow models, backtest to {last['date']} ({s['current']['days']} days out of sample, "
                   f"{s['current']['signals']} setups): current rules {base:+.0f}R; best "
                   + ", ".join(f"{WORDS[k]} {v:+.0f}R" for k, v in ranked[:3]) + ".")
    real = [t for h in hist[-days:] for t in h.get("real_trades") or [] if t.get("scored")]
    if real:
        actual = sum(t["actual_rs"] for t in real)
        totals = {}
        for t in real:
            for k, v in (t.get("rs") or {}).items():
                if v is not None:
                    totals[k] = totals.get(k, 0.0) + v
        best = sorted(((k, v) for k, v in totals.items() if k in WORDS and k != "current"), key=lambda x: -x[1])[:3]
        skipped = sum(1 for t in real if not t.get("filter_takes"))
        out.append(f"On your real trades, last {days} days ({len(real)} trades, actual {actual:+,.0f} Rs): "
                   + ", ".join(f"{WORDS[k]} {v:+,.0f}" for k, v in best)
                   + f"; the filter would have skipped {skipped}.")
    return out
