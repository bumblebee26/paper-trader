"""Helpers for the GitHub Actions trading session (see .github/workflows/trading-session.yml).

  python trader/session_ctl.py phase              -> morning | afternoon | none (from the IST clock)
  python trader/session_ctl.py claim <phase>      -> exit 0 if this phase may run, 3 if it already ran
  python trader/session_ctl.py prompt <phase>     -> print the task prompt for Claude
  python trader/session_ctl.py alert "<text>"     -> send a Telegram message
"""
import sys

import engine
import market
import telegram_bot as tg

PHASES = {
    "morning": {
        "end": "12:25",
        "task": (
            "Morning session. 1) Call premarket_context. 2) Pick a focus list of at most 15 NSE symbols from "
            "default_watchlist spread across at least 6 sectors (no more than 3 from one sector), add up to 3 unusual_volume_nse names with clear volume (>=2x) and "
            "price >= 100, and prefer names whose ADR moved strongly. 3) Call start_day(focus, plan) with the "
            "plan from step 1. 4) Call wait_for_next_candle(session_end='{end}') until 09:20 has passed. "
            "If run_cycle shows no Nifty candle dated today by 09:30, it is a holiday: notify 'No market "
            "today' and stop. 5) Loop: run_cycle, handle candidates per the rules, "
            "wait_for_next_candle(session_end='{end}'), until session_over. If positions are still open then, "
            "notify one line: 'Morning session done. The afternoon session (12:27) keeps managing <symbols>; "
            "everything closes by 15:10.' Otherwise stop without a message."
        ),
    },
    "afternoon": {
        "end": "15:25",
        "task": (
            "Afternoon session. The day plan and focus list are already set (see account_status). "
            "Loop: run_cycle, handle candidates per the rules (entries stop at 14:30), "
            "wait_for_next_candle(session_end='{end}'), until session_over. Make sure run_cycle runs after "
            "15:10 so everything is squared off, then call end_of_day_report."
        ),
    },
    "test": {
        "end": "00:00",
        "task": (
            "Setup check only, no trading: call account_status, premarket_context and "
            "market_snapshot(['RELIANCE']), then notify exactly: '[MOCK] GitHub session OK. Dummy balance "
            "Rs <balance>. Lakehouse data: <source>. Prices: <ok with RELIANCE close, or the error>.' Then stop."
        ),
    },
}


def current_phase() -> str:
    t = market.now_ist().strftime("%H:%M")
    if "08:30" <= t < "12:25":
        return "morning"
    if "12:25" <= t < "15:20":
        return "afternoon"
    return "none"


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "phase":
        print(current_phase())
    elif cmd == "claim":
        phase = argv[2]
        if phase == "test":
            return 0
        now = market.now_ist()
        if now.weekday() >= 5:
            print("weekend, skipping")
            return 3
        name = market.holiday(now)
        if name:
            print(f"NSE holiday ({name}), skipping")
            if phase == "morning" and engine.claim_session("holiday_notice"):  # tell the user once
                nxt = market.next_trading_day_after(now)
                tg.send(f"[MOCK] No market today: NSE is closed for {name}. No sessions today; "
                        f"the next one starts {nxt:%a %d %b} at 09:08.")
            return 3
        if not engine.claim_session(phase):
            print(f"{phase} already ran today, skipping")
            return 3
        print(f"claimed {phase}")
        try:
            tg.set_commands()  # keeps the "/" menu in Telegram up to date
        except Exception as err:  # noqa: BLE001 - cosmetic, never block a session
            print(f"could not register Telegram commands: {err}")
    elif cmd == "prompt":
        phase = argv[2]
        p = PHASES[phase]
        print(f"Today is {market.now_ist():%A %Y-%m-%d}. " + p["task"].format(end=p["end"]))
    elif cmd == "alert":
        tg.send(argv[2])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
