"""Paper-trading engine: dummy-money ledger, risk gate, approvals, fills, exits, reports.

No broker or bank is ever contacted. Prices come from `market`, approvals from `telegram_bot`.
"""
import math
import time
import uuid
from datetime import datetime, timedelta

import advisor
import config
import exits
import track
import links
import market
import news
import pools
import reviewer
import sectors
import storage
import telegram_bot as tg
from http_client import log

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


def _roll_week(state: dict) -> None:
    """Add the finished day's result to this week's; start a new week (brake off) on a new Monday."""
    now = market.now_ist()
    monday = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
    week = state.get("week") or {}
    previous_day = state.get("day") or ""
    if week.get("start") == monday:
        if previous_day >= monday:
            week["realized"] = round(week.get("realized", 0.0) + state.get("realized_today", 0.0), 2)
    else:
        # A new week, or the first run after this was added mid-week: count the week's earlier days from the
        # journals so the weekly brake sees them.
        earlier = sum(_journal_realized(d) for d in _week_days_before_today(monday))
        week = {"start": monday, "realized": round(earlier, 2), "brake": False}
    state["week"] = week


def _week_days_before_today(monday: str):
    start = datetime.strptime(monday, "%Y-%m-%d")
    today = _today()
    return [d for d in ((start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)) if d < today]


def _journal_realized(day: str) -> float:
    """A day's realized P&L from its journal, the way realized_today moves: entry fees at the fill, net P&L
    (after the exit fee) at the exit."""
    total = 0.0
    for r in storage.read_rows(f"journal/{day}.csv"):
        if r.get("event") == "filled":
            total -= float(r.get("fees_rs") or 0)
        elif r.get("event") in ("exited", "squared_off"):
            total += float(r.get("pnl_rs") or 0)
    return total


def _new_day(state: dict) -> None:
    _roll_week(state)
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
    # Proposal messages of the last 7 days, so a 👍/👎 reaction on an older message still finds its trade
    week_ago = (market.now_ist() - timedelta(days=RATING_DAYS)).strftime("%Y-%m-%d")
    state["msg_index"] = {k: v for k, v in (state.get("msg_index") or {}).items() if v.get("day", "") >= week_ago}
    state["decisions"] = {}
    state["exit_requests"] = []
    state["requests"] = []
    state["day_risk_pct"] = config.RISK_PER_TRADE_PCT
    state["plan"] = None
    state["focus"] = []
    state["focus_news"] = []
    state["news_symbols"] = []
    state["news_seen"] = []
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
    return market.is_trading_day(now) and _at(config.ENTRY_START) <= now <= _at(config.ENTRY_END)


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
        f"Loss limit today: -{inr(loss_limit_rs(state))} ({config.DAILY_LOSS_LIMIT_R:g} trades' risk of "
        f"{inr(one_r(state))}) | room left {inr(max(0.0, loss_room(state)))}",
        f"This week:        {inr(week_realized(state), sign=True)}"
        + (" | weekly brake ON: half-size trades until Monday" if weekly_brake_on(state) else
           f" (brake at -{inr(weekly_limit_rs(state))})"),
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
/news SYMBOL  the company's latest NSE filings with links, e.g. /news RELIANCE
/portfolio  all dummy pools: intraday, swing and long-term (Rs 2 lakh in total)
/shadow  everything tested on paper: exit rules, background rules, skipped trades, news vs watchlist picks, the swing and long-term shadow pools
/stop  kill switch: no new proposals at all until /resume (open positions still managed)
/pause  no new proposals for the day; open positions are still managed. After 14:30, at weekends or on holidays it applies to the next trading day
/resume  allow new proposals again (undoes /pause and /stop)
/auto on | /auto off  auto mode: trades with a suggestion over 55% YES (higher for losing groups) are placed without asking you (you rate them with 👍/👎); off = every trade needs your YES
YES / NO  answer the latest proposal (or tap its buttons)
/help  this list (also /prompts, /commands, or ask "what can you do?")

