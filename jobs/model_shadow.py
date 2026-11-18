"""Shadow models for the intraday pool: trained and judged every trading day after the close. Nothing here
places, changes or closes a trade.

  python jobs/model_shadow.py [--no-send] [--days N]

1. Data: 5-minute candles for every stock in the tradable universe (stocks.gold.intraday_universe, else the
   watchlist) and the Nifty 50, from Yahoo (60 days available), merged into a cache in S3 (models/bars5m/) so
   the history keeps growing beyond Yahoo's 60 days.
2. Backtest, walk-forward (models/walkforward.py): every scanner setup on every stock, each day predicted only by
   models trained on earlier days. Rules compared on the same signals: the current rules; no stop; stops 1.5x/2x
   wider with the same rupee risk; stop on a 5-minute close; trailing/breakeven/half-and-run/time stop; a 1.5R
   target; the previous day's high/low as the target when it comes before 2R (both from 19 Nov); and the
   four models (volatility stop, trade filter, early cut, let winners run) plus all of them combined.
3. Today's real trades: each is scored by models trained on all earlier days, and replayed under every rule.
4. Results: journal events "model_shadow" per real trade and "model_backtest" per rule (pnl_rs = total R; both
   reach the lakehouse with the 23:30 export, for the dashboard), models/results/<date>.json and models/results/history.json
   in S3, a short Telegram note, and the full report in the job log. Reviewed with you before any goes live.
"""
import argparse
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "trader"), os.path.join(HERE, "..")]

import market  # noqa: E402
import sectors  # noqa: E402
import storage  # noqa: E402
from http_client import log  # noqa: E402
from models import intraday, walkforward  # noqa: E402

CACHE = "models/bars5m/{sym}.json"
KEEP_DAYS = 250
UNIVERSE_SQL = "SELECT symbol FROM stocks.gold.intraday_universe WHERE eligible ORDER BY symbol"
RULE_ORDER = ("current", "filter", "vol_stop", "early_cut", "let_run", "combined", "no_stop", "wide_1_5", "wide_2",
              "close_stop", "trailing", "half_and_run", "breakeven", "time_stop", "target_1_5r", "level_target")
RULE_WORDS = {
    "current": "current rules", "filter": "model: trade filter", "vol_stop": "model: volatility stop",
    "early_cut": "model: early cut", "let_run": "model: let winners run", "combined": "models combined",
    "no_stop": "no stop", "wide_1_5": "stop 1.5x wider, same Rs risk", "wide_2": "stop 2x wider, same Rs risk",
    "close_stop": "stop on a 5-min close", "trailing": "trailing stop", "half_and_run": "sell half at +1R",
    "breakeven": "stop to entry at +1R", "time_stop": "60-minute time stop",
    "target_1_5r": "target 1.5R", "level_target": "target at yesterday's high/low",
}


NIFTY200_CSV = "https://niftyindices.com/IndexConstituent/ind_nifty200list.csv"


def nifty200():
    """The Nifty 200 list straight from niftyindices.com (for a run without the lakehouse)."""
    import csv
    import io

    from http_client import request

    rows = csv.DictReader(io.StringIO(request("GET", NIFTY200_CSV).decode("utf-8-sig")))
    return [r["Symbol"].strip() for r in rows if r.get("Symbol")]


def universe():
    try:
        import brief

        rows = brief.sql(UNIVERSE_SQL)
        if rows:
            return [r["symbol"] for r in rows]
    except Exception as err:  # noqa: BLE001
        log.warning("universe unavailable, using the watchlist: %s", err)
    return list(sectors.WATCHLIST)


def complete_days(bars, today):
    """Drop today's candles if the session isn't over (a run before 15:30 IST)."""
    now = market.now_ist()
    if now.strftime("%Y-%m-%d") == today and now.strftime("%H:%M") < "15:31":
        return [b for b in bars if b["time"][:10] < today]
    return bars


def load_bars(sym, today):
    """Yahoo's last 60 days of 5-minute candles merged with the S3 cache; the cache is updated."""
    cached = storage.get_json(CACHE.format(sym=sym)) or []
    try:
        fresh = market.candles(sym, "5m", 60)
    except Exception as err:  # noqa: BLE001
        log.warning("%s candles unavailable: %s", sym, err)
        fresh = []
    merged = {b["time"]: b for b in cached}
    merged.update({b["time"]: b for b in fresh if b.get("volume") is not None or sym.startswith("^")})
    bars = complete_days(sorted(merged.values(), key=lambda b: b["time"]), today)
    days = sorted({b["time"][:10] for b in bars})[-KEEP_DAYS:]
    bars = [b for b in bars if b["time"][:10] >= days[0]] if days else []
    for b in bars:
        b["volume"] = b.get("volume") or 0
    if fresh:
        storage.put_json(CACHE.format(sym=sym), bars)
    return bars


