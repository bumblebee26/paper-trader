"""The lakehouse's technical scanner (Nifty 200, daily) on Telegram: /screen, /screen SYMBOL, the scanner line in
/analyze, and the 09:45 check of the morning's strict picks against the first half hour.

Source: stocks.gold.technical_screen (built after each NSE bhavcopy). Freshness rule as everywhere: if the scan
for the last NSE session hasn't arrived, nothing from an older day is shown in its place. Levels are indicative
(swing points and ATR) and never a guaranteed return. Code only, no Claude.
"""
from datetime import datetime, timedelta

import links
import market
from http_client import log

FIELDS = """symbol, company, CAST(trade_date AS STRING) AS trade_date, list_type, rank, close, setup, stance, rsi14,
            macd, macd_signal, macd_cross_up, adx14, adx_ok, close > sma20 AS above20, close > sma50 AS above50,
            close > sma200 AS above200, vol_ratio, mfi14, deliv_per, fno, entry_low, entry_high, trigger, stop,
            stop_basis, target1, target2, rr, misses, filters_passed"""
LIST_SQL = f"""SELECT {FIELDS} FROM stocks.gold.technical_screen
               WHERE list_type IN ('strict', 'near-miss') ORDER BY list_type DESC, rank"""
ONE_SQL = "SELECT {fields} FROM stocks.gold.technical_screen WHERE symbol = '{sym}'"
CHASE_PCT = 3.0     # at 09:45, this far above the entry zone is "don't chase"


def _sql(statement):
    import brief

    return brief.sql(statement, max_wait=60)   # the responder Lambda has under 2 minutes in all


def expected_session(now=None):
    """The NSE session the scan should be for: today after 21:00 IST on a trading day, else the one before."""
    now = now or market.now_ist()
    d = now.date()
    if market.is_trading_day(d) and now.hour >= 21:
        return d.isoformat()
    d -= timedelta(days=1)
    while not market.is_trading_day(d):
        d -= timedelta(days=1)
    return d.isoformat()


def _fresh(rows, now=None):
    exp = expected_session(now)
    if rows and (rows[0].get("trade_date") or "")[:10] >= exp:
        return rows, exp
    return [], exp


def rs(x):
    return "-" if x is None else f"{x:,.2f}"


def pick_lines(r):
    dma = "/".join(k for k, f in (("20", "above20"), ("50", "above50"), ("200", "above200")) if r.get(f)) or "none"
    out = [f"{r['symbol']} Rs {rs(r['close'])} · {r['setup']} · {r['stance']}",
           f"  RSI {r['rsi14']:.0f} · MACD {r['macd']:.2f}/{r['macd_signal']:.2f}"
           + (" crossed up" if r.get("macd_cross_up") else "")
           + f" · ADX {r['adx14']:.0f}{' rising' if r.get('adx_ok') else ''} · above DMA {dma} · vol {r['vol_ratio']:.1f}x"
           + (f" · deliv {r['deliv_per']:.0f}%" if r.get("deliv_per") is not None else "")
           + (" · F&O" if r.get("fno") else "")]
    if r.get("entry_high") is not None and r.get("stop") is not None:
        out.append(f"  entry {rs(r['entry_low'])}-{rs(r['entry_high'])}"
                   + (f" · trigger {rs(r['trigger'])}" if r.get("trigger") is not None else "")
                   + f" · stop {rs(r['stop'])} · T1 {rs(r['target1'])} · T2 {rs(r['target2'])} · R:R {r['rr']:.1f}")
    return out


def text(state=None, symbol=None, now=None):
    """/screen or /screen SYMBOL."""
    try:
        if symbol:
            sym = symbol.upper().replace("'", "")
            rows = _sql(ONE_SQL.format(fields=FIELDS, sym=sym))
        else:
            rows = _sql(LIST_SQL)
    except Exception as err:  # noqa: BLE001
        log.warning("screen unavailable: %s", err)
        return "[MOCK] The technical scanner isn't available right now (lakehouse not reachable)."
    rows, exp = _fresh(rows, now)
    day = datetime.strptime(exp, "%Y-%m-%d").strftime("%a %d %b")
    if not rows:
        if symbol:
            return (f"[MOCK] {symbol.upper()}: not in the scan for {day} (not a Nifty 200 stock, or the scan for "
                    f"{day} hasn't run yet; an older day isn't used).")
        return f"[MOCK] The scan for {day} hasn't run yet, so nothing is shown (an older day isn't used)."
    if symbol:
        r = rows[0]
        kind = {"strict": "a strict pick", "near-miss": "a near-miss"}.get(r["list_type"], "not on the list")
        lines = [f"[MOCK] Technical scanner · {r['symbol']} · NSE close {day}: {kind} "
                 f"({int(r['filters_passed'])}/6 filters)", *pick_lines(r)]
        if r.get("misses"):
            lines.append(f"  missed: {r['misses']}")
        lines += ["Indicative levels only, never a guaranteed return.", f"NSE: {links.nse(r['symbol'])}"]
        return "\n".join(lines)
    strict = [r for r in rows if r["list_type"] == "strict"][:10]
    near = [r for r in rows if r["list_type"] == "near-miss"][:8]
    lines = [f"[MOCK] Technical scanner · Nifty 200 · NSE close {day}"]
    if strict:
        lines.append(f"Strict picks ({len(strict)}):")
        for r in strict:
            lines += pick_lines(r)
    else:
        lines.append("No stock passed all six filters; none forced.")
    if near:
        lines.append("Near-misses:")
        lines += [f"- {r['symbol']} Rs {rs(r['close'])}: missed {r['misses']}" for r in near]
    lines.append("Indicative levels only, never a guaranteed return. /screen SYMBOL for one stock.")
    return "\n".join(lines)


