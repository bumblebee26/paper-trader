"""Runs without Claude. 15:12 IST: square off anything still open. 15:40 IST: send the
day report if the afternoon session didn't. Scheduled by cron as a backstop."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
import engine  # noqa: E402

mode = sys.argv[1] if len(sys.argv) > 1 else "squareoff"
state = engine.load()
try:
    if mode == "squareoff" and state["positions"]:
        out = engine.monitor(state)
        print("safety net squared off:", out["events"])
    elif mode == "report" and not (config.DATA_DIR / f"{engine._today()}-report.json").exists():
        if (config.DATA_DIR / f"{engine._today()}.csv").exists():
            engine.report(state, send=True)
finally:
    engine.save(state)
