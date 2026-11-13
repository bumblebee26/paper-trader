"""Your own analysis and trades from Telegram (dummy money), for every pool.

  /analyze TCS [intraday|swing|longterm]   the trader's view of any NSE stock (all three views by default)
  /buy TCS [10] [sl 2400] [tgt 2480]        intraday long on the intraday ledger
  /short TCS [10] [sl 2440] [tgt 2380]      intraday short (sell first, buy back by 15:10)
  /swing buy TCS [10] [sl 2300] [tgt 2700]  swing pool (delivery); /swing sell TCS [qty]
  /longterm buy TCS [5]                     long-term pool (delivery); /longterm sell TCS [qty]  (also /lt)

Only the symbol is required. Defaults: intraday stop 1% away and target 2x the risk, quantity sized to 1R (one
normal trade's risk) and the 30% margin cap; swing/long-term quantity worth 10% of the pool. Each order is shown
first with its costs and any warnings, and runs only when you tap Confirm (within 3 minutes) at the live price.

Your trades use the same ledgers, costs and slippage as the trader's, and are marked custom: intraday ones as
setup "Custom" with "source custom" on the proposal (journal → gold.trades, gold.proposal_outcomes,
gold.custom_trades), pool ones as "pool_trade" events. They don't use the trader's daily trade allowance, but
their profit or loss counts toward the daily loss limit like any other. The track record keeps them apart
(dimension "source" = custom) so they don't change how the trader scores its own ideas.

Analysis is code only (prices, VWAP, ranges, the live checks, news, the lakehouse's company figures): no Claude.
"""
import math
import re
import uuid
from datetime import datetime, timedelta

import advisor
import config
import fees
import links
import market
import news
import pools
import scanner
import telegram_bot as tg
from http_client import log

ORDER_TTL_SEC = 180
PRICE_MOVE_PCT = 1.0          # at Confirm, a bigger move than this since the preview asks you to send it again
DEFAULT_STOP_PCT = 1.0
POOL_ORDER_PCT = 10.0         # default size of a swing/long-term buy, % of the pool
POOLS = {"SWING": "swing", "LONGTERM": "long_term", "LT": "long_term", "LONG": "long_term"}
VIEWS = {"INTRADAY": "intraday", "SWING": "swing", "LONGTERM": "longterm", "LT": "longterm", "LONG": "longterm"}
SYMBOL = re.compile(r"^[A-Z0-9&\-]{1,20}$")


# ------------------------------------------------------------------ parse --
def parse(parts):
    """Upper-cased words of a message -> ("analyze", {...}) | ("order", {...}) | ("error", text) | None."""
    if not parts:
        return None
    cmd = parts[0]
    if cmd in ("ANALYZE", "ANALYSE", "CHECK", "VIEW"):
        if len(parts) < 2 or not SYMBOL.match(parts[1]):
            return "error", "Send /analyze SYMBOL, e.g. /analyze TCS (add intraday, swing or longterm for one view)."
        view = VIEWS.get(parts[2]) if len(parts) > 2 else None
        return "analyze", {"symbol": parts[1], "view": view}
    if cmd in ("BUY", "SHORT"):
        return _order("intraday", "BUY" if cmd == "BUY" else "SELL", parts[1:])
    if cmd in POOLS:
        if len(parts) < 3 or parts[1] not in ("BUY", "SELL"):
            return "error", f"Send /{cmd.lower()} buy SYMBOL [qty] or /{cmd.lower()} sell SYMBOL [qty]."
        return _order(POOLS[cmd], parts[1], parts[2:])
    return None


def _order(pool, side, args):
    if not args or not SYMBOL.match(args[0]):
        return "error", "Which stock? e.g. /buy TCS 10, /short TCS, /swing buy TCS 5 (NSE symbols)."
    order = {"pool": pool, "side": side, "symbol": args[0], "qty": None, "sl": None, "tgt": None}
    rest = args[1:]
    i = 0
    while i < len(rest):
        w = rest[i]
        if w.isdigit() and order["qty"] is None:
            order["qty"] = int(w)
            i += 1
            continue
        key = {"SL": "sl", "STOP": "sl", "TGT": "tgt", "TARGET": "tgt", "TP": "tgt"}.get(w)
        if key and i + 1 < len(rest):
            try:
                order[key] = float(rest[i + 1].replace(",", ""))
            except ValueError:
                return "error", f"{w} needs a price, e.g. {w.lower()} 2400"
            i += 2
            continue
        return "error", f"I didn't understand '{w}'. Example: /buy TCS 10 sl 2400 tgt 2480"
    if order["qty"] is not None and order["qty"] < 1:
        return "error", "Quantity must be at least 1."
    return "order", order