On its own (Mon-Fri IST): 09:08 day plan; proposals until 14:30 (auto mode: placed if 55%+ YES; otherwise each needs your YES within 3 min); stops and targets watched every 5 min; 15:10 square-off; ~15:20 day report; 15:45 journal to the lakehouse; Friday ~17:55 weekly report.
During sessions (09:08-15:25 IST) I reply within seconds; at other times within about 2 minutes. Dummy money only."""


def shadow_text(state) -> str:
    """/shadow: every rule being tested on paper, in one message."""
    import swing_shadow
    import weekly

    w = weekly.build(market.now_ist().date())
    lines = ["[MOCK] Tested on paper (nothing here touched real trades), this week so far:", ""]
    lines += weekly.paper_lines(w) or ["- Nothing to compare yet this week."]
    lines += ["", "Shadow pools (since they started):"]
    lines += [f"- {x}" for x in swing_shadow.lines(swing_shadow.summary())]
    lt = storage.get_json("shadow/longterm/latest.json") or {}
    if lt:
        lines.append(f"- Long-term (weekly quality picks), week {lt.get('week')}: {inr(lt['equity'] - config.LONG_TERM_CAPITAL, sign=True)} "
                     f"({lt['return_pct']:+.2f}%)"
                     + (f" vs NIFTYBEES {lt['nifty_return_pct']:+.2f}%" if lt.get("nifty_return_pct") is not None else "")
                     + "; review on 18 Dec.")
    lines += ["", "In Databricks: gold.shadow_pools_daily (pools vs NIFTYBEES), gold.track_record "
              "(dimension 'source': news vs watchlist; 'decision': skipped trades), gold.swing_signals."]
    return "\n".join(lines)


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
    """Today if entries are still possible today, else the next trading day (weekends and NSE holidays skipped)."""
    now = market.now_ist()
    if market.is_trading_day(now) and now <= _at(config.ENTRY_END):
        return now.strftime("%Y-%m-%d")
    return market.next_trading_day_after(now).strftime("%Y-%m-%d")


def is_paused(state) -> bool:
    return bool(state.get("stopped")) or state.get("paused_for") == _today()


def news_text(symbol) -> str:
    symbol = symbol.upper()
    found = news.for_symbol(symbol, limit=6)
    if found is None:
        return f"[MOCK] News for {symbol}: the NSE feed isn't reachable right now. Try again in a while."
    if not found:
        return f"[MOCK] No NSE filings for {symbol} today or on the last trading day."
    lines = [f"[MOCK] {symbol}: NSE filings, newest first"]
    lines += [f"- {news.describe(i)}\n  {i.get('link', '')}".rstrip() for i in found]
    return "\n".join(lines)


def portfolio_text(state) -> str:
    intraday = state["balance"]
    pnl = intraday - state["starting_capital"]
    total, start = intraday, state["starting_capital"]
    lines = ["[MOCK] Dummy portfolio",
             f"Intraday:  {inr(intraday)} (started {inr(state['starting_capital'])}, {inr(pnl, sign=True)}), live"]
    for label, eq, cap, status in pools.summary_lines(state):
        lines.append(f"{(label + ':').ljust(10)} {inr(eq)} of {inr(cap)}, {status}")
        total, start = total + eq, start + cap
    lines.append(f"Total:     {inr(total)} of {inr(start)} ({inr(total - start, sign=True)})")
    if pools.breaker_on():
        lines.append("Circuit breaker ON: total value fell 12% below its peak. Send /resume after reviewing.")
    if state.get("stopped"):
        lines.append("Stopped with /stop: send /resume to restart.")
    return "\n".join(lines)


def data_health(state, nifty) -> None:
    """Flag stale prices during market hours (blocks new proposals) and tell the user once per episode;
    also warn once a day if the news feed is more than 90 minutes behind."""
    now = market.now_ist()
    t = now.strftime("%H:%M")
    live = market.is_trading_day(now) and "09:30" <= t <= "15:25"
    stale = False
    if live:
        last = (nifty or {}).get("time")
        stale = (not last) or market.is_stale(last, config.DATA_STALE_MIN + 5)
    if stale and not state.get("data_stale"):
        tg.send(f"[MOCK] Data alert: market prices look stale (last Nifty candle "
                f"{(nifty or {}).get('time', 'missing')}). No new proposals until fresh prices arrive; "
                "open positions are still checked.")
        journal("data_alert", note=f"stale prices, last Nifty candle {(nifty or {}).get('time')}")
    elif not stale and state.get("data_stale"):
        tg.send("[MOCK] Prices are fresh again; proposals allowed.")
    state["data_stale"] = stale
    if live and state.get("news_alert_day") != _today():
        age = news.feed_age_min()
        if age is None or age > 20:   # fetched every minute in market hours
            state["news_alert_day"] = _today()
            tg.send("[MOCK] Data alert: the company-news feed is "
                    + ("unreachable" if age is None else f"{age} minutes old")
                    + ". Proposals will say 'news unavailable' until it catches up.")
            journal("data_alert", note=f"news feed age {age}")


def auto_on(state) -> bool:
    return bool(state.get("auto_mode", config.AUTO_MODE_DEFAULT))


def trade_cap(state) -> int:
    return config.AUTO_MAX_TRADES_PER_DAY if auto_on(state) else config.MAX_TRADES_PER_DAY


def set_auto(state, on: bool, reason="by you") -> None:
    state["auto_mode"] = on
    journal("auto_mode_on" if on else "auto_mode_off", note=reason)


RATING_DAYS = 7  # how long a proposal message can still be rated by a reaction


def rating_buttons(state, pid, rating=None):
    """👍/👎 buttons for an auto-mode proposal, the chosen one ticked, plus EXIT while its trade is open."""
    mark = lambda kind, label: ("✅ " if rating == kind else "") + label  # noqa: E731
    buttons = [(mark("like", "👍 good call"), f"fb:like:{pid}"), (mark("dislike", "👎 bad call"), f"fb:dislike:{pid}")]
    if any(pos.get("proposal_id") == pid for pos in state.get("positions", {}).values()):
        buttons.append(("EXIT", f"x:{pid}"))
    symbol = (state["proposals"].get(pid) or {}).get("symbol") or next(
        (v.get("symbol") for v in (state.get("msg_index") or {}).values() if v.get("pid") == pid), "")
    return [buttons, links.buttons(symbol, pid)] if symbol else buttons


def record_feedback(state) -> int:
    """Journal the user's 👍/👎 (buttons or reactions; 'none' when a reaction is removed). Feedback never changes
    a trade. Works for proposals of earlier days too (the journal row carries the proposal_id)."""
    taps = state.pop("feedback", {}) or {}
    for pid, kind in taps.items():
        p = state["proposals"].get(pid) or next(
            (v for v in (state.get("msg_index") or {}).values() if v.get("pid") == pid), {})
        if pid in state["proposals"]:
            state["proposals"][pid]["feedback"] = None if kind == "none" else kind
        journal("user_feedback", event_id=f"fb-{pid}", proposal_id=pid, symbol=p.get("symbol", ""),
                setup=p.get("setup", ""), side=p.get("side", ""), user_reply=kind,
                note=f"decision {p.get('user_reply', '?')} at suggestion {p.get('suggested_yes_pct', '?')}%")
        # Show the saved rating on the message itself: the chosen button gets a ✅ (none after a removal)
        msg_id = (state["proposals"].get(pid) or {}).get("message_id") or next(
            (int(k) for k, v in (state.get("msg_index") or {}).items() if v.get("pid") == pid), None)
        if msg_id:
            tg.set_buttons(msg_id, rating_buttons(state, pid, None if kind == "none" else kind))
    return len(taps)


def _message_of(state, pid):
    return (state["proposals"].get(pid) or {}).get("message_id") or next(
        (int(k) for k, v in (state.get("msg_index") or {}).items() if v.get("pid") == pid), None)


def _full_reasons(state, pid) -> str:
    """The long explanation behind a proposal's Full reasons button (today's from state, older from S3)."""
    text = (state["proposals"].get(pid) or {}).get("full_text")
    if not text:
        day = next((v.get("day") for v in (state.get("msg_index") or {}).values() if v.get("pid") == pid), "")
        text = storage.backend().get(f"reasons/{day}/{pid}.txt") if day else None
    return text or "[MOCK] The full reasons for that proposal aren't available any more (kept for 7 days)."


def answer_requests(state) -> int:
    """Reply to Telegram commands that came in (ledger, help, plan, pause, resume, auto)."""
    record_feedback(state)
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
            stopped = state.pop("stopped", None)
            breaker = pools.reset_breaker()
            journal("resumed_by_user", note="circuit breaker reset" if breaker else "")
            tg.send("[MOCK] Resumed: proposals allowed again"
                    + (" (portfolio circuit breaker reset)." if breaker else ".")
                    if (was or stopped or breaker) else "[MOCK] Nothing was paused.")
        elif ask.startswith("info:"):
            tg.send(_full_reasons(state, ask[5:]), reply_to=_message_of(state, ask[5:]))
        elif ask == "stop":
            state["stopped"] = True
            journal("stopped_by_user")
            tg.send("[MOCK] Stopped: no new proposals in any pool until you send /resume, today or any later day. "
                    "Open positions are still watched and closed by 15:10.")
        elif ask in ("auto_on", "auto_off"):
            on = ask == "auto_on"
            set_auto(state, on)
            tg.send(f"[MOCK] Auto mode ON: proposals with a suggestion of {config.AUTO_MIN_YES_PCT:g}% YES or more are "
                    f"placed straight away (max {config.AUTO_MAX_TRADES_PER_DAY} trades a day); the rest are skipped. "
                    "Rate each with 👍/👎, or tap EXIT to close one. Every limit still applies. /auto off to approve "
                    "each trade yourself." if on else
                    "[MOCK] Auto mode OFF: every trade needs your YES again (3 minutes to answer).")
        elif ask == "auto_status":
            tg.send(f"[MOCK] Auto mode is {'ON' if auto_on(state) else 'OFF'}. Send /auto on or /auto off.")
        elif ask == "portfolio":
            tg.send(portfolio_text(state))
        elif ask == "shadow":
            tg.send(shadow_text(state))
        elif ask.startswith("news:"):
            tg.send(news_text(ask.split(":", 1)[1]))
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
        "one_r_rs": round(one_r(state), 2),
        "daily_loss_limit_rs": round(loss_limit_rs(state), 2),
        "open_risk_rs": round(open_risk(state), 2),
        "loss_room_rs": round(loss_room(state), 2),
        "week_realized_rs": round(week_realized(state), 2),
        "weekly_brake_on": weekly_brake_on(state),
        "focus": state.get("focus", []),
        "paused_by_user": is_paused(state),
        "carried_over_warning": state.get("carried_over_warning"),
    }


# -------------------------------------------------------------- risk gate --
def one_r(state) -> float:
    """1R: one trade's planned risk today (start-of-day balance x today's risk %, which follows VIX).
    The weekly brake halves the risk actually taken, not this unit, so the limits don't shrink with it."""
    return state["day_start_balance"] * state.get("day_risk_pct", config.RISK_PER_TRADE_PCT) / 100


