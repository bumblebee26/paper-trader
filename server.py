"""Paper Trader MCP server: dummy-money NSE intraday trading with Telegram approvals.

Run as MCP (Claude Desktop starts this automatically):   python server.py
Check setup and send a test approval to Telegram:        python server.py --test
Show the dummy account:                                    python server.py --status
"""
import json
import sys

try:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
except ImportError:  # mcp 2.x renamed it
    from mcp.server.mcpserver import MCPServer as _Server

import config
import engine
import market
import scanner
import telegram_bot as tg

mcp = _Server("paper-trader")


def _run(fn, *args, **kwargs):
    state = engine.load()
    try:
        return fn(state, *args, **kwargs)
    finally:
        engine.save(state)


@mcp.tool()
def account_status() -> dict:
    """Dummy account: balance, P&L, open positions, margin, whether entries are allowed now."""
    return _run(engine.account)


@mcp.tool()
def market_snapshot(symbols: list[str]) -> list[dict]:
    """Pre-market / intraday snapshot per NSE symbol (e.g. RELIANCE, TCS): last price, gap %,
    previous-day high/low, VWAP, opening range. Data from Yahoo Finance, may lag a few minutes."""
    out = []
    for s in symbols:
        try:
            info = scanner.analyse(s)
            info.pop("signals", None)
            out.append(info)
        except Exception as e:  # keep going if one symbol fails
            out.append({"symbol": s, "error": str(e)})
    return out


@mcp.tool()
def scan_setups(symbols: list[str]) -> dict:
    """Check the latest completed 5-min candle of each symbol for ORB, VWAP reclaim/rejection
    and previous-day high/low break setups. Returns candidate signals with numbers plus Nifty
    context. Candidates are NOT trades: judge them, then call propose_trade for good ones."""
    results = []
    for s in symbols:
        try:
            info = scanner.analyse(s)
            if info.get("signals"):
                results.append(info)
        except Exception as e:
            results.append({"symbol": s, "error": str(e)})
    return {"time_ist": engine._now(), "nifty": scanner.nifty_context(), "candidates": results,
            "entry_window_open": engine.in_entry_window()}


@mcp.tool()
def propose_trade(symbol: str, side: str, setup: str, entry: float, stop: float, target: float,
                  reasons: list[str], invalid_if: str, news: str = "none") -> dict:
    """Run the risk gate, size the position, and send the proposal to the user's Telegram with
    YES/NO buttons. Returns a proposal_id. Then call wait_for_decision. side is BUY or SELL.
    reasons: up to 3 short, number-based reasons. Never invent news; use "none"."""
    return _run(engine.propose, symbol, side, setup, entry, stop, target, reasons, invalid_if, news)


@mcp.tool()
def wait_for_decision(proposal_id: str, max_wait_sec: int = 45) -> dict:
    """Wait up to max_wait_sec (max 170) for the user's Telegram reply. Returns pending,
    approved, rejected or expired. If pending, call again. No reply by the deadline = expired."""
    return _run(engine.wait_decision, proposal_id, max_wait_sec)


@mcp.tool()
def execute_approved_trade(proposal_id: str) -> dict:
    """Place the simulated (dummy money) order for a proposal the user APPROVED. Re-checks
    expiry, price drift and the risk gate first. Refuses anything not approved."""
    return _run(engine.execute, proposal_id)


@mcp.tool()
def monitor_positions() -> dict:
    """Check open dummy positions for stop-loss/target hits, handle EXIT <symbol> messages from
    Telegram, expire stale proposals, and square off everything after the square-off time.
    Call every few minutes during the session."""
    return _run(engine.monitor)


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
    """Send an informational Telegram message (watchlist, notes). Not for approvals."""
    return {"message_id": tg.send(text)}


@mcp.tool()
def wait_for_next_candle(session_end: str = "15:45") -> dict:
    """Sleep until just after the next 5-minute candle closes (max ~5 min), listening on
    Telegram meanwhile so EXIT messages are picked up. Returns the time and whether the
    session is over (time >= session_end, HH:MM IST). Use between scan/monitor cycles."""
    import time
    from datetime import timedelta

    now = market.now_ist()
    nxt = now.replace(second=20, microsecond=0) + timedelta(minutes=5 - now.minute % 5)
    end_h, end_m = map(int, session_end.split(":"))
    end = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    target = min(nxt, end) if now < end else now
    while market.now_ist() < target:
        left = int((target - market.now_ist()).total_seconds())
        state = engine.load()
        try:
            tg.poll(state, wait=max(1, min(25, left)))
        except Exception:
            time.sleep(max(1, min(25, left)))
        finally:
            engine.save(state)
    t = market.now_ist()
    return {"time_ist": t.strftime("%H:%M:%S"), "session_over": t >= end,
            "pending_exit_requests": engine.load().get("exit_requests", [])}


@mcp.tool()
def end_of_day_report(send_to_telegram: bool = True) -> dict:
    """Compute today's dummy P&L and stats from the journal (including what rejected/expired
    proposals would have made) and optionally send a summary to Telegram."""
    return _run(engine.report, send_to_telegram)


def _selftest():
    print("Checking settings...")
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ALLOWED_USER_ID"):
        v = getattr(config, k)
        print(f"  {k}: {'set' if v and 'paste' not in v else 'MISSING'}")
    me = tg.setup()
    print(f"Telegram bot OK: @{me['username']}")
    price, ts = market.last_price("RELIANCE")
    print(f"Market data OK: RELIANCE {price} at {ts}" if price else "Market data: no data (market closed?)")
    print("Sending a test approval to Telegram... tap a button within 60 seconds.")
    msg = tg.send("[MOCK] Test approval from paper-trader. Tap a button.",
                  buttons=[("YES", "yes:selftest"), ("NO", "no:selftest")])
    state = engine.load()
    import time
    end = time.time() + 60
    while time.time() < end and "selftest" not in state.get("decisions", {}):
        tg.poll(state, wait=20)
    verdict = state.get("decisions", {}).pop("selftest", None)
    engine.save(state)
    if verdict:
        tg.edit(msg, f"[MOCK] Test approval: you tapped {verdict.upper()}. All working.")
        print(f"Got your reply: {verdict.upper()}. Setup complete.")
    else:
        print("No reply received. Check TELEGRAM_CHAT_ID / ALLOWED_USER_ID = your numeric ID.")


if __name__ == "__main__":
    if "--test" in sys.argv:
        _selftest()
    elif "--status" in sys.argv:
        print(json.dumps(_run(engine.account), indent=2))
    else:
        mcp.run()