# ---------------------------------------------------------------- analyze --
def _pct(a, b):
    return (a / b - 1) * 100 if a and b else None


def intraday_view(state, symbol):
    info = scanner.analyse(symbol)
    if info.get("error"):
        return [f"Intraday: no intraday prices for {symbol} right now ({info['error']}). Is the NSE symbol right?"]
    price = info["close"]
    ch = _pct(price, info.get("prev_close"))
    lines = [f"Intraday ({info['last_candle'][11:]}): Rs {price:,.2f}"
             + (f" ({ch:+.2f}% today, gap {info.get('gap_pct', 0):+.2f}%)" if ch is not None else "")]
    bits = []
    if info.get("vwap"):
        bits.append(f"{'above' if price > info['vwap'] else 'below'} VWAP {info['vwap']:,.2f}")
    if "or_high" in info:
        bits.append(f"opening range {info['or_low']:,.2f}–{info['or_high']:,.2f}")
    if "pdh" in info:
        bits.append(f"yesterday {info['pdl']:,.2f}–{info['pdh']:,.2f}")
    if info.get("vol_vs_avg") is not None:
        bits.append(f"last 5 min {info['vol_vs_avg']}x usual volume")
    if bits:
        lines.append("- " + " · ".join(bits))
    sigs = info.get("signals") or []
    lines.append("- Setup now: " + ("; ".join(f"{s['setup']} ({'BUY' if s['side'] == 'BUY' else 'SHORT'} ~{s['entry']:,.2f}, "
                                              f"stop {s['stop']:,.2f}, target {s['target']:,.2f})" for s in sigs)
                                    if sigs else "none on the last 5-minute candle"))
    d = price * DEFAULT_STOP_PCT / 100
    buy = advisor.assess(state, symbol, "BUY", price, price - d, price + 2 * d, 2.0)
    sell = advisor.assess(state, symbol, "SELL", price, price + d, price - 2 * d, 2.0)
    lean = "BUY" if buy["checks_pct"] > sell["checks_pct"] else "SHORT" if sell["checks_pct"] > buy["checks_pct"] else "neither"
    lines.append(f"- Live checks: BUY {buy['checks_pct']}% · SHORT {sell['checks_pct']}% → leaning {lean} "
                 "(market, VWAP side, strength vs sector, time left; not a chance of profit)")
    lines.append(f"- {(buy if lean != 'SHORT' else sell).get('market_short', '')}")
    lines.append(f"- {news.compact(symbol)}")
    return lines


def swing_view(symbol):
    try:
        bars = market.daily(symbol, "1y")
    except Exception as err:  # noqa: BLE001
        return [f"Swing: no daily prices for {symbol} ({err})."]
    closes = [b["close"] for b in bars if b.get("close")]
    if len(closes) < 60:
        return [f"Swing: not enough daily history for {symbol}."]
    last = closes[-1]
    avg = lambda n: sum(closes[-n:]) / n if len(closes) >= n else None  # noqa: E731
    ma20, ma50, ma200 = avg(20), avg(50), avg(200)
    hi20 = max(b["high"] for b in bars[-20:])
    lo20 = min(b["low"] for b in bars[-20:])
    r1m, r3m = _pct(last, closes[-22]), _pct(last, closes[-64]) if len(closes) >= 64 else None
    try:
        nb = [b["close"] for b in market.daily("NIFTYBEES", "6mo") if b.get("close")]
        n3m = _pct(nb[-1], nb[-64]) if len(nb) >= 64 else None
    except Exception:  # noqa: BLE001
        n3m = None
    if ma200 and last > ma50 > ma200:
        trend = "uptrend (above its 50- and 200-day averages)"
    elif ma200 and last < ma50 < ma200:
        trend = "downtrend (below its 50- and 200-day averages)"
    else:
        trend = "no clear trend"
    lines = [f"Swing (daily): {trend}"]
    lines.append(f"- 20-day range {lo20:,.2f}–{hi20:,.2f} ({_pct(last, hi20):+.1f}% from the high) · "
                 f"20-day average {ma20:,.2f}" + (f" · 200-day {ma200:,.2f}" if ma200 else ""))
    lines.append(f"- 1 month {r1m:+.1f}%" + (f" · 3 months {r3m:+.1f}%" if r3m is not None else "")
                 + (f" (NIFTYBEES {n3m:+.1f}%)" if n3m is not None else ""))
    near_high = _pct(last, hi20) > -1.5
    lines.append("- " + ("Near its 20-day high: a breakout is the swing shadow's setup when it comes with news and 2x volume."
                         if near_high else "Not near a breakout level; the swing rules would wait."))
    return lines


