"""Paper Trader MCP server: dummy-money NSE intraday trading with Telegram approvals.

  python trader/server.py            run as an MCP server (Claude Code / Claude Desktop start it)
  python trader/server.py --test     check Telegram + prices and send a test approval
  python trader/server.py --status   print the dummy account
"""
import json
import sys
import time
from datetime import timedelta

try:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
except ImportError:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server

import advisor
import brief
import config
import engine
import track
import market
import news
import scanner
import sectors
import telegram_bot as tg

mcp = _Server("paper-trader")

SCAN_PAUSE_SEC = 0.3
DEFAULT_WATCHLIST = sectors.WATCHLIST  # 36 large caps across 11 sectors (trader/sectors.py)


_run = engine.run_tool


@mcp.tool()
def premarket_context() -> dict:
    """Today's pre-market context from the stocks_lakehouse gold tables (US move, India's usual
    opening gap after such US days, VIX and risk level, unusual-volume NSE names, ADR moves).
    Also: track_record (how past proposals turned out by setup, hour, mood, suggestion band, reviewer
    verdict, mode and the user's 👍/👎; rows with judged=false have too few outcomes to act on) and
    auto_mode. Call once at the start of the day, then call start_day."""
    data, failed = brief.fetch()
    plan = brief.build_plan(data, failed)
    plan["default_watchlist"] = DEFAULT_WATCHLIST
    plan["track_record"] = track.for_claude()
    plan["auto_mode"] = engine.auto_on(engine.load())
    return plan


@mcp.tool()
def start_day(focus: list[str], plan: dict | None = None, send_to_telegram: bool = True) -> dict:
    """Lock in today's plan: risk per trade (from plan.risk_per_trade_pct, capped at the default)
    and the focus list (max 15 NSE symbols, spread across sectors). Sends the plan to Telegram."""
    plan = plan or {}
    focus = [s.upper() for s in focus][:config.FOCUS_MAX]
    result = _run(engine.start_day, plan, focus)
    if send_to_telegram:
        tg.send(brief.plan_message({**plan, "risk_per_trade_pct": result["risk_per_trade_pct"]}, focus))
    return result


@mcp.tool()
def account_status() -> dict:
    """Dummy account: balance, P&L, open positions, today's risk and focus list."""
    return _run(engine.account)


@mcp.tool()
def _with_market(info, moves):
    """Add today's Nifty and sector-index moves (% since the open), so reasons can be checked against them."""
    def move(idx):
        if idx not in moves:
            moves[idx] = advisor.index_move(idx)
        return moves[idx]
    info["nifty_move_pct"] = move("^NSEI")
    sec = sectors.SECTOR_INDEX.get(info.get("symbol", ""))
    if sec:
        info["sector_index"], info["sector_move_pct"] = sec[1], move(sec[0])
    return info


def market_snapshot(symbols: list[str]) -> list[dict]:
    """Per NSE symbol: last price, gap %, previous-day high/low, VWAP, opening range, and today's Nifty and
    sector-index moves (check reasons like "IT is weak" against sector_move_pct)."""
    out, moves = [], {}
    for s in symbols:
        try:
            info = scanner.analyse(s)
            info.pop("signals", None)
            out.append(_with_market(info, moves))
        except Exception as e:  # noqa: BLE001
            out.append({"symbol": s, "error": str(e)})
    return out


def _scan(symbols):
    found, moves = [], {}
    for i, s in enumerate(symbols):
        if i and i % 5 == 0:
            time.sleep(SCAN_PAUSE_SEC)  # small batches, gentle on Yahoo's rate limits
        try:
            info = scanner.analyse(s)
            if info.get("signals"):
                found.append(_with_market(info, moves))
        except Exception as e:  # noqa: BLE001
            found.append({"symbol": s, "error": str(e)})
    return found


@mcp.tool()
def scan_setups(symbols: list[str]) -> dict:
    """ORB, VWAP reclaim/rejection and previous-day high/low candidates on the last completed
    5-min candle, plus Nifty context. Candidates are not trades: judge them first."""
    return {"time_ist": engine._now(), "nifty": scanner.nifty_context(), "candidates": _scan(symbols),
            "entry_window_open": engine.in_entry_window()}


@mcp.tool()
def run_cycle() -> dict:
    """One 5-minute cycle in a single call: monitor positions (stops, targets, EXIT messages,
    square-off), then scan today's focus list if entries are allowed. Returns events, a short
    account line and any candidates. Use this instead of separate monitor/scan calls."""
    state = engine.load()
    try:
        mon = engine.monitor(state)
        focus = state.get("focus") or DEFAULT_WATCHLIST
        nifty = scanner.nifty_context() if mon["account"]["entry_window_open"] else None
        if nifty is not None:
            engine.data_health(state, nifty)
    finally:
        engine.save(state)
    engine.heartbeat()
    acct = mon["account"]
    out = {
        "time_ist": engine._now(),
        "events": mon["events"],
        "balance": acct["dummy_balance"],
        "realized_today": acct["realized_today"],
        "open_positions": list(acct["open_positions"].keys()),
        "entry_window_open": acct["entry_window_open"],
    }
    if acct["entry_window_open"]:
        out["nifty"] = nifty
        out["market_mood"] = advisor.market_mood(nifty.get("change_from_open_pct"),
                                                 (state.get("plan") or {}).get("vix"))
        if state.get("data_stale"):
            out["data_stale"] = "prices look stale: no proposals this cycle"
        else:
            out["candidates"] = _scan(focus)
    return out


