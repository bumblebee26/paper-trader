"""Walk-forward training and evaluation of the four shadow models (scikit-learn gradient-boosted trees).

Walk-forward = every day is predicted by models trained only on earlier days (refit every REFIT_DAYS, after a
first MIN_TRAIN_DAYS), so the results are what the models would have done live. Thresholds and the stop width
are chosen on the training days only, by the net R they would have made there.

  vol_stop     predicts the next 60 minutes' high-low range; the stop goes k x that range from the entry (never
               tighter than the scanner's), with fewer shares so the rupee risk is unchanged
  filter       predicts whether a setup ends in profit after costs; setups under the threshold are skipped
  early_cut    at each 5-minute close while a trade is losing, predicts whether holding will end worse than
               cutting now; above the threshold the trade is closed at that close
  let_run      when a trade reaches its 2R target, predicts whether running on (stop to +1R, trailing 1R) beats
               booking it; above the threshold it runs
  combined     filter + early_cut + let_run together, on the scanner's stops
"""
import math
import statistics

MIN_TRAIN_DAYS = 15
REFIT_DAYS = 5
VOL_K = (0.5, 0.75, 1.0, 1.25)
THRESHOLDS = (0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70)


def _sk():
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

    return HistGradientBoostingClassifier, HistGradientBoostingRegressor


def _matrix(rows, names):
    return [[float(r.get(n, 0.0) or 0.0) for n in names] for r in rows]


def _clf(X, y):
    C, _ = _sk()
    if len(set(y)) < 2 or len(y) < 40:
        return None
    return C(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=20,
             l2_regularization=1.0, random_state=0).fit(X, y)


def _reg(X, y):
    _, R = _sk()
    if len(y) < 40:
        return None
    return R(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=20,
             l2_regularization=1.0, random_state=0).fit(X, y)


def _proba(model, X):
    if model is None or not X:
        return [None] * len(X)
    return [p[1] for p in model.predict_proba(X)]


def _best_threshold(probs, gain_if_act, gain_if_not, min_share=0.2):
    """The threshold (from THRESHOLDS) that maximises total R on training rows; acting = p >= threshold."""
    best, best_total = None, None
    n = len(probs)
    for th in THRESHOLDS:
        act = [p is not None and p >= th for p in probs]
        if n and sum(act) < min_share * n and min_share > 0:
            continue
        total = sum(a if x else b for x, a, b in zip(act, gain_if_act, gain_if_not))
        if best_total is None or total > best_total:
            best, best_total = th, total
    return best


def names(data):
    sig_names = sorted(data[0]["features"]) if data else []
    cp_names = sorted(next((d["checkpoints"][0]["features"] for d in data if d["checkpoints"]), {}))
    lr_names = sorted(next((d["let_run"]["features"] for d in data if d["let_run"]), {}))
    return sig_names, cp_names, lr_names


def walk_forward(data, vol_stop_fn):
    """data: labelled signals (models.intraday.dataset). vol_stop_fn(sig, pred_range_pct, k) -> R.
    Returns (per-signal out-of-sample decisions, model notes)."""
    sig_names, cp_names, lr_names = names(data)
    days = sorted({d["date"] for d in data})
    out, notes = [], []
    models = None
    for n, day in enumerate(days):
        if n < MIN_TRAIN_DAYS:
            continue
        if models is None or (n - MIN_TRAIN_DAYS) % REFIT_DAYS == 0:
            train = [d for d in data if d["date"] < day]
            models = fit(train, sig_names, cp_names, lr_names, vol_stop_fn)
            notes.append({"refit_on": day, "train_days": n, "train_signals": len(train),
                          **{k: v for k, v in models.items() if k.startswith("th_") or k == "vol_k"}})
        for d in [x for x in data if x["date"] == day]:
            out.append(decide(d, models, sig_names, cp_names, lr_names, vol_stop_fn))
    return out, notes


def fit(train, sig_names, cp_names, lr_names, vol_stop_fn):
    m = {}
    # volatility: predicted 60-minute range at the signal
    vrows = [d for d in train if d["fwd_range_pct"] is not None]
    m["vol"] = _reg(_matrix([d["features"] for d in vrows], sig_names), [d["fwd_range_pct"] for d in vrows])
    if m["vol"] is not None:
        preds = m["vol"].predict(_matrix([d["features"] for d in train], sig_names))
        best_k, best_total = None, None
        for k in VOL_K:
            total = sum(r for r in (vol_stop_fn(d, p, k) for d, p in zip(train, preds)) if r is not None)
            if best_total is None or total > best_total:
                best_k, best_total = k, total
        m["vol_k"] = best_k
    # filter: P(profit after costs)
    X = _matrix([d["features"] for d in train], sig_names)
    y = [1 if d["results"]["current"] > 0 else 0 for d in train]
    m["filter"] = _clf(X, y)
    probs = _proba(m["filter"], X)
    m["th_filter"] = _best_threshold(probs, [d["results"]["current"] for d in train], [0.0] * len(train))
    # early cut: P(holding ends worse than cutting now)
    cps = [(d, c) for d in train for c in d["checkpoints"]]
    Xc = _matrix([c["features"] for _, c in cps], cp_names)
    yc = [1 if c["hold_r"] < c["cut_r"] else 0 for _, c in cps]
    m["cut"] = _clf(Xc, yc)
    m["th_cut"] = _cut_threshold(train, m["cut"], cp_names)
    # let run: P(running on beats booking 2R)
    lrs = [d for d in train if d["let_run"]]
    Xl = _matrix([d["let_run"]["features"] for d in lrs], lr_names)
    yl = [1 if d["let_run"]["run_r"] > d["let_run"]["take_r"] else 0 for d in lrs]
    m["run"] = _clf(Xl, yl)
    pl = _proba(m["run"], Xl)
    m["th_run"] = _best_threshold(pl, [d["let_run"]["run_r"] for d in lrs], [d["let_run"]["take_r"] for d in lrs],
                                  min_share=0)
    return m