def longterm_view(symbol):
    import brief

    sym = symbol.replace("'", "")
    try:
        rows = brief.sql(f"""
            SELECT f.tests_passed, f.tests_available, f.roce_pct, f.roa_pct, f.is_financial, f.debt_to_equity,
                   f.revenue_cagr_3y_pct, f.profit_cagr_3y_pct, f.pe, f.pe_percentile, f.profit_down_2q,
                   c.rank, c.is_pick
            FROM stocks.gold.company_fundamentals f
            LEFT JOIN stocks.gold.longterm_candidates c USING (symbol)
            WHERE f.symbol = '{sym}'""", max_wait=40)
    except Exception as err:  # noqa: BLE001
        log.warning("long-term view unavailable: %s", err)
        return ["Long-term: the lakehouse's company figures aren't reachable right now; try again in a few minutes."]
    if not rows:
        return [f"Long-term: no company figures for {symbol} (fetched weekly for the Nifty 200 and the watchlist)."]
    r = rows[0]
    num = lambda k: float(r[k]) if r.get(k) not in (None, "") else None  # noqa: E731
    cap = (f"return on assets {num('roa_pct'):.2f}%" if str(r.get("is_financial")).lower() == "true" and num("roa_pct") is not None
           else f"return on capital {num('roce_pct'):.0f}%" if num("roce_pct") is not None else "return on capital n/a")
    bits = [cap]
    if num("debt_to_equity") is not None:
        bits.append(f"debt {num('debt_to_equity'):.2f}x equity")
    if num("profit_cagr_3y_pct") is not None:
        bits.append(f"profit {num('profit_cagr_3y_pct'):+.0f}%/yr over 3 years")
    if num("pe") is not None:
        bits.append(f"P/E {num('pe'):.0f}" + (f" (cheaper than {100 - num('pe_percentile'):.0f}% of those checked)"
                                              if num("pe_percentile") is not None else ""))
    lines = [f"Long-term: passes {int(num('tests_passed') or 0)} of {int(num('tests_available') or 0)} quality tests"]
    lines.append("- " + " · ".join(bits))
    if r.get("rank") not in (None, ""):
        pick = str(r.get("is_pick")).lower() == "true"
        lines.append(f"- This week's long-term rank: {int(float(r['rank']))}" + (" (one of the 10 picks)" if pick else ""))
    else:
        lines.append("- Not eligible for the long-term pool this week (fails a quality rule or isn't in the Nifty 200).")
    if str(r.get("profit_down_2q")).lower() == "true":
        lines.append("- ⚠️ Profit fell two quarters in a row.")
    return lines


def analyze_text(state, symbol, view=None):
    symbol = symbol.upper()
    head = [f"[MOCK] Analysis: {symbol}" + ("" if tradable_note(symbol) is None else f" ({tradable_note(symbol)})")]
    body = []
    if view in (None, "intraday"):
        body += intraday_view(state, symbol) + [""]
    if view in (None, "swing"):
        body += swing_view(symbol) + [""]
    if view in (None, "longterm"):
        body += longterm_view(symbol) + [""]
    tail = [f"Trade it: /buy {symbol} · /short {symbol} · /swing buy {symbol} · /longterm buy {symbol} "
            "(add a quantity, sl and tgt if you like). Dummy money only."]
    return "\n".join(head + [""] + body + tail)


