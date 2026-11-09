"""Paper-trading engine: dummy-money ledger, risk gate, approvals, fills, exits, reports.

No broker or bank is ever contacted. Prices come from `market`, approvals from `telegram_bot`.
"""
import math
import time
import uuid
from datetime import datetime, timedelta

import advisor
import config
import market
import reviewer
import sectors
import storage
import telegram_bot as tg

JOURNAL_COLUMNS = [
    "timestamp_ist", "event", "symbol", "setup", "side", "entry", "stop", "target", "qty",
    "risk_rs", "rr", "reasons", "user_reply", "fill_price", "exit_price", "exit_reason",
    "pnl_rs", "note", "event_id", "proposal_id", "fees_rs",
]


# ---------------------------------------------------------------- ledger ----
def _today() -> str:
    return market.now_ist().strftime("%Y-%m-%d")


def _now() -> str:
    return market.now_ist().strftime("%Y-%m-%d %H:%M:%S")


def load() -> dict:
    state = storage.get_json(storage.LEDGER_KEY)
    if state is None:
        state = {
            "balance": config.STARTING_CAPITAL,
            "starting_capital": config.STARTING_CAPITAL,
            "positions": {},
            "proposals": {},
            "decisions": {},
            "exit_requests": [],
            "tg_offset": 0,
            "day": None,
        }
    if state.get("day") != _today():
        _new_day(state)
    return state


def save(state: dict) -> None:
    storage.put_json(storage.LEDGER_KEY, state)


def _apply_capital_change(state: dict) -> None:
    """When STARTING_CAPITAL changes, move the balance by the same amount, so the P&L so far is kept."""
    old = state.get("starting_capital", config.STARTING_CAPITAL)
    new = config.STARTING_CAPITAL
    if old == new or state["positions"]:
        return
    state["balance"] = round(state["balance"] + new - old, 2)
    state["starting_capital"] = new
    pnl = state["balance"] - new
    journal("capital_change", note=f"intraday pool {inr(old)} -> {inr(new)}; P&L so far {inr(pnl, sign=True)} kept")
    tg.send(f"[MOCK] Intraday pool resized from {inr(old)} to {inr(new)} (part of the Rs 2 lakh dummy portfolio). "
            f"Your P&L so far ({inr(pnl, sign=True)}) is kept: balance now {inr(state['balance'])}.")


def _new_day(state: dict) -> None:
    state["day"] = _today()
    _apply_capital_change(state)
    state["day_start_balance"] = state["balance"]
    state["trades_today"] = 0
    state["realized_today"] = 0.0
    state["loss_limit_hit"] = False
    state["loss_streak"] = 0
    state["cooloff_until"] = None
    state["sector_trades"] = {}
    for p in state["proposals"].values():
        if p["status"] in ("pending", "approved"):
            p["status"] = "expired"
    state["proposals"] = {k: v for k, v in state["proposals"].items() if v.get("day") == _today()}
    state["decisions"] = {}
    state["exit_requests"] = []
    state["requests"] = []
    state["day_risk_pct"] = config.RISK_PER_TRADE_PCT
    state["plan"] = None
    state["focus"] = []
    if state["positions"]:
        state["carried_over_warning"] = list(state["positions"].keys())


def journal(event: str, event_id: str = "", **fields) -> None:
    """Append one event. event_id is unique (random unless the caller needs a fixed one,
    e.g. a re-sent report), so the Databricks copy can de-duplicate safely."""
    row = dict(fields, timestamp_ist=_now(), event=event, event_id=event_id or uuid.uuid4().hex)
    storage.append_row(f"journal/{_today()}.csv", JOURNAL_COLUMNS, row)


# ----------------------------------------------------------------- helpers --
def _hhmm(s: str):
    h, m = s.split(":")
    return int(h), int(m)


def _at(hhmm: str) -> datetime:
    h, m = _hhmm(hhmm)
    return market.now_ist().replace(hour=h, minute=m, second=0, microsecond=0)


def in_entry_window() -> bool:
    now = market.now_ist()
    return now.weekday() < 5 and _at(config.ENTRY_START) <= now <= _at(config.ENTRY_END)


def past_square_off() -> bool:
    return market.now_ist() >= _at(config.SQUARE_OFF)


def costs(value: float) -> float:
    return config.BROKERAGE_PER_ORDER  # per order; slippage is applied to the price itself


def slip(price: float, side: str, opening: bool) -> float:
    """Worse price for the trader: buy higher, sell lower."""
    buying = (side == "BUY") == opening
    f = config.SLIPPAGE_PCT / 100
    return round(price * (1 + f) if buying else price * (1 - f), 2)


def margin_used(state: dict) -> float:
    return sum(p["qty"] * p["fill_price"] for p in state["positions"].values()) / config.INTRADAY_LEVERAGE


