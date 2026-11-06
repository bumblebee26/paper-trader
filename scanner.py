"""Finds ORB, VWAP reclaim/rejection and previous-day high/low setups on completed 5-min candles."""
from datetime import datetime, timedelta

import market


def _completed(bars):
    """Drop the still-forming last 5-min candle."""
    if not bars:
        return bars
    last = datetime.strptime(bars[-1]["time"], "%Y-%m-%d %H:%M").replace(tzinfo=market.IST)
    return bars[:-1] if last + timedelta(minutes=5) > market.now_ist() else bars


def _vwap(bars):
    out, pv, vol = [], 0.0, 0
    for b in bars:
        tp = (b["high"] + b["low"] + b["close"]) / 3
        pv += tp * b["volume"]
        vol += b["volume"]
        out.append(round(pv / vol, 2) if vol else None)
    return out


def analyse(symbol: str, bars=None):
    bars = _completed(bars if bars is not None else market.candles(symbol, "5m", 5))
    if not bars:
        return {"symbol": symbol, "error": "no data"}
    today = bars[-1]["time"][:10]
    t = [b for b in bars if b["time"][:10] == today]
    prev_days = sorted({b["time"][:10] for b in bars if b["time"][:10] < today})
    prev = [b for b in bars if prev_days and b["time"][:10] == prev_days[-1]]
    info = {"symbol": symbol.upper(), "last_candle": t[-1]["time"], "close": t[-1]["close"]}
    if prev:
        info.update(prev_close=prev[-1]["close"], pdh=max(b["high"] for b in prev),
                    pdl=min(b["low"] for b in prev))
        info["gap_pct"] = round((t[0]["open"] - info["prev_close"]) / info["prev_close"] * 100, 2)
    vw = _vwap(t)
    info["vwap"] = vw[-1]
    orb = [b for b in t if b["time"][11:] < "09:30"]
    if len(orb) >= 3:
        info["or_high"] = max(b["high"] for b in orb)
        info["or_low"] = min(b["low"] for b in orb)
    vols = [b["volume"] for b in bars[-21:-1]]
    avg_vol = sum(vols) / len(vols) if vols else 0
    cur, before = t[-1], (t[-2] if len(t) >= 2 else None)
    info["vol_vs_avg"] = round(cur["volume"] / avg_vol, 2) if avg_vol else None

    signals = []

    def add(setup, side, stop, evidence):
        entry = cur["close"]
        risk = abs(entry - stop)
        if risk <= 0:
            return
        target = entry + 2 * risk if side == "BUY" else entry - 2 * risk
        signals.append({"setup": setup, "side": side, "entry": round(entry, 2),
                        "stop": round(stop, 2), "target": round(target, 2), "evidence": evidence})

    if before is None:
        info["signals"] = signals
        return info
    volume_ok = info["vol_vs_avg"] is not None and info["vol_vs_avg"] >= 1.2
    above_vwap = info["vwap"] is not None and cur["close"] > info["vwap"]
    below_vwap = info["vwap"] is not None and cur["close"] < info["vwap"]

    if "or_high" in info and cur["time"][11:] >= "09:30":
        if before["close"] <= info["or_high"] < cur["close"] and volume_ok and above_vwap:
            add("ORB long", "BUY", max(info["or_low"], cur["low"]),
                f"5m close {cur['close']} broke opening-range high {info['or_high']} on "
                f"{info['vol_vs_avg']}x avg volume; above VWAP {info['vwap']}")
        if before["close"] >= info["or_low"] > cur["close"] and volume_ok and below_vwap:
            add("ORB short", "SELL", min(info["or_high"], cur["high"]),
                f"5m close {cur['close']} broke opening-range low {info['or_low']} on "
                f"{info['vol_vs_avg']}x avg volume; below VWAP {info['vwap']}")

    if info["vwap"] is not None and len(vw) >= 2 and vw[-2] is not None:
        if before["close"] < vw[-2] and cur["close"] > vw[-1]:
            add("VWAP reclaim", "BUY", cur["low"],
                f"closed back above VWAP {vw[-1]} (prev close {before['close']} was below)")
        if before["close"] > vw[-2] and cur["close"] < vw[-1]:
            add("VWAP rejection", "SELL", cur["high"],
                f"closed back below VWAP {vw[-1]} (prev close {before['close']} was above)")

    if "pdh" in info and volume_ok:
        if before["close"] <= info["pdh"] < cur["close"]:
            add("PDH break", "BUY", cur["low"],
                f"closed above previous-day high {info['pdh']} on {info['vol_vs_avg']}x volume")
        if before["close"] >= info["pdl"] > cur["close"]:
            add("PDL break", "SELL", cur["high"],
                f"closed below previous-day low {info['pdl']} on {info['vol_vs_avg']}x volume")

    info["signals"] = signals
    return info


def nifty_context():
    bars = _completed(market.candles("^NSEI", "5m", 1))
    if not bars:
        return {"error": "no Nifty data"}
    day_high = max(b["high"] for b in bars)
    day_low = min(b["low"] for b in bars)
    first, last = bars[0], bars[-1]
    return {
        "nifty_last": last["close"], "time": last["time"], "day_high": day_high, "day_low": day_low,
        "change_from_open_pct": round((last["close"] - first["open"]) / first["open"] * 100, 2),
        "at_day_low": last["low"] <= day_low, "at_day_high": last["high"] >= day_high,
        "note": "Nifty index has no volume on Yahoo, so no VWAP; use direction from open and day range.",
    }
