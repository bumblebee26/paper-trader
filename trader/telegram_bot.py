"""Telegram Bot API over long polling (standard library only). No webhook needed.

The same bot can be shared with the lakehouse pre-market brief: that Lambda only
sends messages, while this module is the only reader of updates.
"""
import json
import urllib.error
import urllib.request

import config


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
    proposal; "EXIT RELIANCE" -> state["exit_requests"]. Other senders are ignored.
    """
    allowed = str(config.allowed_user_id())
    updates = _api("getUpdates", http_timeout=wait + 15, offset=state.get("tg_offset", 0),
                   timeout=wait, allowed_updates=["message", "callback_query"])
    for u in updates:
        state["tg_offset"] = u["update_id"] + 1
        if "callback_query" in u:
            cq = u["callback_query"]
            if str(cq["from"]["id"]) != allowed:
                continue
            data = cq.get("data", "")
            if ":" in data:
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
            parts = (msg.get("text") or "").strip().upper().split()
            if len(parts) == 2 and parts[0] == "EXIT":
                state.setdefault("exit_requests", []).append(parts[1])
            elif parts and parts[0] in ("YES", "NO"):
                pending = [p for p, v in state.get("proposals", {}).items() if v["status"] == "pending"]
                if pending:
                    state.setdefault("decisions", {}).setdefault(pending[-1], parts[0].lower())