def inr(x, sign=False) -> str:
    """Rupees with Indian digit grouping: 100000 -> '1,00,000'; sign=True adds +/-."""
    neg = x < 0
    n = f"{abs(round(x)):d}"
    if len(n) > 3:
        head, tail = n[:-3], n[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        n = ",".join(([head] if head else []) + groups) + "," + tail
    prefix = "-" if neg else ("+" if sign and x > 0 else "")
    return f"{prefix}Rs {n}"


def ledger_line(state) -> str:
    """One line for Telegram: what the dummy money is doing right now."""
    used = margin_used(state)
    n = len(state["positions"])
    return (f"Ledger: balance {inr(state['balance'])} | in use {inr(used)} "
            f"({n} position{'s' if n != 1 else ''}) | free {inr(state['balance'] - used)} | "
            f"today {inr(state['realized_today'], sign=True)}")


def ledger_statement(state) -> str:
    """Full statement for the LEDGER command: money, positions with live P&L."""
    used = margin_used(state)
    exposure = used * config.INTRADAY_LEVERAGE
    lines = [
        f"[MOCK] Ledger at {market.now_ist():%H:%M} IST",
        f"Starting capital: {inr(state['starting_capital'])}",
        f"Balance now:      {inr(state['balance'])} ({inr(state['balance'] - state['starting_capital'], sign=True)} total)",
        f"In use (margin):  {inr(used)}",
        f"Free to trade:    {inr(state['balance'] - used)}",
        f"Exposure:         {inr(exposure)} ({config.INTRADAY_LEVERAGE:g}x intraday)",
        f"Today realized:   {inr(state['realized_today'], sign=True)} | trades {state['trades_today']}"
        f"/{config.MAX_TRADES_PER_DAY}",
    ]
    if state["positions"]:
        lines.append("Open positions:")
        total = 0.0
        for sym, p in state["positions"].items():
            price, _ = market.last_price(sym)
            d = 1 if p["side"] == "BUY" else -1
            if price is None:
                lines.append(f"- {p['side']} {sym} {p['qty']} @ {p['fill_price']:,.2f} (no live price)")
                continue
            upnl = (price - p["fill_price"]) * p["qty"] * d
            total += upnl
            lines.append(f"- {p['side']} {sym} {p['qty']} @ {p['fill_price']:,.2f}, now {price:,.2f}: "
                         f"{inr(upnl, sign=True)} | SL {p['stop']:,.2f} TGT {p['target']:,.2f}")
        lines.append(f"Unrealized: {inr(total, sign=True)} (before exit costs)")
    else:
        lines.append("No open positions.")
    return "\n".join(lines)


HELP_TEXT = """[MOCK] paper-trader: what I can do

Commands (with or without the /):
/ledger  money now: balance, in use, free, open positions with live P&L (also /balance, /status)
/plan  today's plan: US move, VIX, risk per trade, bias, focus list
/exit SYMBOL  close that position now, e.g. /exit HDFCBANK
/pause  no new proposals for the day; open positions are still managed. After 14:30, at weekends or on holidays it applies to the next trading day
/resume  allow new proposals again
YES / NO  answer the latest proposal (or tap its buttons)
/help  this list (also /prompts, /commands, or ask "what can you do?")

On its own (Mon-Fri IST): 09:08 day plan; proposals until 14:30, each needs your YES within 3 min; stops and targets watched every 5 min; 15:10 square-off; ~15:20 day report; 15:45 journal to the lakehouse.
During sessions (09:08-15:25 IST) I reply within seconds; at other times within about 2 minutes. Dummy money only."""


def _plan_text(state) -> str:
    plan = state.get("plan")
    if not plan:
        return "[MOCK] No plan yet today. It is set at the start of the morning session (09:08 IST)."
    import brief

    text = brief.plan_message({**plan, "risk_per_trade_pct": state.get("day_risk_pct")}, state.get("focus", []))
    if is_paused(state):
        text += "\nNew proposals: PAUSED (send /resume)"
    return text


def next_trading_day() -> str:
    """Today if entries are still possible today, else the next weekday (holidays not known)."""
    now = market.now_ist()
    if now.weekday() < 5 and now <= _at(config.ENTRY_END):
        return now.strftime("%Y-%m-%d")
    d = now + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def is_paused(state) -> bool:
    return state.get("paused_for") == _today()


def answer_requests(state) -> int:
    """Reply to Telegram commands that came in (ledger, help, plan, pause, resume)."""
    asks = list(dict.fromkeys(state.pop("requests", [])))
    for ask in asks:
        if ask == "ledger":
            tg.send(ledger_statement(state))
        elif ask == "help":
            tg.send(HELP_TEXT)
        elif ask == "plan":
            tg.send(_plan_text(state))
        elif ask == "pause":
            day = next_trading_day()
            state["paused_for"] = day
            journal("paused_by_user", note=f"no new proposals on {day}")
            if day == _today():
                tg.send("[MOCK] Paused: no new proposals today. Open positions are still watched and squared off "
                        "at 15:10. Send /resume to allow proposals again.")
            else:
                tg.send(f"[MOCK] Paused for {datetime.strptime(day, '%Y-%m-%d'):%a %d %b}: that day's sessions "
                        f"will make no proposals. Send /resume to undo.")
        elif ask == "resume":
            was = state.pop("paused_for", None)
            journal("resumed_by_user")
            tg.send("[MOCK] Resumed: proposals allowed again." if was else "[MOCK] Nothing was paused.")
        elif ask == "nothing_pending":
            tg.send("[MOCK] There's no proposal waiting for an answer right now.")
        elif ask == "unknown":
            tg.send("[MOCK] I didn't catch that. Send /help to see what I can do.")
    return len(asks)


def account(state: dict) -> dict:
    return {
        "dummy_balance": round(state["balance"], 2),
        "starting_capital": state["starting_capital"],
        "total_pnl": round(state["balance"] - state["starting_capital"], 2),
        "realized_today": round(state["realized_today"], 2),
        "trades_today": state["trades_today"],
        "open_positions": state["positions"],
        "margin_used": round(margin_used(state), 2),
        "margin_free": round(state["balance"] - margin_used(state), 2),
        "entry_window_open": in_entry_window(),
        "risk_per_trade_pct_today": state.get("day_risk_pct", config.RISK_PER_TRADE_PCT),
        "focus": state.get("focus", []),
        "paused_by_user": is_paused(state),
        "carried_over_warning": state.get("carried_over_warning"),
    }


# -------------------------------------------------------------- risk gate --
def loss_limit_rs(state) -> float:
    if config.DAILY_LOSS_LIMIT_RS > 0:
        return config.DAILY_LOSS_LIMIT_RS
    return state["day_start_balance"] * config.DAILY_LOSS_LIMIT_PCT / 100


def trades_left_text(state) -> str:
    if config.MAX_TRADES_PER_DAY <= 0:
        return ""
    used = state.get("trades_today", 0)
    if used >= config.MAX_TRADES_PER_DAY:
        return f", but all {config.MAX_TRADES_PER_DAY} trades for today are used"
    return f" ({config.MAX_TRADES_PER_DAY - used} of {config.MAX_TRADES_PER_DAY} trades left today)"


def in_cooloff(state) -> bool:
    until = state.get("cooloff_until")
    return bool(until) and _now() < until


def record_result(state, net) -> None:
    """Count losses in a row; after COOLOFF_AFTER_LOSSES of them, no new proposals for COOLOFF_MIN."""
    if net >= 0:
        state["loss_streak"] = 0
        return
    state["loss_streak"] = state.get("loss_streak", 0) + 1
    n = config.COOLOFF_AFTER_LOSSES
    if n > 0 and state["loss_streak"] % n == 0:
        until = market.now_ist() + timedelta(minutes=config.COOLOFF_MIN)
        state["cooloff_until"] = until.strftime("%Y-%m-%d %H:%M:%S")
        tg.send(f"[MOCK] {state['loss_streak']} losses in a row, so a {config.COOLOFF_MIN}-minute cool-off: no new "
                f"proposals until {until:%H:%M}. Open positions are still managed.\n"
                "In plain words: a short break after a losing run, so the next trade isn't a reaction to the "
                "last ones.")
        journal("cooloff", note=f"{state['loss_streak']} losses in a row, until {until:%H:%M}")


def loss_limit_reached(state) -> bool:
    return -state["realized_today"] >= loss_limit_rs(state)


def check_loss_limit(state) -> None:
    """Tell the user when the day's realized loss crosses the limit, and again when a winning exit brings it
    back under (then new proposals are allowed again). Called after every change to realized P&L."""
    hit = loss_limit_reached(state)
    if hit == bool(state.get("loss_limit_hit")):
        return
    state["loss_limit_hit"] = hit
    today, limit = inr(state["realized_today"], sign=True), inr(loss_limit_rs(state))
    if hit:
        tg.send(f"[MOCK] Daily loss limit reached: today {today} (limit -{limit}). No new proposals for now; "
                "open positions are still managed. If a winning exit brings the loss back under the limit, "
                "trading resumes by itself.")
        journal("loss_limit_hit", pnl_rs=round(state["realized_today"], 2))
    else:
        more = trades_left_text(state)
        tg.send(f"[MOCK] Back under the daily loss limit: today {today} (limit -{limit}). New proposals are "
                f"allowed again{more}.")
        journal("loss_limit_cleared", pnl_rs=round(state["realized_today"], 2))


def risk_gate(state: dict, symbol: str, side: str, entry: float, stop: float, target: float):
    """Return (ok, reason, qty, risk_rs, rr)."""
    side = side.upper()
    if side not in ("BUY", "SELL"):
        return False, "side must be BUY or SELL", 0, 0, 0
    per_share = abs(entry - stop)
    if per_share <= 0:
        return False, "stop must differ from entry", 0, 0, 0
    if (side == "BUY" and not (stop < entry < target)) or (side == "SELL" and not (target < entry < stop)):
        return False, "for BUY need stop < entry < target; for SELL need target < entry < stop", 0, 0, 0
    rr = round(abs(target - entry) / per_share, 2)
    if is_paused(state):
        return False, "paused by you on Telegram (send /resume)", 0, 0, rr
    if not in_entry_window():
        return False, f"outside entry window {config.ENTRY_START}-{config.ENTRY_END} IST (weekdays)", 0, 0, rr
    if symbol in state["positions"]:
        return False, f"already holding {symbol}", 0, 0, rr
    if len(state["positions"]) >= config.MAX_OPEN_POSITIONS:
        return False, "max open positions reached", 0, 0, rr
    if 0 < config.MAX_TRADES_PER_DAY <= state["trades_today"]:
        return False, f"all {config.MAX_TRADES_PER_DAY} trades for today are used", 0, 0, rr
    if in_cooloff(state):
        return False, (f"cool-off after {state.get('loss_streak', 0)} losses in a row, until "
                       f"{state['cooloff_until'][11:16]}"), 0, 0, rr
    sec = sectors.sector(symbol)
    open_in_sector = [s for s in state["positions"] if sectors.sector(s) == sec]
    if len(open_in_sector) >= config.MAX_OPEN_PER_SECTOR:
        return False, (f"already holding {', '.join(open_in_sector)} in {sec}; at most "
                       f"{config.MAX_OPEN_PER_SECTOR} open per sector"), 0, 0, rr
    if state.get("sector_trades", {}).get(sec, 0) >= config.MAX_TRADES_PER_SECTOR:
        return False, f"already {config.MAX_TRADES_PER_SECTOR} {sec} trades today; pick another sector", 0, 0, rr
    if loss_limit_reached(state):
        return False, (f"daily loss limit reached (today {inr(state['realized_today'], sign=True)}, limit "
                       f"-{inr(loss_limit_rs(state))}); it lifts by itself if a winning exit brings the loss "
                       "back under the limit"), 0, 0, rr
    if rr < config.MIN_RR:
        return False, f"risk-reward {rr} is below minimum {config.MIN_RR}", 0, 0, rr
    risk_budget = state["balance"] * state.get("day_risk_pct", config.RISK_PER_TRADE_PCT) / 100
    qty = math.floor(risk_budget / per_share)
    free = state["balance"] - margin_used(state)
    max_qty_by_margin = math.floor(free * config.INTRADAY_LEVERAGE / entry)
    cap_margin = state["balance"] * config.MAX_POSITION_MARGIN_PCT / 100
    max_qty_by_cap = math.floor(cap_margin * config.INTRADAY_LEVERAGE / entry)
    qty = min(qty, max_qty_by_margin, max_qty_by_cap)
    if qty < 1:
        return False, "not enough free margin for even 1 share", 0, 0, rr
    return True, "ok", qty, round(qty * per_share, 2), rr


# ---------------------------------------------------------- plain words --
SETUP_PLAIN = {
    "ORB long": "the price broke above its first 15-minute range with strong buying, which often keeps going up",
    "ORB short": "the price broke below its first 15-minute range with strong selling, which often keeps going down",
    "VWAP reclaim": "the price climbed back above today's average traded price (VWAP), a sign buyers are back in control",
    "VWAP rejection": "the price failed to stay above today's average traded price (VWAP), a sign sellers are in control",
    "PDH break": "the price broke above yesterday's high with strong volume, a sign of fresh buying",
    "PDL break": "the price broke below yesterday's low with strong volume, a sign of fresh selling",
}

EXIT_PLAIN = {
    "target": "It reached the target price, so the profit was booked as planned.",
    "stop_loss": "It hit the stop-loss price, so it was closed to keep the loss small, as planned.",
    "square_off": "Intraday trades must close the same day, so it was closed at 15:10 at the market price.",
    "user_exit": "You asked to exit, so it was closed at the market price.",
}


def plain_proposal(side, symbol, setup, entry, stop, target, qty, risk_rs, summary="") -> list[str]:
    """Plain-language explanation of a proposal (code-built, so the numbers always match)."""
    buy = side == "BUY"
    gain = abs(target - entry) * qty
    idea = SETUP_PLAIN.get(setup, f"a '{setup}' setup appeared")
    lines = [
        "In plain words:",
        (f"- Buy {qty} {symbol} shares at about Rs {entry:,.2f} and sell them later today."
         if buy else
         f"- Sell {qty} {symbol} shares at about Rs {entry:,.2f} now and buy them back later today "
         "(this profits if the price falls)."),
        f"- Why now: {idea}.",
    ]
    if summary:
        lines.append(f"- {summary.strip()}")
    lines += [
        f"- If it works: the price {'rises' if buy else 'falls'} to Rs {target:,.2f} and you make about "
        f"{inr(gain)}.",
        f"- If it fails: the price {'falls' if buy else 'rises'} to Rs {stop:,.2f}, it closes automatically "
        f"and you lose about {inr(risk_rs)}.",
        f"- So you risk {inr(risk_rs)} to try to make {inr(gain)} (before small fees). If neither "
        "happens, it closes at 15:10.",
    ]
    return lines


# -------------------------------------------------------------- proposals --
def propose(state, symbol, side, setup, entry, stop, target, reasons, invalid_if, news="none", summary="",
            ai_yes_pct=None):
    symbol, side = symbol.upper(), side.upper()
    ok, why, qty, risk_rs, rr = risk_gate(state, symbol, side, entry, stop, target)
    if not ok:
        journal("blocked_by_risk_gate", symbol=symbol, setup=setup, side=side, entry=entry,
                stop=stop, target=target, rr=rr, note=why)
        return {"status": "blocked", "reason": why}

    pid = uuid.uuid4().hex[:8]
    risk_pct = round(risk_rs / state["balance"] * 100, 2)
    need = qty * entry / config.INTRADAY_LEVERAGE
    advice = advisor.assess(state, symbol, side, entry, stop, target, rr, ai_yes_pct)
    review = reviewer.review(state, symbol, side, setup, entry, stop, target, rr, qty, advice["market_lines"])
    if review:
        advice = advisor.combine(advice, ai_yes_pct, review)
    valid_until = market.now_ist() + timedelta(seconds=config.APPROVAL_TIMEOUT_SEC)  # after the review
    lines = [
        f"[MOCK] {side} {symbol}  ({setup})",
        *advice["lines"],
        "",
        *plain_proposal(side, symbol, setup, entry, stop, target, qty, risk_rs, summary),
        "",
        *advice["market_lines"],
        "",
        "Details:",
        f"Entry ~{entry:,.2f} | SL {stop:,.2f} | Target {target:,.2f}",
        f"Qty {qty} | Risk Rs {risk_rs:,.0f} ({risk_pct}%) | R:R {rr}",
        f"Uses {inr(need)} margin of {inr(state['balance'] - margin_used(state))} free",
        "Why:",
        *[f"{i}. {r}" for i, r in enumerate(reasons[:3], 1)],
        f"News: {news}",
        f"Invalid if: {invalid_if}",
        f"Valid until {valid_until.strftime('%H:%M:%S')} IST. Dummy money only.",
    ]
    text = "\n".join(lines)
    msg_id = tg.send(text, buttons=[("YES - place", f"yes:{pid}"), ("NO - skip", f"no:{pid}")])
    state["proposals"][pid] = {
        "day": _today(), "symbol": symbol, "side": side, "setup": setup, "entry": entry,
        "stop": stop, "target": target, "qty": qty, "risk_rs": risk_rs, "rr": rr,
        "reasons": reasons[:3], "invalid_if": invalid_if, "status": "pending",
        "sent_at": _now(), "valid_until": valid_until.strftime("%Y-%m-%d %H:%M:%S"),
        "message_id": msg_id, "text": text, "suggested_yes_pct": advice["yes_pct"],
        "review": review,
    }
    journal("proposed", proposal_id=pid, symbol=symbol, setup=setup, side=side, entry=entry, stop=stop,
            target=target, qty=qty, risk_rs=risk_rs, rr=rr, reasons=" | ".join(reasons[:3]),
            note=(f"suggested {advice['verdict']} {advice['yes_pct']}% | checks {advice['checks_pct']}%"
                  f" | agent {ai_yes_pct if ai_yes_pct is not None else '-'}%"
                  f" | reviewer {review['verdict'] + ' ' + str(review['yes_pct']) + '%' if review else '-'}"))
    return {"status": "pending", "proposal_id": pid, "qty": qty, "risk_rs": risk_rs, "rr": rr,
            "suggestion": f"{advice['verdict']} {advice['yes_pct']}%",
            "valid_until": state["proposals"][pid]["valid_until"]}


def _expired(p) -> bool:
    vu = datetime.strptime(p["valid_until"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=market.IST)
    return market.now_ist() > vu


def wait_decision(state, pid, max_wait=45):
    p = state["proposals"].get(pid)
    if not p:
        return {"status": "unknown_proposal"}
    if p["status"] != "pending":
        return {"status": p["status"]}
    deadline = time.time() + max(1, min(max_wait, 170))
    while time.time() < deadline:
        tg.poll(state, wait=min(20, max(1, int(deadline - time.time()))))
        verdict = state["decisions"].get(pid)
        if verdict:
            p["status"] = "approved" if verdict == "yes" else "rejected"
            p["user_reply"] = verdict.upper()
            p["decided_at"] = _now()
            if _expired(p) and p["status"] == "approved":
                p["status"] = "expired"
                tg.edit(p["message_id"], p["text"] + "\n\n-> YES arrived after expiry. Not placed, no money used.")
            elif p["status"] == "rejected":
                tg.edit(p["message_id"], p["text"] + "\n\n-> Skipped (NO). Not placed, no money used.")
                journal("rejected", proposal_id=pid, symbol=p["symbol"], setup=p["setup"], side=p["side"],
                        entry=p["entry"], stop=p["stop"], target=p["target"], user_reply="NO")
            if p["status"] == "expired":
                journal("expired", proposal_id=pid, symbol=p["symbol"], setup=p["setup"], user_reply="YES-late")
            return {"status": p["status"]}
        if _expired(p):
            break
    if _expired(p):
        p["status"] = "expired"
        tg.edit(p["message_id"], p["text"] + "\n\n-> Expired with no reply (3 minutes). Not placed, no money used.")
        journal("expired", proposal_id=pid, symbol=p["symbol"], setup=p["setup"], side=p["side"],
                entry=p["entry"], stop=p["stop"], target=p["target"], user_reply="none")
        return {"status": "expired"}
    return {"status": "pending", "valid_until": p["valid_until"]}


def execute(state, pid):
    """Place the simulated order for an APPROVED proposal (code-enforced)."""
    p = state["proposals"].get(pid)
    if not p or p["status"] != "approved":
        return {"status": "refused", "reason": "only an approved proposal can be executed"}
    if _expired(p):
        p["status"] = "expired"
        return {"status": "refused", "reason": "proposal expired"}
    price, ts = market.last_price(p["symbol"])
    if price is None:
        return {"status": "refused", "reason": "no price available"}
    drift = abs(price - p["entry"]) / p["entry"] * 100
    stop_hit = price <= p["stop"] if p["side"] == "BUY" else price >= p["stop"]
    ok, why, _, _, _ = risk_gate(state, p["symbol"], p["side"], p["entry"], p["stop"], p["target"])
    reason = None
    if drift > config.ENTRY_TOLERANCE_PCT:
        reason = f"price moved to {price} ({drift:.2f}% from entry)"
    elif stop_hit:
        reason = f"price {price} is already through the stop"
    elif not ok:
        reason = why
    if reason:
        p["status"] = "skipped_after_approval"
        tg.send(f"[MOCK] Skipped {p['symbol']} even though you said YES: {reason}.\n"
                "In plain words: the price moved too much before the order, so it was no longer the trade "
                "you approved. Nothing was bought or sold.")
        journal("skipped_after_approval", proposal_id=pid, symbol=p["symbol"], setup=p["setup"], note=reason)
        return {"status": "skipped", "reason": reason}

    fill = slip(price, p["side"], opening=True)
    state["balance"] -= costs(fill * p["qty"])
    state["realized_today"] -= costs(fill * p["qty"])
    check_loss_limit(state)
    state["trades_today"] += 1
    sec = sectors.sector(p["symbol"])
    state.setdefault("sector_trades", {})[sec] = state["sector_trades"].get(sec, 0) + 1
    state["positions"][p["symbol"]] = {
        "side": p["side"], "qty": p["qty"], "fill_price": fill, "stop": p["stop"],
        "target": p["target"], "opened_at": _now(), "setup": p["setup"], "proposal_id": pid,
    }
    p["status"] = "filled"
    tg.edit(p["message_id"], p["text"] + f"\n\n-> Placed @ {fill:,.2f}")
    tg.send(f"Placed [MOCK] {p['side']} {p['symbol']} {p['qty']} @ {fill:,.2f}. "
            f"SL {p['stop']:,.2f}, TGT {p['target']:,.2f}.\n"
            f"In plain words: you now {'own' if p['side'] == 'BUY' else 'are short'} {p['qty']} {p['symbol']} "
            f"shares. It closes by itself at Rs {p['target']:,.2f} (profit) or Rs {p['stop']:,.2f} "
            f"(small loss), or at 15:10 whatever the price. Send EXIT {p['symbol']} to close earlier.\n"
            f"{ledger_line(state)}")
    journal("filled", proposal_id=pid, risk_rs=p["risk_rs"], fees_rs=costs(fill * p["qty"]),
            symbol=p["symbol"], setup=p["setup"], side=p["side"], entry=p["entry"],
            stop=p["stop"], target=p["target"], qty=p["qty"], fill_price=fill, user_reply="YES",
            note=f"quote time {ts}")
    return {"status": "filled", "fill_price": fill, "qty": p["qty"]}


# ------------------------------------------------------------------ exits --
def close(state, symbol, exit_price, reason, notify=True):
    pos = state["positions"].pop(symbol, None)
    if not pos:
        return {"status": "no_position"}
    exit_fill = slip(exit_price, pos["side"], opening=False)
    direction = 1 if pos["side"] == "BUY" else -1
    gross = (exit_fill - pos["fill_price"]) * pos["qty"] * direction
    net = round(gross - costs(exit_fill * pos["qty"]), 2)
    state["balance"] += net
    state["realized_today"] += net
    journal("squared_off" if reason == "square_off" else "exited", proposal_id=pos.get("proposal_id", ""),
            fees_rs=costs(exit_fill * pos["qty"]), symbol=symbol,
            setup=pos["setup"], side=pos["side"], qty=pos["qty"], fill_price=pos["fill_price"],
            exit_price=exit_fill, exit_reason=reason, pnl_rs=net)
    if notify:
        tg.send(f"[MOCK] Closed {symbol} ({reason}) @ {exit_fill:,.2f}. "
                f"P&L {inr(net, sign=True)}.\n"
                f"In plain words: {EXIT_PLAIN.get(reason, 'It was closed.')} "
                f"{'You made' if net > 0 else 'You lost'} {inr(abs(net))} on this trade, after fees.\n"
                f"{ledger_line(state)}")
    check_loss_limit(state)
    record_result(state, net)
    return {"status": "closed", "symbol": symbol, "exit_price": exit_fill, "pnl_rs": net}


def monitor(state):
    """Check stops/targets, EXIT commands, and square-off time. Call every few minutes."""
    events = []
    tg.poll(state, wait=0)
    answer_requests(state)
    for sym in list(dict.fromkeys(state.get("exit_requests", []))):
        if sym in state["positions"]:
            price, _ = market.last_price(sym)
            if price is not None:
                events.append(close(state, sym, price, "user_exit"))
    state["exit_requests"] = []

    for sym, pos in list(state["positions"].items()):
        opened = pos["opened_at"][:16]
        bars = [b for b in market.candles(sym, "1m", 1) if b["time"] > opened]
        hit = None
        for b in bars:
            if pos["side"] == "BUY":
                if b["low"] <= pos["stop"]:
                    hit = (min(b["open"], pos["stop"]), "stop_loss")
                elif b["high"] >= pos["target"]:
                    hit = (max(b["open"], pos["target"]), "target")
            else:
                if b["high"] >= pos["stop"]:
                    hit = (max(b["open"], pos["stop"]), "stop_loss")
                elif b["low"] <= pos["target"]:
                    hit = (min(b["open"], pos["target"]), "target")
            if hit:
                break
        if hit:
            events.append(close(state, sym, hit[0], hit[1]))

    if past_square_off() and state["positions"]:
        for sym in list(state["positions"]):
            price, _ = market.last_price(sym)
            if price is not None:
                events.append(close(state, sym, price, "square_off"))

    for pid, p in state["proposals"].items():
        if p["status"] == "pending" and _expired(p):
            p["status"] = "expired"
            tg.edit(p["message_id"], p["text"] + "\n\n-> Expired with no reply (3 minutes). Not placed, no money used.")
            journal("expired", proposal_id=pid, symbol=p["symbol"], setup=p["setup"], user_reply="none")
    return {"events": events, "account": account(state)}


# ----------------------------------------------------------------- report --
def _hypothetical(p) -> float:
    """What a rejected/expired proposal would have made, from 1m bars after it was sent."""
    bars = [b for b in market.candles(p["symbol"], "1m", 1) if b["time"] > p["sent_at"][:16]]
    if not bars:
        return 0.0
    direction = 1 if p["side"] == "BUY" else -1
    entry = bars[0]["open"]
    exit_price = bars[-1]["close"]
    for b in bars:
        if b["time"][11:] >= config.SQUARE_OFF:
            exit_price = b["open"]
            break
        stop_hit = b["low"] <= p["stop"] if direction == 1 else b["high"] >= p["stop"]
        tgt_hit = b["high"] >= p["target"] if direction == 1 else b["low"] <= p["target"]
        if stop_hit:
            exit_price = p["stop"]
            break
        if tgt_hit:
            exit_price = p["target"]
            break
    return round((exit_price - entry) * p["qty"] * direction - 2 * config.BROKERAGE_PER_ORDER, 2)


def report(state, send=True):
    rows = storage.read_rows(f"journal/{_today()}.csv")
    count = lambda e: sum(1 for r in rows if r["event"] == e)
    closed = [r for r in rows if r["event"] in ("exited", "squared_off")]
    pnls = [float(r["pnl_rs"]) for r in closed]
    wins = [x for x in pnls if x > 0]
    passed = [(pid, p) for pid, p in state["proposals"].items() if p["status"] in ("rejected", "expired")]
    hypo = {}
    for pid, p in passed:
        pnl = _hypothetical(p)
        hypo[f"{p['symbol']} ({p['status']})"] = pnl
        # Fixed event_id: re-running the report doesn't double-count in Databricks.
        journal("passed_outcome", event_id=f"passed-{pid}", proposal_id=pid, symbol=p["symbol"],
                setup=p["setup"], side=p["side"], qty=p["qty"], risk_rs=p["risk_rs"], pnl_rs=pnl,
                user_reply=p["status"], note="what this skipped proposal would have made")
    summary = {
        "date": _today(),
        "signals_blocked_by_risk_gate": count("blocked_by_risk_gate"),
        "proposed": count("proposed"),
        "rejected": count("rejected"),
        "expired": count("expired"),
        "skipped_after_approval": count("skipped_after_approval"),
        "trades_closed": len(closed),
        "wins": len(wins),
        "win_rate_pct": round(len(wins) / len(pnls) * 100, 1) if pnls else None,
        "realized_today_rs": round(state["realized_today"], 2),
        "balance_rs": round(state["balance"], 2),
        "total_pnl_since_start_rs": round(state["balance"] - state["starting_capital"], 2),
        "best_trade": max(closed, key=lambda r: float(r["pnl_rs"]))["symbol"] if closed else None,
        "worst_trade": min(closed, key=lambda r: float(r["pnl_rs"]))["symbol"] if closed else None,
        "passed_trades_hypothetical_pnl": hypo,
        "open_positions": list(state["positions"].keys()),
    }
    storage.put_json(f"reports/{_today()}.json", summary)
    if send:
        lines = [
            f"[MOCK] Day report {summary['date']}",
            f"P&L today: {inr(summary['realized_today_rs'], sign=True)}",
            f"Balance: {inr(summary['balance_rs'])} (total {inr(summary['total_pnl_since_start_rs'], sign=True)}"
            f" since start of {inr(state['starting_capital'])})",
            f"In use: {inr(margin_used(state))} | free: {inr(state['balance'] - margin_used(state))}",
            f"Trades: {summary['trades_closed']} closed, {summary['wins']} wins"
            + (f" ({summary['win_rate_pct']}%)" if summary["win_rate_pct"] is not None else ""),
            f"Proposed {summary['proposed']} | rejected {summary['rejected']} | expired {summary['expired']}"
            f" | blocked {summary['signals_blocked_by_risk_gate']}",
        ]
        if hypo:
            lines.append(f"Passed trades would have made: Rs {sum(hypo.values()):,.0f}")
        if summary["open_positions"]:
            lines.append(f"WARNING still open: {', '.join(summary['open_positions'])}")
        tg.send("\n".join(lines))
    return summary


# ---------------------------------------------------------- day / session --
def start_day(state, plan, focus):
    """Apply the pre-market plan: risk per trade for today (never above the default) and focus list."""
    risk = min(float(plan.get("risk_per_trade_pct") or config.RISK_PER_TRADE_PCT), config.RISK_PER_TRADE_PCT)
    state["day_risk_pct"] = risk
    state["plan"] = plan
    state["focus"] = [f.upper() for f in focus]
    storage.put_json(f"plans/{_today()}.json", {"plan": plan, "focus": state["focus"]})
    journal("day_plan", note=f"risk {risk}% | bias {plan.get('bias')} | focus {','.join(state['focus'])}")
    return {"risk_per_trade_pct": risk, "focus": state["focus"], "bias": plan.get("bias")}


def heartbeat(phase="session"):
    storage.put_json("heartbeat.json", {"time": _now(), "phase": phase})


def session_alive(max_age_min=10) -> bool:
    hb = storage.get_json("heartbeat.json")
    if not hb:
        return False
    t = datetime.strptime(hb["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=market.IST)
    return market.now_ist() - t < timedelta(minutes=max_age_min)


def session_active(max_age_min=10) -> bool:
    """A trading session is live (fresh heartbeat) or just started (claimed minutes ago, still
    installing). While it is, only the session may read Telegram updates."""
    if session_alive(max_age_min):
        return True
    for phase in ("morning", "afternoon"):
        claim = storage.get_json(f"sessions/{_today()}/{phase}")
        if claim:
            t = datetime.strptime(claim["claimed_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=market.IST)
            if market.now_ist() - t < timedelta(minutes=max_age_min):
                return True
    return False


def claim_session(phase) -> bool:
    """True the first time a phase is claimed today; False if it already ran (duplicate trigger)."""
    key = f"sessions/{_today()}/{phase}"
    if storage.exists(key):
        return False
    storage.put_json(key, {"claimed_at": _now()})
    return True


def report_exists() -> bool:
    return storage.exists(f"reports/{_today()}.json")