def todays_trades(today):
    rows = storage.read_rows(f"journal/{today}.csv") or []
    fills = {r["proposal_id"]: r for r in rows if r["event"] == "filled" and r.get("proposal_id")}
    exits_ = {r["proposal_id"]: r for r in rows if r["event"] in ("exited", "squared_off") and r.get("proposal_id")}
    out = []
    for pid, f in fills.items():
        x = exits_.get(pid)
        if not x:
            continue
        net = float(x.get("pnl_rs") or 0) - float(f.get("fees_rs") or 0)
        out.append({"proposal_id": pid, "symbol": f["symbol"], "date": today, "time": f["timestamp_ist"][11:16],
                    "setup": f.get("setup") or "", "side": f["side"], "entry": float(f["fill_price"]),
                    "stop": float(f["stop"]), "target": float(f["target"]), "qty": int(float(f["qty"])),
                    "actual_rs": round(net, 2)})
    return out


def score_real(trades, bars, nifty, data, days_bars):
    """Each real trade replayed under every rule, scored by models trained on all days before it."""
    if not trades or not data:
        return []
    names = walkforward.names(data)
    train = [d for d in data if d["date"] < trades[0]["date"]]
    models = walkforward.fit(train, *names, vol_fn(days_bars))
    out = []
    for t in trades:
        b = bars.get(t["symbol"]) or []
        sig = intraday.signal_from_trade(b, nifty, t)
        if not sig:
            out.append({**t, "scored": False})
            continue
        sig["symbol"] = t["symbol"]
        dec = walkforward.decide(sig, models, *names, vol_fn({t["symbol"]: intraday.by_day(b)}))
        # in rupees at the trade's real size: R x the trade's own 1R
        one_r = abs(t["entry"] - t["stop"]) * t["qty"]
        out.append({**t, "scored": True, "p_win": dec["p_win"], "filter_takes": dec["taken"],
                    "pred_range_pct": dec["pred_range_pct"], "one_r_rs": round(one_r, 2),
                    "r": dec["r"], "rs": {k: (round(v * one_r, 2) if v is not None else None)
                                          for k, v in dec["r"].items()}})
    return out


def vol_fn(days_bars):
    def f(sig, pred, k):
        return intraday.vol_stop_r(sig, days_bars[sig["symbol"]][sig["date"]], pred, k)
    return f


def report_lines(summary, real, notes, n_syms, n_days):
    lines = [f"Backtest, out of sample: {n_syms} stocks, {summary.get('current', {}).get('days', 0)} of {n_days} days "
             "predicted by models trained only on earlier days. R = one unit of risk (Rs ~400 at today's size)."]
    base = summary.get("current", {}).get("total_r", 0.0)
    for rule in RULE_ORDER:
        s = summary.get(rule)
        if not s:
            continue
        lines.append(f"  {RULE_WORDS[rule]:32} {s['total_r']:+8.1f}R ({s['total_r'] - base:+7.1f} vs current) | "
                     f"{s['traded']:5d} trades, avg {s['avg_r']:+.3f}R, win {s['win_pct']:4.1f}%, "
                     f"days up {s['days_up_pct']:4.1f}%, worst day {s['worst_day_r']:+.1f}R")
    if "_vol_model" in summary:
        v = summary["_vol_model"]
        lines.append(f"  volatility model: predicted vs actual 60-min range correlation {v['corr']}, "
                     f"average error {v['mae_pct']}%")
    if "_filter_calibration" in summary:
        lines.append("  trade filter calibration (predicted win chance -> actual): " + ", ".join(
            f"{b}: {v['won_pct']}% of {v['n']}" for b, v in summary["_filter_calibration"].items()))
    if notes:
        lines.append(f"  last refit: {notes[-1]}")
    if real:
        lines.append(f"Today's real trades ({len(real)}):")
        for t in real:
            if not t.get("scored"):
                lines.append(f"  {t['symbol']} {t['side']}: not scored (candles missing); actual Rs {t['actual_rs']:+.2f}")
                continue
            rs = t["rs"]
            lines.append(f"  {t['symbol']} {t['side']} {t['setup']} at {t['time']}: actual Rs {t['actual_rs']:+.2f} | "
                         f"filter {'takes' if t['filter_takes'] else 'SKIPS'} (p {t['p_win']:.2f}) | " + ", ".join(
                             f"{RULE_WORDS[k]} {rs[k]:+.0f}" for k in RULE_ORDER if rs.get(k) is not None))
    return lines