def _cut(d, model, th, cp_names):
    """(R, fired) with the early-cut rule: the first losing checkpoint the model flags, else the current R."""
    if model is None or th is None or not d["checkpoints"]:
        return d["results"]["current"], False
    probs = _proba(model, _matrix([c["features"] for c in d["checkpoints"]], cp_names))
    for c, p in zip(d["checkpoints"], probs):
        if p is not None and p >= th:
            return c["cut_r"], True
    return d["results"]["current"], False


def _cut_r(d, model, th, cp_names):
    return _cut(d, model, th, cp_names)[0]


def _cut_threshold(train, model, cp_names):
    best, best_total = None, None
    for th in THRESHOLDS:
        total = sum(_cut_r(d, model, th, cp_names) for d in train)
        if best_total is None or total > best_total:
            best, best_total = th, total
    base = sum(d["results"]["current"] for d in train)
    return best if best_total is not None and best_total > base else None   # no cut rule if it never helped


def _run_r(d, model, th, lr_names):
    if not d["let_run"] or model is None or th is None:
        return d["results"]["current"]
    p = _proba(model, _matrix([d["let_run"]["features"]], lr_names))[0]
    return d["let_run"]["run_r"] if p is not None and p >= th else d["let_run"]["take_r"]


def decide(d, m, sig_names, cp_names, lr_names, vol_stop_fn):
    x = _matrix([d["features"]], sig_names)
    p = _proba(m["filter"], x)[0]
    take = p is None or m["th_filter"] is None or p >= m["th_filter"]
    pred_range = float(m["vol"].predict(x)[0]) if m.get("vol") is not None else None
    vol_r = vol_stop_fn(d, pred_range, m["vol_k"]) if pred_range is not None and m.get("vol_k") else None
    cut, cut_fired = _cut(d, m["cut"], m["th_cut"], cp_names)
    run = _run_r(d, m["run"], m["th_run"], lr_names)
    # combined: skipped if filtered out; else early cut if it fired (the trade is closed there), else let-run
    combined = 0.0 if not take else (cut if cut_fired else run)
    return {"symbol": d.get("symbol"), "date": d["date"], "time": d["time"], "setup": d["setup"], "side": d["side"],
            "p_win": p, "taken": take, "pred_range_pct": pred_range, "actual_range_pct": d["fwd_range_pct"],
            "r": {**d["results"], "filter": d["results"]["current"] if take else 0.0, "vol_stop": vol_r,
                  "early_cut": cut, "let_run": run, "combined": combined,
                  # background rule on trial (trader/stretch.py): skip breakouts that already ran a long way
                  "skip_stretched": 0.0 if d.get("stretched") else d["results"]["current"],
                  # friend's suggestion B1 (24 Nov): skip when the nearest level is under 1R away
                  "skip_low_room": 0.0 if (d.get("room_r") is not None and d["room_r"] < 1)
                  else d["results"]["current"]}}


def summarise(decisions):
    """Totals per rule over the out-of-sample signals: R, average R, win rate, days up, and the worst day."""
    rules = sorted({k for d in decisions for k in d["r"]})
    out = {}
    for rule in rules:
        vals = [(d["date"], d["r"][rule]) for d in decisions if d["r"].get(rule) is not None]
        if not vals:
            continue
        skips = ("filter", "combined", "skip_stretched", "skip_low_room")
        traded = [v for _, v in vals if not (rule in skips and v == 0.0)]
        per_day = {}
        for day, v in vals:
            per_day[day] = per_day.get(day, 0.0) + v
        out[rule] = {"signals": len(vals), "traded": len(traded), "total_r": round(sum(v for _, v in vals), 1),
                     "avg_r": round(sum(traded) / len(traded), 3) if traded else 0.0,
                     "win_pct": round(sum(1 for v in traded if v > 0) / len(traded) * 100, 1) if traded else 0.0,
                     "days": len(per_day), "days_up_pct": round(sum(1 for v in per_day.values() if v > 0)
                                                               / len(per_day) * 100, 1),
                     "worst_day_r": round(min(per_day.values()), 1)}
    vol = [(d["pred_range_pct"], d["actual_range_pct"]) for d in decisions
           if d["pred_range_pct"] is not None and d["actual_range_pct"] is not None]
    if len(vol) > 10:
        out["_vol_model"] = {"rows": len(vol), "corr": round(statistics.correlation(*zip(*vol)), 3),
                             "mae_pct": round(sum(abs(a - b) for a, b in vol) / len(vol), 3),
                             "naive_mae_pct": None}
    probs = [(d["p_win"], d["r"]["current"] > 0) for d in decisions if d["p_win"] is not None]
    if probs:
        bands = {}
        for p, won in probs:
            b = f"{math.floor(p * 10) * 10}-{math.floor(p * 10) * 10 + 9}%"
            bands.setdefault(b, []).append(won)
        out["_filter_calibration"] = {b: {"n": len(v), "won_pct": round(sum(v) / len(v) * 100, 1)}
                                      for b, v in sorted(bands.items())}
    return out