def loss_limit_rs(state) -> float:
    if config.DAILY_LOSS_LIMIT_RS > 0:
        return config.DAILY_LOSS_LIMIT_RS
    if config.DAILY_LOSS_LIMIT_R > 0:
        return config.DAILY_LOSS_LIMIT_R * one_r(state)
    return state["day_start_balance"] * config.DAILY_LOSS_LIMIT_PCT / 100


def open_risk(state) -> float:
    """What the open positions lose if every stop is hit (from the fill price; 0 once a stop is past entry)."""
    total = 0.0
    for p in state["positions"].values():
        direction = 1 if p["side"] == "BUY" else -1
        total += max(0.0, (p["fill_price"] - p["stop"]) * direction * p["qty"])
    return total


def loss_room(state) -> float:
    """How much more can be put at risk today: the limit minus today's loss and the open positions' risk."""
    return loss_limit_rs(state) - max(0.0, -state["realized_today"]) - open_risk(state)


def week_realized(state) -> float:
    return (state.get("week") or {}).get("realized", 0.0) + state["realized_today"]


def weekly_limit_rs(state) -> float:
    return config.WEEKLY_LOSS_LIMIT_R * one_r(state)


def weekly_brake_on(state) -> bool:
    return bool((state.get("week") or {}).get("brake"))


def check_weekly_brake(state) -> None:
    """Halve risk per trade for the rest of the week once the week's loss reaches WEEKLY_LOSS_LIMIT_R."""
    if config.WEEKLY_LOSS_LIMIT_R <= 0 or weekly_brake_on(state):
        return
    if -week_realized(state) < weekly_limit_rs(state):
        return
    state.setdefault("week", {})["brake"] = True
    pct = round(config.WEEKLY_BRAKE_FACTOR * 100)
    tg.send(f"[MOCK] Weekly brake: this week is {inr(week_realized(state), sign=True)}, past the weekly limit of "
            f"-{inr(weekly_limit_rs(state))}. Until Monday each trade risks {pct}% of the usual amount.\n"
            "In plain words: after a bad week, smaller bets until the new week starts, so a losing run can't "
            "dig a deep hole.")
    journal("weekly_brake", pnl_rs=round(week_realized(state), 2),
            note=f"risk per trade x{config.WEEKLY_BRAKE_FACTOR} until Monday")
    if auto_on(state):
        set_auto(state, False, reason="weekly brake")
        tg.send("[MOCK] Auto mode is now OFF because of the weekly brake: each trade needs your YES until you send "
                "/auto on.")


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


