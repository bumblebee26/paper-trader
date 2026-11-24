"""Run the phase 3 history backtests (backtest/history.py) on the lakehouse's 2019-2026 NSE prices.

  python backtest/run_history.py [--only momentum,high52] [--no-send]

Loads daily bars for the 250 most traded stocks of the first 60 sessions of 2019 (plus NIFTYBEES), runs every idea
on 2020-2024 and on the held-back 2025-2026, saves backtests/history/<date>.json in the state bucket, writes a
table to the GitHub job summary and sends a short Telegram summary. Started from GitHub Actions ("History
backtests"). Nothing live changes: results go to the 1 Dec / 18 Dec reviews.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "trader"), HERE]

import history  # noqa: E402
import market  # noqa: E402
import run_swing  # noqa: E402
import storage  # noqa: E402


def table(results):
    lines = ["| Idea | Period | Return | NIFTYBEES | Deepest fall | Trades | Won | Avg R | Avg R, low end | Verdict |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for key, periods in results.items():
        for label, s in periods.items():
            if not s:
                continue
            lines.append(f"| {history.NAMES[key]} | {label} | {s['return_pct']:+.1f}% | {s['niftybees_pct']:+.1f}% | "
                         f"{s['max_drawdown_pct']}% | {s['trades']} | {s['win_rate_pct']}% | {s['avg_r']} | "
                         f"{s['avg_r_low95']} | {s['verdict']} |")
    return "\n".join(lines)


def message(results):
    lines = ["[MOCK] History backtests, NSE 2019-2026 (Rs 60,000 pool, costs included; 2025-26 held back):"]
    for key, periods in results.items():
        parts = [f"{label}: {s['return_pct']:+.1f}% vs NIFTYBEES {s['niftybees_pct']:+.1f}%, {s['trades']} trades, "
                 f"avg {s['avg_r']}R -> {s['verdict']}" for label, s in periods.items() if s]
        lines.append(f"- {history.NAMES[key]}: " + "; ".join(parts))
    lines.append("Nothing live changes; the results go to the reviews.")
    return "\n".join(lines)


def main(argv):
    only = None
    if "--only" in argv:
        only = argv[argv.index("--only") + 1].split(",")
    t0 = time.monotonic()
    data = run_swing.load_data()
    print(f"Loaded {len(data)} symbols in {time.monotonic() - t0:.0f}s", flush=True)
    results = history.run_all(data, only)
    print(f"Ran in {time.monotonic() - t0:.0f}s", flush=True)
    text = table(results)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("## History backtests\n\n" + text + "\n\n" + history.__doc__.replace("\n", "  \n") + "\n")
    day = market.now_ist().strftime("%Y-%m-%d")
    try:
        storage.put_json(f"backtests/history/{day}.json", {"run_at": market.now_ist().isoformat(), "results": results})
    except Exception as err:  # noqa: BLE001
        print(f"Saving to S3 failed: {err}")
    with open("history_results.json", "w") as f:
        json.dump(results, f, indent=1)
    if "--no-send" not in argv:
        import telegram_bot as tg

        tg.send(message(results))


if __name__ == "__main__":
    main(sys.argv[1:])