def tradable_note(symbol):
    import engine

    if engine.tradable(symbol):
        return None
    return "not on the trader's own list; you can still trade it yourself"


# ------------------------------------------------------------------ orders --
def _window(pool):
    now = market.now_ist()
    if not market.is_trading_day(now):
        return "the market is closed today"
    end = "15:00" if pool == "intraday" else "15:30"
    if not ("09:15" <= now.strftime("%H:%M") <= end):
        return f"orders run 09:15–{end} IST on trading days (it's {now:%H:%M} now)"
    return None


def preview(state, order):
    """Check the order, work out the details and ask for Confirm. Returns the stored order or None."""
    import engine

    sym, pool, side = order["symbol"].upper(), order["pool"], order["side"]
    closed = _window(pool)
    if closed:
        tg.send(f"[MOCK] Can't place that now: {closed}. Send it again then.")
        return None
    price, _ = market.last_price(sym)
    if not price:
        tg.send(f"[MOCK] No live price for {sym}. Is it the NSE symbol (e.g. TCS, M&M, BAJAJ-AUTO)?")
        return None
    warnings, lines = [], []
    if pool == "intraday":
        if sym in state["positions"]:
            tg.send(f"[MOCK] You already have an intraday {sym} position; send EXIT {sym} first.")
            return None
        d = 1 if side == "BUY" else -1
        stop = order["sl"] if order["sl"] else round(price * (1 - d * DEFAULT_STOP_PCT / 100), 2)
        if (stop - price) * d >= 0:
            tg.send(f"[MOCK] The stop must be {'below' if side == 'BUY' else 'above'} the price (Rs {price:,.2f}).")
            return None
        per_share = abs(price - stop)
        target = order["tgt"] if order["tgt"] else round(price + d * 2 * per_share, 2)
        if (target - price) * d <= 0:
            tg.send(f"[MOCK] The target must be {'above' if side == 'BUY' else 'below'} the price (Rs {price:,.2f}).")
            return None
        free = state["balance"] - engine.margin_used(state)
        max_qty = math.floor(free * config.INTRADAY_LEVERAGE / price)
        cap_qty = math.floor(state["balance"] * config.MAX_POSITION_MARGIN_PCT / 100 * config.INTRADAY_LEVERAGE / price)
        qty = order["qty"] or max(0, min(math.floor(engine.one_r(state) / per_share), cap_qty))
        if qty < 1 or qty > max_qty:
            tg.send(f"[MOCK] Not enough free margin: up to {max_qty} {sym} fit (Rs {free:,.0f} free x 5).")
            return None
        risk = qty * per_share
        cost = fees.round_trip(price * qty, price * qty, side) + 2 * price * qty * config.SLIPPAGE_PCT / 100
        lines += [f"{'Buy' if side == 'BUY' else 'Sell (short)'} {qty} @ ~{price:,.2f} now, "
                  f"{'sell' if side == 'BUY' else 'buy back'} by 15:10",
                  f"🎯 {target:,.2f} (+{engine.inr(abs(target - price) * qty)}) · 🛑 {stop:,.2f} (-{engine.inr(risk)})"
                  f" · R:R {abs(target - price) / per_share:.1f}",
                  f"Margin {engine.inr(qty * price / config.INTRADAY_LEVERAGE)} of {engine.inr(free)} free · "
                  f"costs ~{engine.inr(cost)} round trip ({cost / risk * 100:.0f}% of the risk)"]
        if cost / risk * 100 > config.MAX_COST_SHARE_PCT:
            warnings.append(f"costs are {cost / risk * 100:.0f}% of the risk: the trader itself would skip a stop this tight")
        if risk > engine.loss_room(state):
            warnings.append(f"the risk ({engine.inr(risk)}) is more than the room left under today's loss limit "
                            f"({engine.inr(engine.loss_room(state))})")
        if risk > 1.5 * engine.one_r(state):
            warnings.append(f"that's {risk / engine.one_r(state):.1f}x a normal trade's risk ({engine.inr(engine.one_r(state))})")
        order.update(qty=qty, sl=stop, tgt=target, risk=round(risk, 2))
    else:
        p = pools.load(pool)
        label = "swing" if pool == "swing" else "long-term"
        if side == "BUY":
            qty = order["qty"] or math.floor(pools.equity(p) * POOL_ORDER_PCT / 100 / price)
            if qty < 1:
                tg.send(f"[MOCK] One {sym} share (Rs {price:,.0f}) is more than {POOL_ORDER_PCT:g}% of the {label} pool; "
                        "send a quantity if you want it anyway.")
                return None
            value = qty * price
            funding = _funding(p, value)
            why = _buy_problem(p, sym, qty, price, funding)
            if why:
                tg.send(f"[MOCK] Can't buy that in the {label} pool: {why}.")
                return None
            lines += [f"{label.capitalize()} pool: buy {qty} {sym} @ ~{price:,.2f} = {engine.inr(value)} (delivery)",
                      f"Costs ~{engine.inr(value * (config.DELIVERY_COST_PCT + config.SLIPPAGE_PCT) / 100)} · pool cash "
                      f"{engine.inr(p['cash'])}"]
            if funding:
                lines.append(f"Paid for by selling {funding['qty']} NIFTYBEES (~{engine.inr(funding['value'])}); "
                             "the rest of the pool stays in NIFTYBEES")
            if order["sl"] or order["tgt"]:
                if pool != "swing":
                    warnings.append("stops and targets are checked for the swing pool only; ignored here")
                    order.update(sl=None, tgt=None)
                else:
                    lines.append("Checked daily at 15:50: " + " · ".join(
                        x for x in (f"🛑 {order['sl']:,.2f}" if order["sl"] else "",
                                    f"🎯 {order['tgt']:,.2f}" if order["tgt"] else "") if x))
            order.update(qty=qty, funding=funding)
        else:
            h = p["holdings"].get(sym)
            if not h:
                tg.send(f"[MOCK] The {label} pool doesn't hold {sym}. /portfolio shows what it holds.")
                return None
            qty = min(order["qty"] or h["qty"], h["qty"])
            gain = (price - h["avg_cost"]) * qty
            lines += [f"{label.capitalize()} pool: sell {qty} of {h['qty']} {sym} @ ~{price:,.2f} = {engine.inr(qty * price)}",
                      f"Bought at {h['avg_cost']:,.2f} on average: about {engine.inr(gain, sign=True)} before costs"]
            order.update(qty=qty)
    if sym != "NIFTYBEES" and not engine.tradable(sym):
        warnings.append("not on the trader's own list (the 36-stock watchlist or the liquid Nifty 200)")
    if price < config.MIN_STOCK_PRICE:
        warnings.append(f"under Rs {config.MIN_STOCK_PRICE:g} a share: penny stocks can lock at price limits and have "
                        "wide spreads, so these results may be better than real life")
    oid = uuid.uuid4().hex[:8]
    word = {"BUY": "BUY", "SELL": "SHORT" if pool == "intraday" else "SELL"}[side]
    head = [f"[MOCK] Your order · {word} {sym}" + (f" · {pool.replace('_', '-')} pool" if pool != "intraday" else " · intraday")]
    text = "\n".join(head + lines + [f"⚠️ {w}" for w in warnings]
                     + ["", "Tap Confirm within 3 minutes; it runs at the live price then. Marked as your own (custom) trade."])
    msg_id = tg.send(text, buttons=[[(f"✅ Confirm {word}", f"cf:{oid}"), ("✖ Cancel", f"cx:{oid}")],
                                    links.buttons(sym, oid)[:-1] or [("NSE quote", links.nse(sym))]])
    stored = {**order, "symbol": sym, "price": price, "created": market.now_ist().strftime("%Y-%m-%d %H:%M:%S"),
              "message_id": msg_id, "text": text, "warnings": warnings}
    state.setdefault("custom_orders", {})[oid] = stored
    return stored