# ------------------------------------------------------------ news radar --
UNIVERSE_KEY = "state/universe.json"
_universe = {"date": None, "symbols": frozenset()}


def save_universe(plan) -> None:
    """Keep today's tradable Nifty 200 names and the pre-market news radar (from premarket_context)."""
    symbols = sorted(plan.get("tradable_universe") or [])
    storage.put_json(UNIVERSE_KEY, {"date": _today(), "symbols": symbols,
                                    "names": plan.get("tradable_names") or {},
                                    "radar": [r["symbol"] for r in plan.get("news_radar") or []]})
    _universe.update(date=_today(), symbols=frozenset(symbols))
    links.reset_cache()


def universe() -> frozenset:
    """Today's tradable names beyond the watchlist (the last saved list if today's isn't there yet)."""
    if _universe["date"] != _today():
        u = storage.get_json(UNIVERSE_KEY) or {}
        _universe.update(date=_today(), symbols=frozenset(u.get("symbols") or []))
    return _universe["symbols"]


def tradable(symbol) -> bool:
    return symbol.upper() in sectors.WATCHLIST or symbol.upper() in universe()


def price_frozen(symbol) -> bool:
    """True when the last 10 one-minute candles have no range at all (likely locked at a price limit)."""
    try:
        bars = market.candles(symbol, "1m", 1)[-10:]
    except Exception:  # noqa: BLE001 - can't tell: don't block on it
        return False
    return len(bars) >= 10 and all(b["high"] == b["low"] for b in bars) and len({b["close"] for b in bars}) == 1


def is_news_pick(state, symbol) -> bool:
    return symbol in (state.get("news_symbols") or []) or symbol not in sectors.WATCHLIST


def news_alerts(state, limit=8) -> list:
    """New NSE filings (results, orders, mergers, ratings...) on tradable stocks since the last check. Each one's
    stock joins the scan (up to NEWS_FOCUS_MAX extra names, newest first) and its trades are labelled 'news'."""
    items = news.items() or []
    seen = set(state.get("news_seen") or [])
    today = _today()
    relevant = [i for i in items
                if (i.get("symbol") or "") and tradable(i["symbol"])
                and i.get("category") in config.NEWS_RADAR_CATEGORIES
                and (i.get("published_ist") or i.get("fetched_at_ist") or "").startswith(today)]
    fresh = [i for i in relevant if i.get("id") not in seen]
    state["news_seen"] = [i.get("id") for i in relevant]   # today's relevant filings only: small, never truncated
    out = []
    for i in fresh[:limit]:
        sym = i["symbol"].upper()
        if sym not in state.setdefault("news_symbols", []):
            state["news_symbols"].append(sym)
        extra = [s for s in state.setdefault("focus_news", []) if s != sym]
        if sym not in (state.get("focus") or []):
            state["focus_news"] = ([sym] + extra)[:config.NEWS_FOCUS_MAX]
        out.append({"symbol": sym, "category": i.get("category"), "when": news.when(i), "text": news.describe(i)})
    return out


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
    if not tradable(symbol):
        return False, (f"{symbol} isn't on the tradable list: the 36-stock watchlist, or a Nifty 200 stock that "
                       "passes the guardrails (main board, Rs 50+, Rs 50 crore+ traded a day)"), 0, 0, rr
    if entry < config.MIN_STOCK_PRICE:
        return False, f"price under Rs {config.MIN_STOCK_PRICE:g}: no penny stocks", 0, 0, rr
    if symbol not in sectors.WATCHLIST and price_frozen(symbol):
        return False, (f"{symbol}'s price hasn't moved for 10 minutes: it may be stuck at its daily price limit, "
                       "where you can't get out"), 0, 0, rr
    if state.get("stopped"):
        return False, "stopped by you with /stop (send /resume to restart)", 0, 0, rr
    if pools.breaker_on():
        return False, ("portfolio circuit breaker: total value is 12% below its peak; send /resume after "
                       "reviewing"), 0, 0, rr
    if is_paused(state):
        return False, "paused by you on Telegram (send /resume)", 0, 0, rr
    if state.get("data_stale"):
        return False, "prices look stale right now; no new proposals until they're fresh", 0, 0, rr
    if not in_entry_window():
        return False, f"outside entry window {config.ENTRY_START}-{config.ENTRY_END} IST (weekdays)", 0, 0, rr
    if symbol in state["positions"]:
        return False, f"already holding {symbol}", 0, 0, rr
    if len(state["positions"]) >= config.MAX_OPEN_POSITIONS:
        return False, "max open positions reached", 0, 0, rr
    if 0 < trade_cap(state) <= state["trades_today"]:
        return False, (f"all {trade_cap(state)} trades for today are used"
                       + (" (auto mode)" if auto_on(state) else "")), 0, 0, rr
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
    full_budget = risk_budget
    if weekly_brake_on(state):
        risk_budget *= config.WEEKLY_BRAKE_FACTOR
    room = loss_room(state)
    if room < per_share:
        return False, (f"no room left under the daily loss limit (-{inr(loss_limit_rs(state))}): today "
                       f"{inr(state['realized_today'], sign=True)} plus {inr(open_risk(state))} at risk in open "
                       "positions"), 0, 0, rr
    risk_budget = min(risk_budget, room)
    qty = math.floor(risk_budget / per_share)
    free = state["balance"] - margin_used(state)
    max_qty_by_margin = math.floor(free * config.INTRADAY_LEVERAGE / entry)
    cap_margin = state["balance"] * config.MAX_POSITION_MARGIN_PCT / 100
    max_qty_by_cap = math.floor(cap_margin * config.INTRADAY_LEVERAGE / entry)
    qty = min(qty, max_qty_by_margin, max_qty_by_cap)
    if qty < 1:
        return False, "not enough free margin for even 1 share", 0, 0, rr
    # Judge the stop as if the trade were full size: a trade made smaller by the weekly brake or the room left
    # under the limit pays the same Rs 20 per order, but that's the price of the brake, not a too-tight stop.
    full_qty = max(1, min(math.floor(full_budget / per_share), max_qty_by_cap))
    share = cost_share_pct(entry, per_share, full_qty)
    if config.MAX_COST_SHARE_PCT > 0 and share > config.MAX_COST_SHARE_PCT:
        return False, (f"fees and slippage would be {share:.0f}% of the risk (max {config.MAX_COST_SHARE_PCT:g}%): "
                       "the stop is too tight for the costs"), 0, 0, rr
    return True, "ok", qty, round(qty * per_share, 2), rr


