"""Independent risk reviewer: a second Claude that argues against each proposal before you see it.

It never sees the proposing agent's reasons, summary or confidence: only facts gathered by code
(the trade, the live market check, recent candles, the day plan and today's earlier trades).
It runs `claude -p` with no tools, on the same Claude subscription as the session, and only when
REVIEWER=on (set in the trading-session workflow). Any failure returns None: a proposal is never
blocked by the reviewer.
"""
import json
import os
import re
import shutil
import subprocess

import config
import market
import news
import sectors
import storage
from http_client import log

PROMPT = """You are a sceptical risk reviewer for a MOCK intraday trade on the Indian stock market (dummy money).
Another agent proposed the trade below. Your job is to look for reasons it will FAIL, then give a fair verdict.
Judge only from the facts given; don't invent news or prices. Pay attention to: the trade going against
Nifty/Sensex/the sector, repeating a setup or stock that already lost today, a stop too tight for a jumpy
market (high VIX), entering late, little time left, and a weak or tiny edge.

FACTS
{facts}

Reply with JSON only, no other text:
{{"yes_pct": <0-100, how strongly you'd say YES; 50 = coin flip>, "verdict": "YES" | "NO" | "UNSURE",
  "why": "<one plain sentence of at most 25 words for someone who isn't a trader: no jargon or abbreviations>"}}"""


def shorten(text, limit):
    """Cut at a word boundary with an ellipsis, never mid-word."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:-") + "…"


LAST_ERROR = ""  # why the last review didn't come back (shown in the proposal and journaled)


def enabled() -> bool:
    return os.environ.get("REVIEWER", "off").lower() == "on" and shutil.which("claude") is not None


def unavailable_reason() -> str:
    if os.environ.get("REVIEWER", "off").lower() != "on":
        return "switched off (REVIEWER is not 'on')"
    if shutil.which("claude") is None:
        return "the claude command isn't installed where the trader runs"
    return LAST_ERROR or "no answer"


def facts(state, symbol, side, setup, entry, stop, target, rr, qty, market_lines) -> str:
    plan = state.get("plan") or {}
    lines = [
        f"Time now: {market.now_ist():%H:%M} IST (entries stop 14:30, everything closes 15:10)",
        f"Trade: {side} {qty} {symbol} at ~{entry} | stop-loss {stop} | target {target} | gain/loss ratio {rr}",
        f"Setup: {setup}",
        f"Stop distance: {abs(entry - stop) / entry * 100:.2f}% of price",
        f"Day plan: bias {plan.get('bias', 'n/a')}, VIX {plan.get('vix', 'n/a')}, "
        f"S&P 500 overnight {plan.get('spy_return_pct', 'n/a')}%",
        *[ln for ln in market_lines if ln != "Market check:"],
    ]
    try:
        bars = market.candles(symbol, "5m", 1)[-12:]
        if bars:
            lines.append("Last 5-min candles (time close volume): " + "; ".join(
                f"{b['time'][-5:]} {b['close']} {b['volume']}" for b in bars))
    except Exception:  # noqa: BLE001
        pass
    try:
        rows = storage.read_rows(f"journal/{market.now_ist():%Y-%m-%d}.csv")
    except Exception:  # noqa: BLE001
        rows = []
    done = [r for r in rows if r.get("event") in ("exited", "squared_off")]
    if done:
        lines.append("Today's closed trades: " + "; ".join(
            f"{r['side']} {r['symbol']} ({r['setup']}) {r['exit_reason']} P&L Rs {r['pnl_rs']}" for r in done))
    else:
        lines.append("Today's closed trades: none")
    if state.get("positions"):
        lines.append("Open positions: " + ", ".join(
            f"{p.get('side', '')} {s} @ {p.get('entry', p.get('fill_price', ''))}" for s, p in state["positions"].items()))
    sec = sectors.sector(symbol)
    same = [r for r in done if sectors.sector(r["symbol"]) == sec]
    lines.append(f"Sector: {sec}; closed trades in this sector today: {len(same)}"
                 + (" (" + "; ".join(f"{r['symbol']} P&L Rs {r['pnl_rs']}" for r in same) + ")" if same else ""))
    lines.append(f"Losses in a row so far: {state.get('loss_streak', 0)}")
    found = news.for_symbol(symbol)
    if found is None:
        lines.append("NSE filings for this company: feed unavailable")
    elif found:
        lines.append("NSE filings for this company (today/yesterday): " + " | ".join(news.describe(i) for i in found))
    else:
        lines.append("NSE filings for this company (today/yesterday): none")
    lines.append(f"Realized P&L today: Rs {state.get('realized_today', 0):,.0f}")
    return "\n".join(lines)


def parse(text: str):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        out = json.loads(m.group(0))
        pct = int(round(float(out["yes_pct"])))
    except (ValueError, KeyError, TypeError):
        return None
    pct = max(0, min(100, pct))
    verdict = str(out.get("verdict", "")).upper()
    if verdict not in ("YES", "NO", "UNSURE"):
        verdict = "YES" if pct >= 60 else "NO" if pct <= 40 else "UNSURE"
    why = shorten(" ".join(str(out.get("why", "")).split()), 220)
    return {"yes_pct": pct, "verdict": verdict, "why": why}


def _fail(reason):
    global LAST_ERROR
    LAST_ERROR = " ".join(str(reason).split())[:200]
    log.warning("reviewer unavailable: %s", LAST_ERROR)
    return None


def review(state, symbol, side, setup, entry, stop, target, rr, qty, market_lines):
    if not enabled():
        return _fail(unavailable_reason())
    prompt = PROMPT.format(facts=facts(state, symbol, side, setup, entry, stop, target, rr, qty, market_lines))
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDECODE") and k != "ANTHROPIC_API_KEY"}
    if not env.get("CLAUDE_CODE_OAUTH_TOKEN") and env.get("REVIEWER_CLAUDE_TOKEN"):
        # The session's login isn't passed down to its tools ("Not logged in"); the workflow hands it over
        # under another name for this one purpose.
        env["CLAUDE_CODE_OAUTH_TOKEN"] = env["REVIEWER_CLAUDE_TOKEN"]
    env.pop("REVIEWER_CLAUDE_TOKEN", None)
    try:
        res = subprocess.run(
            ["claude", "-p", prompt, "--model", config.REVIEWER_MODEL, "--tools", "",
             "--strict-mcp-config", "--output-format", "text"],
            capture_output=True, text=True, timeout=config.REVIEWER_TIMEOUT_SEC, env=env,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return _fail(f"no answer within {config.REVIEWER_TIMEOUT_SEC} s")
    except OSError as err:
        return _fail(f"couldn't start claude: {err}")
    if res.returncode != 0:
        return _fail(f"claude exited {res.returncode}: {(res.stderr or res.stdout).strip()[-160:]}")
    out = parse(res.stdout)
    return out if out else _fail(f"answer wasn't the expected JSON: {res.stdout.strip()[:120]}")