def _funding(p, value):
    """Swing pool: NIFTYBEES to sell when its cash can't pay for the buy (the pool's idle money is parked there)."""
    need = value * (1 + (config.DELIVERY_COST_PCT + config.SLIPPAGE_PCT) / 100) - p["cash"]
    h = p["holdings"].get(config.SWING_PARK_SYMBOL) if config.SWING_PARK_SYMBOL else None
    if need <= 0 or not h:
        return None
    px = h.get("last_price") or h["avg_cost"]
    qty = min(h["qty"], math.ceil(need * 1.01 / (px * (1 - (config.DELIVERY_COST_PCT + config.SLIPPAGE_PCT) / 100))))
    return {"symbol": config.SWING_PARK_SYMBOL, "qty": qty, "value": round(qty * px, 2)}


def _buy_problem(p, sym, qty, price, funding):
    trial = {**p, "holdings": {k: dict(v) for k, v in p["holdings"].items()}}
    if funding:
        h = trial["holdings"][funding["symbol"]]
        trial["cash"] = p["cash"] + funding["value"] * (1 - (config.DELIVERY_COST_PCT + config.SLIPPAGE_PCT) / 100)
        h["qty"] -= funding["qty"]
        if h["qty"] <= 0:
            del trial["holdings"][funding["symbol"]]
    return pools.buy_check(trial, sym, qty, price)