def cost_share_pct(entry, per_share, qty) -> float:
    """Round-trip costs (2 orders' brokerage + slippage on both fills) as a % of the trade's planned risk."""
    costs_rs = 2 * config.BROKERAGE_PER_ORDER + 2 * entry * qty * config.SLIPPAGE_PCT / 100
    return costs_rs / (qty * per_share) * 100


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


def action_word(side) -> str:
    """BUY stays BUY; a SELL you don't own is a SHORT (sell first, buy back the same day)."""
    return "BUY" if side == "BUY" else "SHORT"


def _short(text, limit=110) -> str:
    return reviewer.shorten(" ".join(str(text or "").split()), limit)


def compact_proposal(*, auto, place, symbol, side, setup, entry, stop, target, qty, risk_rs, rr, risk_pct, need,
                     free, advice, yes, ai_yes_pct, ai_reason, review, reasons, summary, invalid_if, skip_why, bar,
                     bar_why, track_line, valid_until, news_pick=False) -> str:
    """The Telegram proposal: every number, reasons in one line each. The long version is behind Full reasons."""
    buy = side == "BUY"
    status = "your call" if not auto else "PLACING" if place else "SKIPPED"
    agent = f"agent {int(round(float(ai_yes_pct)))}" if ai_yes_pct is not None else "agent -"
    rev = f"reviewer {review['verdict']} {review['yes_pct']}" if review else "reviewer unavailable"
    if not auto:
        decide = f"You decide: YES or NO by {valid_until:%H:%M:%S}"
    elif place:
        decide = f"Auto: placing it (over {bar:g}%{bar_why}, agent over {config.AUTO_MIN_AGENT_PCT:g}%, reviewer checked)"
    else:
        decide = f"Auto: skipped, {skip_why}"
    gain = abs(target - entry) * qty
    against = next((r for r in reasons if str(r).lower().startswith("risk")), "")
    against = against.split(":", 1)[1] if ":" in against[:6] else against
    pros = [r for r in reasons if r != against and not str(r).lower().startswith("risk")]
    lines = [
        f"[MOCK] {'🔺' if buy else '🔻'} {'AUTO · ' if auto else ''}{action_word(side)} {symbol} "
        f"(bet on a {'rise' if buy else 'fall'}) · {setup}{' · 📰 news pick' if news_pick else ''}: {status}",
        f"{advice['verdict']} {yes}% · checks {advice['checks_pct']} · {agent} · {rev}",
        decide,
        "",
        (f"Buy {qty} @ ~{entry:,.2f} now, sell by 15:10" if buy
         else f"Sell {qty} @ ~{entry:,.2f} now, buy back by 15:10"),
        f"🎯 {target:,.2f} (+{inr(gain)}) · 🛑 {stop:,.2f} (-{inr(risk_rs)}) · R:R {rr}",
        f"Margin {inr(need)} of {inr(free)} free · risk {risk_pct}%",
        "",
        f"For: {_short(pros[0] if pros else summary)}",
    ]
    if against:
        lines.append(f"Against: {_short(against)}")
    if ai_yes_pct is not None and ai_reason:
        lines.append(f"Agent: {_short(ai_reason)}")
    lines.append(f"Reviewer: {_short(review['why'], 160)}" if review
                 else f"Reviewer: unavailable ({_short(reviewer.unavailable_reason(), 80)})")
    lines.append(advice.get("market_short") or "")
    if track_line:
        lines.append(track_line)
    lines += [news.compact(symbol), f"Exit early if: {_short(invalid_if, 90)}"]
    return "\n".join(x for x in lines if x is not None)