@mcp.tool()
def company_news(symbol: str) -> dict:
    """The company's NSE filings from today and the last trading day (results, orders, management
    changes, regulatory matters, board meetings...), newest first, from the lakehouse news feed."""
    found = news.for_symbol(symbol.upper(), limit=6)
    if found is None:
        return {"symbol": symbol.upper(), "news": "unavailable"}
    return {"symbol": symbol.upper(), "items": [
        {"when": news.when(i), "category": i.get("category"), "text": news.describe(i), "link": i.get("link")}
        for i in found]}


@mcp.tool()
def propose_trade(symbol: str, side: str, setup: str, entry: float, stop: float, target: float,
                  reasons: list[str], invalid_if: str, news: str = "none", summary: str = "",
                  ai_yes_pct: int | None = None, ai_reason: str = "") -> dict:
    """Risk gate + position size + Telegram proposal. Returns proposal_id and a status:
    pending (human mode: call wait_for_decision), approved (auto mode, suggestion high enough: call
    execute_approved_trade now), skipped_auto (auto mode, suggestion too low: move on), or blocked.
    side: BUY or SELL. reasons: up to 3 short, number-based. news: leave "none" (code adds the company's
    NSE filings from the lakehouse itself); only add a note if company_news showed something that matters.
    summary: one or two plain-English sentences for a non-trader (no jargon, no abbreviations) on why
    this looks good and the main thing that could go wrong. Money amounts are added by code.
    ai_yes_pct: your honest 0-100 view of whether this trade should be taken. ai_reason: one short sentence
    on why that number (shown to the user as the trading agent's reason). Code adds live Nifty, Sensex and
    sector checks and the independent reviewer, and combines the three into one suggestion."""
    return _run(engine.propose, symbol, side, setup, entry, stop, target, reasons, invalid_if, news, summary,
                ai_yes_pct, ai_reason)


@mcp.tool()
def wait_for_decision(proposal_id: str, max_wait_sec: int = 170) -> dict:
    """Wait (max 170 s) for the user's Telegram reply: pending, approved, rejected or expired."""
    return _run(engine.wait_decision, proposal_id, max_wait_sec)


@mcp.tool()
def execute_approved_trade(proposal_id: str) -> dict:
    """Simulated fill for an APPROVED proposal after re-checking price drift and the risk gate."""
    return _run(engine.execute, proposal_id)


@mcp.tool()
def monitor_positions() -> dict:
    """Stops/targets, EXIT messages, stale proposals and the square-off. run_cycle includes this."""
    result = _run(engine.monitor)
    engine.heartbeat()
    return result


@mcp.tool()
def close_position(symbol: str, reason: str = "manual") -> dict:
    """Close one dummy position at the latest price."""
    def _close(state):
        price, _ = market.last_price(symbol.upper())
        if price is None:
            return {"status": "no_price"}
        return engine.close(state, symbol.upper(), price, reason)
    return _run(_close)


@mcp.tool()
def notify(text: str) -> dict:
    """Informational Telegram message (not for approvals)."""
    return {"message_id": tg.send(text)}


@mcp.tool()
def wait_for_next_candle(session_end: str = "15:45") -> dict:
    """Sleep until just after the next 5-minute candle closes (max ~5 min), listening on Telegram
    so EXIT messages are picked up. Returns whether the session is over (time >= session_end IST)."""
    now = market.now_ist()
    nxt = now.replace(second=20, microsecond=0) + timedelta(minutes=5 - now.minute % 5)
    h, m = map(int, session_end.split(":"))
    end = now.replace(hour=h, minute=m, second=0, microsecond=0)
    target = min(nxt, end) if now < end else now
    while market.now_ist() < target:
        left = int((target - market.now_ist()).total_seconds())
        state = engine.load()
        try:
            tg.poll(state, wait=max(1, min(25, left)))
            engine.answer_requests(state)
        except Exception:  # noqa: BLE001 - network blip: just sleep
            time.sleep(max(1, min(25, left)))
        finally:
            engine.save(state)
        engine.heartbeat()
    t = market.now_ist()
    return {"time_ist": t.strftime("%H:%M:%S"), "session_over": t >= end}


@mcp.tool()
def end_of_day_report(send_to_telegram: bool = True) -> dict:
    """Day P&L and stats from the journal (incl. what skipped proposals would have made)."""
    return _run(engine.report, send_to_telegram)


def _selftest():
    me = tg.setup()
    print(f"Telegram bot OK: @{me['username']}")
    try:
        price, ts = market.last_price("RELIANCE")
        print(f"Market data OK: RELIANCE {price} at {ts}" if price else "Market data: no rows (market closed?)")
    except Exception as e:  # noqa: BLE001
        print(f"Market data FAILED: {e}")
    print(f"State: {'S3 bucket ' + config.STATE_BUCKET if config.STATE_BUCKET else config.LOCAL_DATA_DIR}")
    print(json.dumps(_run(engine.account), indent=2)[:400])
    msg = tg.send("[MOCK] Test approval from paper-trader. Tap a button.",
                  buttons=[("YES", "yes:selftest"), ("NO", "no:selftest")])
    state = engine.load()
    end = time.time() + 60
    while time.time() < end and "selftest" not in state.get("decisions", {}):
        tg.poll(state, wait=20)
    verdict = state.get("decisions", {}).pop("selftest", None)
    engine.save(state)
    if verdict:
        tg.edit(msg, f"[MOCK] Test approval: you tapped {verdict.upper()}. All working.")
        print(f"Got your reply: {verdict.upper()}. Setup complete.")
    else:
        print("No reply in 60 s. Check the chat ID / ALLOWED_USER_ID.")


if __name__ == "__main__":
    if "--test" in sys.argv:
        _selftest()
    elif "--status" in sys.argv:
        print(json.dumps(_run(engine.account), indent=2))
    else:
        mcp.run()
