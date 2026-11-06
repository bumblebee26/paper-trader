"""Minimal Telegram Bot API client using long polling (no webhook, no server needed)."""
import requests

import config


class TelegramError(RuntimeError):
    pass


def _api(method: str, http_timeout: int = 30, **params):
    token = config.TELEGRAM_BOT_TOKEN
    if not token or "paste" in token:
        raise TelegramError("TELEGRAM_BOT_TOKEN is not set in .env")
    url = f"https://api.telegram.org/bot{token}/{method}"
    r = requests.post(url, json=params, timeout=http_timeout)
    data = r.json()
    if not data.get("ok"):
        raise TelegramError(f"{method} failed: {data.get('description')}")
    return data["result"]


def setup() -> dict:
    """Remove any webhook so polling works, and return the bot's identity."""
    _api("deleteWebhook", drop_pending_updates=False)
    return _api("getMe")


def send(text: str, buttons=None) -> int:
    params = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text}
    if buttons:
        params["reply_markup"] = {
            "inline_keyboard": [[{"text": label, "callback_data": data} for label, data in buttons]]
        }
    return _api("sendMessage", **params)["message_id"]


def edit(message_id: int, text: str) -> None:
    try:
        _api("editMessageText", chat_id=config.TELEGRAM_CHAT_ID, message_id=message_id, text=text)
    except TelegramError:
        pass  # unchanged or too old; harmless


def poll(state: dict, wait: int = 0) -> None:
    """Read new updates once (long-polling up to `wait` seconds) into `state`.

    - Button presses yes:<id> / no:<id>  -> state["decisions"][id]
    - Typed YES / NO                      -> newest pending proposal
    - Typed "EXIT RELIANCE"               -> state["exit_requests"]
    Messages from anyone except ALLOWED_USER_ID are ignored.
    """
    updates = _api(
        "getUpdates",
        http_timeout=wait + 15,
        offset=state.get("tg_offset", 0),
        timeout=wait,
        allowed_updates=["message", "callback_query"],
    )
    for u in updates:
        state["tg_offset"] = u["update_id"] + 1
        if "callback_query" in u:
            cq = u["callback_query"]
            if str(cq["from"]["id"]) != str(config.ALLOWED_USER_ID):
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
            if str(msg.get("from", {}).get("id")) != str(config.ALLOWED_USER_ID):
                continue
            parts = (msg.get("text") or "").strip().upper().split()
            if len(parts) == 2 and parts[0] == "EXIT":
                state.setdefault("exit_requests", []).append(parts[1])
            elif parts and parts[0] in ("YES", "NO"):
                pending = [p for p, v in state.get("proposals", {}).items() if v["status"] == "pending"]
                if pending:
                    state.setdefault("decisions", {}).setdefault(pending[-1], parts[0].lower())