def analyze_lines(symbol, now=None):
    """The scanner's line in /analyze's swing view."""
    rows = _sql(ONE_SQL.format(fields=FIELDS, sym=symbol.upper().replace("'", "")))
    rows, exp = _fresh(rows, now)
    if not rows:
        return ["- Technical scanner: not in the latest scan (not Nifty 200, or the scan hasn't run)."]
    r = rows[0]
    kind = {"strict": "strict pick", "near-miss": "near-miss"}.get(r["list_type"], "not on the list")
    out = [f"- Technical scanner ({exp}): {kind}, {int(r['filters_passed'])}/6 filters · {r['setup']} · {r['stance']}"]
    if r["list_type"] == "strict" and r.get("stop") is not None:
        out.append(f"  entry {rs(r['entry_low'])}-{rs(r['entry_high'])} · stop {rs(r['stop'])} · T2 {rs(r['target2'])}"
                   " (indicative)")
    elif r.get("misses"):
        out.append(f"  missed: {r['misses']}")
    return out


# ------------------------------------------------------------- 09:45 check --
def status(r, last, gap_pct):
    """One strict pick against the market now: (word, plain sentence, show the Swing buy button?)."""
    lo, hi, trig, stop = r.get("entry_low"), r.get("entry_high"), r.get("trigger"), r.get("stop")
    if stop is not None and last <= stop:
        return "invalidated", f"at {rs(last)}, at or below its stop {rs(stop)}: the setup failed", False
    if hi is not None and last > hi * (1 + CHASE_PCT / 100):
        return "extended", f"at {rs(last)}, {(last / hi - 1) * 100:.1f}% above its entry zone: don't chase", False
    if r.get("stance") == "breakout-only" and trig is not None:
        if last >= trig:
            return "triggered", f"at {rs(last)}, through its trigger {rs(trig)}", True
        return "waiting", f"at {rs(last)}, {(trig / last - 1) * 100:.1f}% under its trigger {rs(trig)}: not yet", False
    if lo is not None and hi is not None:
        if lo <= last <= hi:
            return "in zone", f"at {rs(last)}, inside its entry zone {rs(lo)}-{rs(hi)}", True
        if last > hi:
            return "above zone", f"at {rs(last)}, above its entry zone: wait for a dip to {rs(hi)}", False
        return "weak", f"at {rs(last)}, under its entry zone (stop {rs(stop)}): weakening", False
    return "no levels", f"at {rs(last)}; no grounded levels today", False


def confirm(send=True, now=None):
    """Guardian action scanner_confirm (~09:45 IST): the morning's strict picks after the first half hour."""
    import engine
    import telegram_bot as tg

    now = now or market.now_ist()
    if not market.is_trading_day(now.date()):
        return {"skipped": "market closed today"}
    try:
        rows = _sql(LIST_SQL)
    except Exception as err:  # noqa: BLE001
        log.warning("scanner check skipped: %s", err)
        return {"skipped": f"lakehouse unavailable: {err}"}
    rows, exp = _fresh(rows, now)
    strict = [r for r in rows if r["list_type"] == "strict"][:10]
    if not strict:
        return {"skipped": "no strict picks today" if rows else "scan not fresh"}
    lines = ["[MOCK] Scanner check, first half hour (indicative, nothing is bought):"]
    buttons, out = [], []
    for r in strict:
        try:
            last, _ = market.last_price(r["symbol"])
        except Exception as err:  # noqa: BLE001
            log.warning("price %s unavailable: %s", r["symbol"], err)
            last = None
        if not last:
            lines.append(f"- {r['symbol']}: no live price")
            continue
        rv = engine.opening_rvol(r["symbol"])
        word, sentence, button = status(r, last, None)
        pace = f" · opening volume {rv['rvol']:.1f}x usual" if rv else ""
        lines.append(f"- {r['symbol']} {word}: {sentence}{pace}")
        out.append({"symbol": r["symbol"], "status": word, "price": last, "rvol": rv["rvol"] if rv else None})
        if button and r.get("stop") is not None and r.get("target2") is not None:
            data = f"sw:{r['symbol']}:{r['stop']:.2f}:{r['target2']:.2f}"
            if len(data.encode()) <= 64:
                buttons.append([(f"Hand buy {r['symbol']} (outside auto rules, asks first)", data)])
    lines.append("A triggered or in-zone pick with strong opening volume is the scanner's plan working; the rest wait.")
    if send:
        tg.send("\n".join(lines), buttons=buttons or None)
    engine.journal("scanner_check", note=" ".join(f"{o['symbol']}:{o['status'].replace(' ', '_')}" for o in out))
    return {"session": exp, "picks": out}
