"""Settings for the paper trader. Values come from the .env file next to this script."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env(BASE_DIR / ".env")


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _i(name: str, default: int) -> int:
    return int(float(os.environ.get(name, default)))


TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ALLOWED_USER_ID = os.environ.get("ALLOWED_USER_ID", "")

STARTING_CAPITAL = _f("STARTING_CAPITAL", 1_000_000)   # dummy rupees
RISK_PER_TRADE_PCT = _f("RISK_PER_TRADE_PCT", 1.0)
MAX_OPEN_POSITIONS = _i("MAX_OPEN_POSITIONS", 3)
MAX_TRADES_PER_DAY = _i("MAX_TRADES_PER_DAY", 5)
DAILY_LOSS_LIMIT_PCT = _f("DAILY_LOSS_LIMIT_PCT", 2.0)
MIN_RR = _f("MIN_RR", 1.5)
INTRADAY_LEVERAGE = _f("INTRADAY_LEVERAGE", 5.0)       # simulated MIS margin
SLIPPAGE_PCT = _f("SLIPPAGE_PCT", 0.05)
BROKERAGE_PER_ORDER = _f("BROKERAGE_PER_ORDER", 20.0)
APPROVAL_TIMEOUT_SEC = _i("APPROVAL_TIMEOUT_SEC", 180)
ENTRY_TOLERANCE_PCT = _f("ENTRY_TOLERANCE_PCT", 0.3)

ENTRY_START = os.environ.get("ENTRY_START", "09:30")
ENTRY_END = os.environ.get("ENTRY_END", "14:30")
SQUARE_OFF = os.environ.get("SQUARE_OFF", "15:10")

DATA_DIR = BASE_DIR / "journal"
DATA_DIR.mkdir(exist_ok=True)
LEDGER_FILE = DATA_DIR / "ledger.json"
