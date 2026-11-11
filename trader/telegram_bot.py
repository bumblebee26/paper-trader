"""Telegram Bot API over long polling (standard library only). No webhook needed.

The same bot can be shared with the lakehouse pre-market brief: that Lambda only
sends messages, while this module is the only reader of updates.
"""
import json
import urllib.error
import urllib.request

import config
from http_client import log


class TelegramError(RuntimeError):
    pass


def _api(method, http_timeout=30, **params):
    token = config.telegram_token()
    if not token:
        raise TelegramError("Telegram token not configured (TELEGRAM_BOT_TOKEN or TELEGRAM_TOKEN_PARAM)")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=http_timeout) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as err:
        data = json.loads(err.read() or b"{}")
    if not data.get("ok"):
        raise TelegramError(f"{method} failed: {data.get('description')}")
    return data["result"]


def setup() -> dict:
    """Remove any webhook so polling works; return the bot's identity."""
    _api("deleteWebhook", drop_pending_updates=False)
    return _api("getMe")


# Shown in Telegram's "/" menu (registered at the start of each session).
COMMANDS = [
    ("ledger", "Money: balance, in use, free, open positions with live P&L"),
    ("plan", "Today's plan: US move, VIX, risk per trade, bias, focus list"),
    ("exit", "Close a position now, e.g. /exit HDFCBANK"),
    ("news", "Latest NSE filings for a stock, e.g. /news RELIANCE"),
    ("portfolio", "All dummy pools: intraday, swing, long-term"),
    ("pause", "No new proposals today (open positions still managed)"),
    ("stop", "Kill switch: no new proposals until /resume"),
    ("resume", "Allow new proposals again"),
    ("help", "All commands and what the trader does"),
]


def set_commands() -> None:
    _api("setMyCommands", commands=[{"command": c, "description": d} for c, d in COMMANDS])


def send(text, buttons=None) -> int:
    params = {"chat_id": config.telegram_chat_id(), "text": text}
    if buttons:
        params["reply_markup"] = {
            "inline_keyboard": [[{"text": label, "callback_data": data} for label, data in buttons]]
        }
    return _api("sendMessage", **params)["message_id"]


def edit(message_id, text) -> None:
    try:
        _api("editMessageText", chat_id=config.telegram_chat_id(), message_id=message_id, text=text)
    except TelegramError:
        pass  # unchanged or too old; harmless


def poll(state, wait=0) -> None:
    """Read new updates once (long-polling up to `wait` seconds) into `state`.

    Button yes:<id> / no:<id> -> state["decisions"][id]; typed YES/NO -> newest pending
    proposal; "EXIT RELIANCE" -> state["exit_requests"]; LEDGER, HELP, PLAN, PAUSE, RESUME ->
    state["requests"] (answered by engine.answer_requests). Other senders are ignored.
    """
    allowed = str(config.allowed_user_id())
    try:
        updates = _api("getUpdates", http_timeout=wait + 15, offset=state.get("tg_offset", 0),
                       timeout=wait, allowed_updates=["message", "callback_query"])
    except TelegramError as err:
        if "conflict" not in str(err).lower():
            raise
        # Another reader (e.g. the responder Lambda) polled at the same moment: Telegram keeps the update
        # for whoever reads next, so skip this round instead of failing the caller.
        log.warning("Telegram read skipped: %s", err)
        return
    for u in updates:
        state["tg_offset"] = u["update_id"] + 1
        if "callback_query" in u:
            cq = u["callback_query"]
            if str(cq["from"]["id"]) != allowed:
                continue
            data = cq.get("data", "")
            if data.startswith("fb:"):                      # fb:like:<id> / fb:dislike:<id>: feedback only
                _, kind, pid = data.split(":", 2)
                state.setdefault("feedback", {})[pid] = kind
            elif data.startswith("x:"):                     # x:<id>: EXIT button on an auto-placed trade
                sym = state.get("proposals", {}).get(data[2:], {}).get("symbol")
                if sym:
                    state.setdefault("exit_requests", []).append(sym)
            elif ":" in data:
                verdict, pid = data.split(":", 1)
                state.setdefault("decisions", {}).setdefault(pid, verdict)
            try:
                _api("answerCallbackQuery", callback_query_id=cq["id"], text="Got it")
            except TelegramError:
                pass
        elif "message" in u:
            msg = u["message"]
            if str(msg.get("from", {}).get("id")) != allowed:
                continue
            # "/ledger", "/exit@mock_26_trader_bot reliance" -> LEDGER, EXIT RELIANCE
            parts = [w.lstrip("/").split("@")[0] for w in (msg.get("text") or "").strip().upper().split()]
            cmd = parts[0] if parts else ""
            asks = state.setdefault("requests", [])
            if len(parts) == 2 and cmd == "EXIT":
                state.setdefault("exit_requests", []).append(parts[1])
            elif cmd in ("LEDGER", "BALANCE", "STATUS"):
                asks.append("ledger")
            elif cmd in ("HELP", "PROMPTS", "COMMANDS", "START", "MENU") or "WHAT CAN YOU DO" in " ".join(parts):
                asks.append("help")
            elif cmd == "NEWS" and len(parts) == 2:
                asks.append(f"news:{parts[1]}")
            elif cmd in ("PORTFOLIO", "POOLS"):
                asks.append("portfolio")
            elif cmd in ("STOP", "KILL"):
                asks.append("stop")
            elif cmd == "PLAN":
                asks.append("plan")
            elif cmd == "AUTO":
                arg = parts[1] if len(parts) > 1 else ""
                asks.append("auto_on" if arg == "ON" else "auto_off" if arg == "OFF" else "auto_status")
            elif cmd == "PAUSE":
                asks.append("pause")
            elif cmd == "RESUME":
                asks.append("resume")
            elif cmd in ("YES", "NO"):
                pending = [p for p, v in state.get("proposals", {}).items() if v["status"] == "pending"]
                if pending:
                    state.setdefault("decisions", {}).setdefault(pending[-1], parts[0].lower())
                else:
                    asks.append("nothing_pending")
            elif parts:
                asks.append("unknown")