def telegram_lines(summary, real, today):
    base = summary.get("current", {}).get("total_r")
    if base is None:
        return []
    best = sorted(((k, v["total_r"]) for k, v in summary.items() if k in RULE_WORDS and k != "current"),
                  key=lambda x: -x[1])[:3]
    lines = [f"[MOCK] Shadow models, {today} (paper only, nothing traded):",
             f"Backtest, last {summary['current']['days']} days out of sample: current rules {base:+.0f}R; best: "
             + ", ".join(f"{RULE_WORDS[k]} {v:+.0f}R" for k, v in best)]
    for t in [t for t in real if t.get("scored")]:
        rs = t["rs"]
        lines.append(f"{t['symbol']}: actual {t['actual_rs']:+.0f}; filter {'takes' if t['filter_takes'] else 'skips'}"
                     f" ({t['p_win'] * 100:.0f}%), early cut {rs['early_cut']:+.0f}, let run {rs['let_run']:+.0f}, "
                     f"vol stop {(rs.get('vol_stop') or 0):+.0f}")
    lines.append("Review with you before December; nothing goes live before that.")
    return lines


def main(send=True, max_syms=None, use_nifty200=False):
    started = time.time()
    today = market.now_ist().strftime("%Y-%m-%d")
    syms = nifty200() if use_nifty200 else universe()
    syms = syms[:max_syms] if max_syms else syms
    bars = {s: load_bars(s, today) for s in syms}
    bars = {s: b for s, b in bars.items() if b}
    nifty = load_bars("^NSEI", today)
    days_bars = {s: intraday.by_day(b) for s, b in bars.items()}
    log.info("candles for %d stocks in %.0fs", len(bars), time.time() - started)
    data = intraday.dataset(bars, nifty)
    n_days = len({d["date"] for d in data})
    log.info("%d labelled signals over %d days", len(data), n_days)
    decisions, notes = walkforward.walk_forward(data, vol_fn(days_bars))
    summary = walkforward.summarise(decisions)
    trades = todays_trades(today) if market.is_trading_day(market.now_ist()) else []
    for t in trades:   # traded names outside the universe still need their candles
        if t["symbol"] not in bars:
            bars[t["symbol"]] = load_bars(t["symbol"], today)
    real = score_real(trades, bars, nifty, [d for d in data if d["date"] < today], days_bars)
    lines = report_lines(summary, real, notes, len(bars), n_days)
    print("\n".join(lines))
    result = {"date": today, "generated_at": datetime.now().isoformat(timespec="seconds"), "stocks": len(bars),
              "signals": len(data), "summary": summary, "refits": notes, "real_trades": real}
    storage.put_json(f"models/results/{today}.json", result)
    hist = storage.get_json("models/results/history.json") or []
    hist = [h for h in hist if h.get("date") != today] + [{"date": today, "summary": summary, "real_trades": real}]
    storage.put_json("models/results/history.json", hist[-120:])
    import engine

    for t in real:
        if t.get("scored"):
            engine.journal("model_shadow", event_id=f"model-{t['proposal_id']}", proposal_id=t["proposal_id"],
                           symbol=t["symbol"], setup=t["setup"], side=t["side"], pnl_rs=t["actual_rs"],
                           note=";".join(f"{k}={v:.2f}" for k, v in t["rs"].items() if v is not None)
                           + f";p_win={t['p_win']:.3f};filter_takes={int(bool(t['filter_takes']))}")
    # the backtest summary per rule, for the lakehouse dashboard (one row per rule per day)
    for rule, s in summary.items():
        if rule in RULE_WORDS:
            engine.journal("model_backtest", event_id=f"modelbt-{today}-{rule}", setup=rule, qty=s["traded"],
                           pnl_rs=s["total_r"], note=";".join(f"{k}={s[k]}" for k in (
                               "signals", "traded", "total_r", "avg_r", "win_pct", "days", "days_up_pct",
                               "worst_day_r")))
    if send:
        msg = telegram_lines(summary, real, today)
        if msg:
            import telegram_bot as tg

            tg.send("\n".join(msg))
    log.info("done in %.0fs", time.time() - started)
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-send", action="store_true")
    ap.add_argument("--max-symbols", type=int, default=None)
    ap.add_argument("--nifty200", action="store_true", help="universe from niftyindices.com, not the lakehouse")
    a = ap.parse_args()
    main(send=not a.no_send, max_syms=a.max_symbols, use_nifty200=a.nifty200)