# -------------------------------------------------------------- proposals --
def propose(state, symbol, side, setup, entry, stop, target, reasons, invalid_if, news_note="none", summary="",
            ai_yes_pct=None, ai_reason=""):
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
    advice = advisor.combine(advice, ai_yes_pct, review)
    yes = advice["yes_pct"]
    auto = auto_on(state)
    mood = advice.get("mood")
    hour = market.now_ist().strftime("%H")
    skip_why, bar, bar_why = (_auto_skip_reason(state, symbol, side, setup, mood, hour, yes, ai_yes_pct, review)
                              if auto else ("", None, ""))
    place = auto and not skip_why
    valid_until = market.now_ist() + timedelta(seconds=config.APPROVAL_TIMEOUT_SEC)  # after the review

    action = action_word(side)
    if not auto:
        head = [f"[MOCK] {action} {symbol}  ({setup}): full reasons", advice["lines"][0]]
    elif place:
        head = [f"[MOCK] AUTO · {action} {symbol}  ({setup}): full reasons",
                f"Decision: TRADE at {advice['verdict']} {yes}% (auto mode needs over {bar:g}%{bar_why}, the "
                f"trading agent over {config.AUTO_MIN_AGENT_PCT:g}% and the reviewer's check)."]
    else:
        head = [f"[MOCK] AUTO · {action} {symbol}  ({setup}): full reasons",
                f"Decision: SKIP at {advice['verdict']} {yes}%: {skip_why}."]
    why_proposed = ["Why proposed:"]
    if summary:
        why_proposed.append(f"- {summary.strip()}")
    why_proposed += [f"- {r}" for r in reasons[:3]]
    if news_note and news_note.strip().lower() != "none":
        why_proposed.append(f"- News: {news_note}")
    why_pct = [f"Why {yes}% YES (the average of these scores; not the chance of profit):",
               f"- Live checks {advice['checks_pct']}%: "
               + ("; ".join(advice.get("top_checks") or []) or "no strong signal either way")]
    if ai_yes_pct is not None:
        why_pct.append(f"- Trading agent {int(round(float(ai_yes_pct)))}%: {ai_reason.strip() or 'no reason given'}")
    why_pct.append(f"- Independent reviewer {review['verdict']} {review['yes_pct']}%: {review['why']}" if review
                   else f"- Independent reviewer: unavailable this time, not counted ({reviewer.unavailable_reason()})")
    record = track.lines_for(setup, yes, mood, hour)

    lines = [
        *head, "",
        *why_proposed, "",
        *why_pct,
        *([""] + record if record else []), "",
        *plain_proposal(side, symbol, setup, entry, stop, target, qty, risk_rs, ""), "",
        *advice["market_lines"], "",
        *news.lines_for(symbol), "",
        "Details:",
        f"Entry ~{entry:,.2f} | SL {stop:,.2f} | Target {target:,.2f}",
        f"Qty {qty} | Risk Rs {risk_rs:,.0f} ({risk_pct}%) | R:R {rr}",
        f"Uses {inr(need)} margin of {inr(state['balance'] - margin_used(state))} free",
        f"Invalid if: {invalid_if}",
        (f"Valid until {valid_until.strftime('%H:%M:%S')} IST. Dummy money only." if not auto
         else "Dummy money only."),
    ]
    full_text = "\n".join(lines)
    text = compact_proposal(
        auto=auto, place=place, symbol=symbol, side=side, setup=setup, entry=entry, stop=stop, target=target,
        qty=qty, risk_rs=risk_rs, rr=rr, risk_pct=risk_pct, need=need, free=state["balance"] - margin_used(state),
        advice=advice, yes=yes, ai_yes_pct=ai_yes_pct, ai_reason=ai_reason, review=review, reasons=reasons,
        summary=summary, invalid_if=invalid_if, skip_why=skip_why, bar=bar, bar_why=bar_why,
        track_line=track.compact(setup, yes, mood, hour), valid_until=valid_until,
        news_pick=is_news_pick(state, symbol))
    if not auto:
        first = [("YES - place", f"yes:{pid}"), ("NO - skip", f"no:{pid}")]
    else:
        first = [("👍 good call", f"fb:like:{pid}"), ("👎 bad call", f"fb:dislike:{pid}")]
        if place:
            first.append(("EXIT", f"x:{pid}"))
    msg_id = tg.send(text, buttons=[first, links.buttons(symbol, pid)])
    try:
        storage.backend().put(f"reasons/{_today()}/{pid}.txt", full_text)
    except Exception as err:  # noqa: BLE001 - the full reasons are a convenience; never block a proposal
        log.warning("couldn't save the full reasons: %s", err)
    state["proposals"][pid] = {
        "day": _today(), "symbol": symbol, "side": side, "setup": setup, "entry": entry,
        "stop": stop, "target": target, "qty": qty, "risk_rs": risk_rs, "rr": rr,
        "reasons": reasons[:3], "invalid_if": invalid_if, "status": "pending",
        "sent_at": _now(), "valid_until": valid_until.strftime("%Y-%m-%d %H:%M:%S"),
        "message_id": msg_id, "text": text, "full_text": full_text, "suggested_yes_pct": yes,
        "review": review, "mode": "auto" if auto else "human",
    }
    state.setdefault("msg_index", {})[str(msg_id)] = {"pid": pid, "day": _today(), "symbol": symbol, "setup": setup,
                                                      "side": side, "user_reply": "", "suggested_yes_pct": yes}
    journal("proposed", proposal_id=pid, symbol=symbol, setup=setup, side=side, entry=entry, stop=stop,
            target=target, qty=qty, risk_rs=risk_rs, rr=rr, reasons=" | ".join(reasons[:3]),
            note=(f"suggested {advice['verdict']} {yes}% | checks {advice['checks_pct']}%"
                  f" | agent {ai_yes_pct if ai_yes_pct is not None else '-'}%"
                  f" | reviewer {review['verdict'] + ' ' + str(review['yes_pct']) + '%' if review else '- (' + reviewer.unavailable_reason()[:80].replace('|', '/') + ')'}"
                  f" | mood {mood or '-'} | shadow-block {','.join(advice.get('shadow') or []) or '-'}"
                  f" | mode {'auto' if auto else 'human'}"
                  f" | source {'news' if is_news_pick(state, symbol) else 'watchlist'}"))
    base = {"proposal_id": pid, "qty": qty, "risk_rs": risk_rs, "rr": rr,
            "suggestion": f"{advice['verdict']} {yes}%"}
    p = state["proposals"][pid]
    if auto:
        p["decided_at"] = _now()
        if place:
            p["status"], p["user_reply"] = "approved", "AUTO"
            return {**base, "status": "approved", "auto": True,
                    "next": "auto mode: call execute_approved_trade now (no wait_for_decision needed)"}
        p["status"], p["user_reply"] = "rejected", "AUTO-NO"
        journal("rejected", proposal_id=pid, symbol=symbol, setup=setup, side=side, entry=entry, stop=stop,
                target=target, user_reply="AUTO-NO", note=skip_why)
        return {**base, "status": "skipped_auto", "auto": True, "reason": skip_why,
                "next": "auto mode skipped it; move on"}
    return {**base, "status": "pending", "valid_until": p["valid_until"]}


