"""Reset the dummy ledger: every pool back to its starting cash with nothing held, from the next trading day.
Asked for on 17 Nov 2026 (Day 1 = Wed 18 Nov). Paper only: nothing real is bought or sold.

Reset   the intraday ledger (balance, open positions, proposals, today's and this week's counters, so the weekly
        brake starts clean), the swing and long-term pools, every shadow pool, and the portfolio peak, history and
        12% circuit breaker. Holdings are dropped, not sold.
Kept    the journals (the trade history the lakehouse track record learns from), your ratings, the model and
        shadow-rule results, the Telegram offset, recent proposal messages (so a late 👍/👎 still finds its trade),
        your settings (auto mode, stop/pause), and every rule and decision in the code and config.
Archive a copy of each state file it replaces goes to archive/ledger-reset-<date>/ first.

    python jobs/reset_ledger.py --confirm RESET           # does it
    python jobs/reset_ledger.py                           # shows what it would do, changes nothing
Run by hand from GitHub: Actions -> Reset ledger (type RESET).
"""
import argparse
import json
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "trader"))

import config  # noqa: E402
import market  # noqa: E402
import pools  # noqa: E402
import storage  # noqa: E402

KEEP = ("tg_offset", "msg_index", "auto_mode", "stopped", "paused_for", "news_alert_day", "market_seen")
ALL_POOLS = pools.POOLS + pools.SHADOW_POOLS


def state_keys():
    return [storage.LEDGER_KEY, pools.PORTFOLIO_KEY, *[pools._key(p) for p in ALL_POOLS]]


def fresh_ledger(old, now):
    monday = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
    state = {"balance": config.STARTING_CAPITAL, "starting_capital": config.STARTING_CAPITAL, "positions": {},
             "proposals": {}, "decisions": {}, "exit_requests": [], "tg_offset": 0,
             "day": None,                                              # the next load starts a new day
             "week": {"start": monday, "realized": 0.0, "brake": False},  # earlier days this week don't count
             "ledger_reset": {"at": now.strftime("%Y-%m-%d %H:%M:%S"),
                              "archive": f"archive/ledger-reset-{now:%Y-%m-%d}/"}}
    state.update({k: old[k] for k in KEEP if k in old})
    return state


def fresh_pool(name):
    return {"pool": name, "capital": pools.capital(name), "cash": pools.capital(name), "holdings": {}, "realized": 0.0}


def summary(old_ledger, old_pools):
    lines = [f"- intraday: Rs {float(old_ledger.get('balance', config.STARTING_CAPITAL)):,.0f}, "
             f"{len(old_ledger.get('positions') or {})} open -> Rs {config.STARTING_CAPITAL:,.0f}, none open"]
    for name, p in old_pools.items():
        if p is None:
            continue
        hv = sum(float(h["qty"]) * float(h.get("last_price") or h["avg_cost"]) for h in p.get("holdings", {}).values())
        lines.append(f"- {name}: Rs {p.get('cash', 0) + hv:,.0f} ({', '.join(p.get('holdings', {})) or 'cash'}) "
                     f"-> Rs {pools.capital(name):,.0f} cash")
    return lines


def reset(confirm=False, now=None):
    now = now or market.now_ist()
    old_ledger = storage.get_json(storage.LEDGER_KEY) or {}
    old_pools = {name: storage.get_json(pools._key(name)) for name in ALL_POOLS}
    lines = summary(old_ledger, old_pools)
    if not confirm:
        return {"done": False, "lines": lines}
    archive = f"archive/ledger-reset-{now:%Y-%m-%d}/"
    archived = []
    for key in state_keys():
        body = storage.backend().get(key)
        if body is not None:
            storage.backend().put(archive + key, body)
            archived.append(key)
    storage.put_json(storage.LEDGER_KEY, fresh_ledger(old_ledger, now))
    for name in ALL_POOLS:
        storage.put_json(pools._key(name), fresh_pool(name))
    total = config.STARTING_CAPITAL + sum(pools.capital(p) for p in pools.POOLS)
    storage.put_json(pools.PORTFOLIO_KEY, {"peak": total, "history": [], "breaker": False})
    return {"done": True, "lines": lines, "archive": archive, "archived": archived}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--confirm", default="", help="type RESET to reset; anything else only shows the plan")
    ap.add_argument("--no-send", action="store_true", help="don't tell Telegram")
    args = ap.parse_args(argv)
    out = reset(confirm=args.confirm == "RESET")
    head = "[MOCK] Ledger reset: every pool starts again from the next trading day." if out["done"] else \
        "Dry run (nothing changed; pass --confirm RESET):"
    print("\n".join([head, *out["lines"], *([f"Archived {len(out['archived'])} files to {out['archive']}"]
                                            if out["done"] else [])]))
    print(json.dumps({k: v for k, v in out.items() if k != "lines"}))
    if out["done"] and not args.no_send:
        import telegram_bot as tg

        tg.send("\n".join([head, *out["lines"],
                           "Kept: the trade history and track record, your ratings, model and shadow-rule results, "
                           "your settings and every rule. The dashboard's money pages start on 18 Nov."]))


if __name__ == "__main__":
    main()
