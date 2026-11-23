"""Auto mode for the delivery pools (from 24 Nov): the real swing and long-term pools follow their chosen rules by
themselves, with a Telegram message for every buy and sell, the way intraday auto mode places trades.

  swing      follows the momentum + news rules (swing_momentum.py); idle cash stays in NIFTYBEES, which is sold to pay
             for a buy. On by default (owner's decision, 23 Nov).
  long_term  follows the weekly quality picks (jobs/longterm_shadow.py). Off until the 18 Dec go/no-go review
             approves it (owner's decision, 23 Nov); /longterm auto on switches it on.

/swing auto on|off and /longterm auto on|off change it; /pause, /stop and the circuit breaker still apply.
"""
import storage

KEY = "state/pool_auto.json"
DEFAULTS = {"swing": True, "long_term": False}
WORDS = {"swing": "Swing pool auto mode (momentum + news rules)",
         "long_term": "Long-term pool auto mode (weekly quality picks)"}


def on(pool) -> bool:
    return bool({**DEFAULTS, **(storage.get_json(KEY) or {})}.get(pool))


def set_mode(pool, value: bool) -> str:
    cur = {**DEFAULTS, **(storage.get_json(KEY) or {})}
    cur[pool] = bool(value)
    storage.put_json(KEY, cur)
    tail = {"swing": " Buys and sells happen at 15:50 on trading days; idle cash stays in NIFTYBEES.",
            "long_term": " Buys and sells happen on Saturdays at 10:15 from this week's picks."}[pool]
    return f"[MOCK] {WORDS[pool]} is now {'ON' if value else 'OFF'}." + (tail if value else
                                                                          " Holdings stay as they are; nothing new is bought.")