def _auto_bar(state, symbol, side, setup, mood, hour):
    """(bar, why): the combined suggestion auto mode must beat. Starts at AUTO_MIN_YES_PCT; +5 when this setup,
    mood or hour has a judged, losing track record; at least AUTO_FLIP_MIN_YES_PCT for the opposite side of a
    stock already proposed today (a flip on a choppy day is how you get whipsawed)."""
    bar, why = config.AUTO_MIN_YES_PCT, []
    losing = track.losing(setup, mood, hour)
    if losing:
        bar += config.AUTO_LOSING_GROUP_BUMP
        why.append(f"+{config.AUTO_LOSING_GROUP_BUMP:g}: {' and '.join(losing)} losing")
    flip = any(p.get("day") == _today() and p.get("symbol") == symbol and p.get("side") != side
               for p in state["proposals"].values())
    if flip and bar < config.AUTO_FLIP_MIN_YES_PCT:
        bar = config.AUTO_FLIP_MIN_YES_PCT
        why.append(f"{bar:g}%: flips an earlier {symbol} proposal today")
    return bar, (" (" + "; ".join(why) + ")") if why else ""


def _auto_skip_reason(state, symbol, side, setup, mood, hour, yes, ai_yes_pct, review):
    """(reason, bar, bar_why): why auto mode won't place this trade ('' = place it). All must hold: the combined
    suggestion beats the bar, Claude itself is for it (over 50%, not a coin flip), and the reviewer answered."""
    bar, bar_why = _auto_bar(state, symbol, side, setup, mood, hour)
    if yes <= bar:
        return f"the suggestion needs to be over {bar:g}%{bar_why}", bar, bar_why
    if ai_yes_pct is None or float(ai_yes_pct) <= config.AUTO_MIN_AGENT_PCT:
        return (f"the trading agent itself isn't for it ({ai_yes_pct if ai_yes_pct is not None else 'no'}%, "
                f"needs over {config.AUTO_MIN_AGENT_PCT:g}%)"), bar, bar_why
    if config.AUTO_NEEDS_REVIEWER and review is None:
        return "the independent reviewer didn't answer, and auto mode needs its check", bar, bar_why
    return "", bar, bar_why


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
        heartbeat()
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
                tg.edit(p["message_id"], p["text"] + "\n\n-> Skipped (NO). Not placed, no money used.",
                        buttons=[links.buttons(p["symbol"], pid)])
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
    keep = (rating_buttons(state, pid, p.get("feedback")) if p.get("mode") == "auto"
            else [links.buttons(p["symbol"], pid)])  # auto: rating and EXIT buttons; always the links
    tg.edit(p["message_id"], p["text"] + f"\n\n-> Placed @ {fill:,.2f}", buttons=keep)
    if p["side"] == "BUY":
        did = f"Bought {p['qty']} {p['symbol']} @ {fill:,.2f}: will sell by 15:10"
    else:
        did = f"Shorted {p['qty']} {p['symbol']} @ {fill:,.2f}: sold first, will buy back by 15:10"
    tg.send(f"[MOCK] {did}.\n🎯 {p['target']:,.2f} · 🛑 {p['stop']:,.2f} · EXIT {p['symbol']} closes it now.\n"
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
        did = (f"Sold {pos['qty']} {symbol}" if pos["side"] == "BUY" else f"Bought back {pos['qty']} {symbol}")
        tg.send(f"[MOCK] {did} @ {exit_fill:,.2f} ({reason.replace('_', ' ')}): {inr(net, sign=True)} after fees.\n"
                f"{EXIT_PLAIN.get(reason, 'It was closed.')}\n"
                f"{ledger_line(state)}")
    check_loss_limit(state)
    check_weekly_brake(state)
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

    _midday_summary(state)

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


def _midday_summary(state) -> None:
    """Auto mode: one short summary at MIDDAY_SUMMARY_AT so the user doesn't have to follow every message."""
    now = market.now_ist()
    if not auto_on(state) or now.strftime("%H:%M") < config.MIDDAY_SUMMARY_AT or now.strftime("%H:%M") > "15:10":
        return
    if state.get("midday_sent") == _today():
        return
    state["midday_sent"] = _today()
    today = [p for p in state["proposals"].values() if p.get("day") == _today()]
    placed = [p for p in today if p.get("user_reply") == "AUTO"]
    skipped = [p for p in today if p.get("user_reply") == "AUTO-NO"]
    rated = sum(1 for p in today if p.get("feedback"))
    lines = [f"[MOCK] Midday summary (auto mode): {len(placed)} placed, {len(skipped)} skipped, "
             f"{state['trades_today']}/{trade_cap(state)} trades used.",
             f"Realized today {inr(state['realized_today'], sign=True)}; "
             f"{len(state['positions'])} open position(s): {', '.join(state['positions']) or 'none'}.",
             f"You rated {rated} of {len(today)} decisions so far (👍/👎 on each message)."]
    tg.send("\n".join(lines))


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
        how = "skipped by auto mode" if p.get("mode") == "auto" else {"rejected": "you said NO"}.get(
            p["status"], "no reply")
        hypo[f"{action_word(p['side'])} {p['symbol']} ({how})"] = pnl
        # Fixed event_id: re-running the report doesn't double-count in Databricks.
        journal("passed_outcome", event_id=f"passed-{pid}", proposal_id=pid, symbol=p["symbol"],
                setup=p["setup"], side=p["side"], qty=p["qty"], risk_rs=p["risk_rs"], pnl_rs=pnl,
                user_reply="auto_skipped" if p.get("mode") == "auto" else p["status"],
                note="what this skipped proposal would have made")
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
    try:
        summary.update(_shadow_exits(state, rows))
    except Exception as err:  # noqa: BLE001 - a trial must never stop the day report
        summary["exit_rules_error"] = str(err)
    try:
        snap = pools.snapshot(state)
        summary["portfolio_total_rs"] = snap["total"]
        summary["portfolio_drawdown_pct"] = snap["drawdown_pct"]
        if snap["tripped_now"]:
            tg.send(f"[MOCK] Portfolio circuit breaker: all pools together are {snap['drawdown_pct']}% below their "
                    f"peak ({inr(snap['total'])} vs {inr(snap['peak'])}). No new buys in any pool until you send "
                    "/resume. A good moment to review what went wrong.")
            journal("circuit_breaker", note=f"drawdown {snap['drawdown_pct']}%")
    except Exception as err:  # noqa: BLE001 - the day report must still go out
        summary["portfolio_error"] = str(err)
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
            lines.append(f"Passed trades would have made: {inr(sum(hypo.values()), sign=True)}")
            lines += [f"- {k}: {inr(v, sign=True)}" for k, v in hypo.items()]
        lines += _shadow_exit_lines(summary)
        lines += _auto_lines(state)
        if summary["open_positions"]:
            lines.append(f"WARNING still open: {', '.join(summary['open_positions'])}")
        tg.send("\n".join(lines))
    return summary


def _shadow_exits(state, rows) -> dict:
    """Replay today's closed trades under the exit rules on trial (trader/exits.py) and journal the results."""
    fills = {r["proposal_id"]: r for r in rows if r["event"] == "filled" and r.get("proposal_id")}
    exits_ = {r["proposal_id"]: r for r in rows if r["event"] in ("exited", "squared_off") and r.get("proposal_id")}
    totals = {rule: 0.0 for rule in exits.RULES}
    actual_total, counted, day_events, bars_cache = 0.0, 0, [], {}
    for pid, f in fills.items():
        x = exits_.get(pid)
        if not x:
            continue
        net = float(x.get("pnl_rs") or 0) - float(f.get("fees_rs") or 0)
        day_events.append((f["timestamp_ist"], x["timestamp_ist"], net))
        sym = f["symbol"]
        if sym not in bars_cache:
            bars_cache[sym] = market.candles(sym, "1m", 1)
        results = exits.replay_all({"side": f["side"], "qty": f["qty"], "fill_price": f["fill_price"],
                                    "stop": f["stop"], "target": f["target"], "opened_at": f["timestamp_ist"]},
                                   bars_cache[sym])
        journal("exit_variants", event_id=f"exitvar-{pid}", proposal_id=pid, symbol=sym, setup=f["setup"],
                side=f["side"], qty=f["qty"], pnl_rs=round(net, 2), note=exits.note_text(results))
        if all(v is not None for v in results.values()):
            counted += 1
            actual_total += net
            for rule, v in results.items():
                totals[rule] += v
    out = {"exit_rules_trades": counted, "exit_rules_actual_rs": round(actual_total, 2),
           "exit_rules_rs": {k: round(v, 2) for k, v in totals.items()}}
    lock = exits.profit_lock(sorted(day_events), one_r(state)) if day_events else None
    if lock:
        journal("profit_lock_shadow", event_id=f"lock-{_today()}", pnl_rs=lock["day_with_lock"],
                note=f"skipped {lock['skipped']} trades; actual {lock['day_actual']}")
        out["profit_lock"] = lock
    return out


def _auto_lines(state) -> list:
    today = [p for p in state["proposals"].values() if p.get("day") == _today() and p.get("mode") == "auto"]
    if not today:
        return []
    placed = sum(1 for p in today if p.get("user_reply") == "AUTO")
    likes = sum(1 for p in today if p.get("feedback") == "like")
    dislikes = sum(1 for p in today if p.get("feedback") == "dislike")
    return [f"Auto mode: {placed} placed, {len(today) - placed} skipped. Your ratings: {likes} 👍, {dislikes} 👎, "
            f"{len(today) - likes - dislikes} not rated (you can still rate them on each message)."]


def _shadow_exit_lines(summary) -> list:
    if not summary.get("exit_rules_trades"):
        return []
    t = summary["exit_rules_rs"]
    base = t["current"]
    parts = [f"{exits.RULE_WORDS[k]} {inr(v - base, sign=True)}" for k, v in t.items() if k != "current"]
    lines = [f"Exit rules on trial ({summary['exit_rules_trades']} trades, vs current rules {inr(base, sign=True)}): "
             + ", ".join(parts)]
    lock = summary.get("profit_lock")
    if lock:
        lines.append(f"Profit lock on trial: would have skipped {lock['skipped']} trade(s); day "
                     f"{inr(lock['day_with_lock'], sign=True)} instead of {inr(lock['day_actual'], sign=True)}")
    return lines


# ---------------------------------------------------------- day / session --
def start_day(state, plan, focus):
    """Apply the pre-market plan: risk per trade for today (never above the default) and focus list."""
    risk = min(float(plan.get("risk_per_trade_pct") or config.RISK_PER_TRADE_PCT), config.RISK_PER_TRADE_PCT)
    state["day_risk_pct"] = risk
    state["plan"] = plan
    state["focus"] = [f.upper() for f in focus]
    radar = {r["symbol"] for r in plan.get("news_radar") or [] if r.get("symbol")}
    state["news_symbols"] = sorted({s for s in state["focus"] if s in radar or s not in sectors.WATCHLIST})
    storage.put_json(f"plans/{_today()}.json", {"plan": plan, "focus": state["focus"]})
    journal("day_plan", note=f"risk {risk}% | bias {plan.get('bias')} | focus {','.join(state['focus'])}")
    return {"risk_per_trade_pct": risk, "focus": state["focus"], "bias": plan.get("bias")}


def heartbeat(phase="session"):
    storage.put_json("heartbeat.json", {"time": _now(), "phase": phase})


def run_tool(fn, *args, **kwargs):
    """How every MCP tool runs: load the ledger, call fn(state, ...), save. Each call also refreshes the
    heartbeat, so the responder Lambda never starts reading Telegram while a session waits on the reviewer or
    on the user's reply (Telegram allows one reader)."""
    heartbeat()
    state = load()
    try:
        return fn(state, *args, **kwargs)
    finally:
        save(state)


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