def confirm(state, oid, yes=True):

    o = (state.get("custom_orders") or {}).pop(oid, None)
    if not o:
        tg.send("[MOCK] That order is no longer open (confirmed, cancelled or expired).")
        return None
    if not yes:
        tg.edit(o["message_id"], o["text"] + "\n\n-> Cancelled. Nothing was traded.")
        return {"status": "cancelled"}
    created = datetime.strptime(o["created"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=market.IST)
    if market.now_ist() - created > timedelta(seconds=ORDER_TTL_SEC):
        tg.edit(o["message_id"], o["text"] + "\n\n-> Expired (3 minutes). Send the order again.")
        return {"status": "expired"}
    closed = _window(o["pool"])
    if closed:
        tg.edit(o["message_id"], o["text"] + f"\n\n-> Not placed: {closed}.")
        return {"status": "closed"}
    price, ts = market.last_price(o["symbol"])
    if not price:
        tg.send(f"[MOCK] No live price for {o['symbol']} right now; nothing was traded.")
        return {"status": "no_price"}
    if abs(price / o["price"] - 1) * 100 > PRICE_MOVE_PCT:
        tg.edit(o["message_id"], o["text"] + f"\n\n-> Not placed: the price moved to {price:,.2f} "
                                             f"({(price / o['price'] - 1) * 100:+.1f}%). Send it again to see the new numbers.")
        return {"status": "moved"}
    if o["pool"] == "intraday":
        return _intraday(state, o, price)
    return _pool(state, o, price)


def _intraday(state, o, price):
    import engine

    pid = "c" + uuid.uuid4().hex[:7]
    now = market.now_ist()
    state["proposals"][pid] = {
        "day": engine._today(), "symbol": o["symbol"], "side": o["side"], "setup": "Custom", "entry": price,
        "stop": o["sl"], "target": o["tgt"], "qty": o["qty"], "risk_rs": o["risk"],
        "rr": round(abs(o["tgt"] - price) / abs(price - o["sl"]), 2), "reasons": ["your own trade"],
        "invalid_if": "", "status": "approved", "sent_at": engine._now(),
        "valid_until": (now + timedelta(seconds=60)).strftime("%Y-%m-%d %H:%M:%S"), "message_id": o["message_id"],
        "text": o["text"], "suggested_yes_pct": None, "review": None, "mode": "custom", "user_reply": "CUSTOM",
    }
    engine.journal("proposed", proposal_id=pid, symbol=o["symbol"], setup="Custom", side=o["side"], entry=price,
                   stop=o["sl"], target=o["tgt"], qty=o["qty"], risk_rs=o["risk"],
                   rr=state["proposals"][pid]["rr"], reasons="your own trade", user_reply="CUSTOM",
                   note="your own order from Telegram | mode custom | source custom"
                        + (" | warnings " + "; ".join(o["warnings"]).replace("|", "/") if o["warnings"] else ""))
    return engine.execute(state, pid)


def _pool(state, o, price):
    import engine

    p = pools.load(o["pool"])
    label = "Swing" if o["pool"] == "swing" else "Long-term"
    if o["side"] == "BUY":
        f = _funding(p, o["qty"] * price)
        if f:
            fpx, _ = market.last_price(f["symbol"])
            out = pools.sell(p, f["symbol"], qty=f["qty"], price=fpx or f["value"] / f["qty"],
                             reason=f"to pay for your {o['symbol']} buy")
            _journal_pool(o["pool"], "SELL", f["symbol"], out, note="funding your own buy")
        out = pools.buy(p, o["symbol"], o["qty"], price, reason="your own trade", origin="custom",
                        stop=o.get("sl"), target=o.get("tgt"))
        if out["status"] != "bought":
            pools.save(p)
            tg.edit(o["message_id"], o["text"] + f"\n\n-> Not placed: {out['reason']}.")
            return out
        pools.save(p)
        _journal_pool(o["pool"], "BUY", o["symbol"], out)
        done = f"Bought {out['qty']} {o['symbol']} @ {out['fill']:,.2f} in the {label.lower()} pool"
    else:
        out = pools.sell(p, o["symbol"], qty=o["qty"], price=price, reason="your own trade")
        if out["status"] != "sold":
            tg.edit(o["message_id"], o["text"] + f"\n\n-> Not placed: {out['reason']}.")
            return out
        pools.save(p)
        _journal_pool(o["pool"], "SELL", o["symbol"], out)
        done = (f"Sold {out['qty']} {o['symbol']} @ {out['fill']:,.2f} from the {label.lower()} pool: "
                f"{engine.inr(out['pnl_rs'], sign=True)} after costs")
    tg.edit(o["message_id"], o["text"] + f"\n\n-> Done @ {out['fill']:,.2f}")
    tg.send(f"[MOCK] {done}.\n{label} pool now: {engine.inr(pools.equity(p))} (cash {engine.inr(p['cash'])}).")
    return out


def _journal_pool(pool, side, symbol, out, note="your own order from Telegram"):
    import engine

    engine.journal("pool_trade", symbol=symbol, setup=f"Custom {pool}", side=side, qty=out.get("qty"),
                   fill_price=out.get("fill"), fees_rs=out.get("fees_rs", ""), pnl_rs=out.get("pnl_rs", ""),
                   user_reply="CUSTOM",
                   note=f"pool {pool} | {note} | source custom")


def expire(state):
    """Drop orders nobody confirmed within 3 minutes (their messages say so)."""
    now = market.now_ist()
    for oid, o in list((state.get("custom_orders") or {}).items()):
        created = datetime.strptime(o["created"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=market.IST)
        if now - created > timedelta(seconds=ORDER_TTL_SEC):
            state["custom_orders"].pop(oid)
            tg.edit(o["message_id"], o["text"] + "\n\n-> Expired (3 minutes). Nothing was traded.")


def check_swing_stops(today=None):
    """15:50 (guardian, with the swing shadow): your swing holdings' stops and targets on today's daily candle."""
    import engine

    p = pools.load("swing")
    today = today or market.now_ist().strftime("%Y-%m-%d")
    done = []
    for sym, h in list(p["holdings"].items()):
        if h.get("origin") != "custom" or not (h.get("stop") or h.get("target")):
            continue
        try:
            bar = next((b for b in market.candles(sym, "1d", 5) if b["time"].startswith(today)), None)
        except Exception:  # noqa: BLE001
            bar = None
        if not bar:
            continue
        if h.get("stop") and bar["low"] <= h["stop"]:
            price, why = min(bar["open"], h["stop"]), "your stop"
        elif h.get("target") and bar["high"] >= h["target"]:
            price, why = max(bar["open"], h["target"]), "your target"
        else:
            continue
        out = pools.sell(p, sym, price=price, reason=why)
        _journal_pool("swing", "SELL", sym, out, note=f"your own trade: {why} hit")
        done.append(f"Sold {out['qty']} {sym} @ {out['fill']:,.2f} ({why}): {engine.inr(out['pnl_rs'], sign=True)}")
    if done:
        pools.save(p)
        tg.send("[MOCK] Swing pool, your own trades:\n" + "\n".join(f"- {d}" for d in done))
    return done
