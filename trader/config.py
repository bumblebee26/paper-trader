"""Settings. Same code runs in three places:

  laptop (Claude Desktop)  -> values from ../.env, state in ../journal/
  GitHub Actions session   -> env vars from the workflow, secrets from SSM, state in S3
  AWS Lambda (guardian)    -> env vars from Terraform, secrets from SSM, state in S3

Secrets can be given directly (TELEGRAM_BOT_TOKEN) or as an SSM parameter name
(TELEGRAM_TOKEN_PARAM); the parameter is read only when needed.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


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


def _f(name, default):
    return float(os.environ.get(name) or default)


def _i(name, default):
    return int(float(os.environ.get(name) or default))


_ssm_cache = {}


def secret(value_env: str, param_env: str) -> str:
    """Value from env var `value_env`, else from the SSM parameter named in `param_env`."""
    value = os.environ.get(value_env, "")
    if value and not value.startswith("paste") and value != "SET_ME_OUTSIDE_TERRAFORM":
        return value
    param = os.environ.get(param_env, "")
    if not param:
        return ""
    if param not in _ssm_cache:
        import boto3  # only needed in AWS / CI

        ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "ap-southeast-2"))
        _ssm_cache[param] = ssm.get_parameter(Name=param, WithDecryption=True)["Parameter"]["Value"]
    return _ssm_cache[param]


def telegram_token() -> str:
    return secret("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN_PARAM")


def telegram_chat_id() -> str:
    return secret("TELEGRAM_CHAT_ID", "TELEGRAM_CHAT_ID_PARAM")


def allowed_user_id() -> str:
    # In a private chat the chat ID is the user's ID.
    return os.environ.get("ALLOWED_USER_ID") or telegram_chat_id()


# --- dummy money and risk rules ---
# Dummy pools: Rs 2,00,000 in total, split 20% / 30% / 50%. Intraday trades; swing is held in NIFTYBEES
# (its rules failed their backtests); long-term runs in shadow mode (docs/SYSTEM_PLAN.md, phases 3 and 4).
STARTING_CAPITAL = _f("STARTING_CAPITAL", 40_000)          # intraday pool
SWING_CAPITAL = _f("SWING_CAPITAL", 60_000)
LONG_TERM_CAPITAL = _f("LONG_TERM_CAPITAL", 100_000)
# Swing rules failed their backtest (9 Nov 2026): the swing pool holds this index fund instead.
SWING_PARK_SYMBOL = os.environ.get("SWING_PARK_SYMBOL", "NIFTYBEES")  # "" = keep the cash idle
RISK_PER_TRADE_PCT = _f("RISK_PER_TRADE_PCT", 1.0)
MAX_OPEN_POSITIONS = _i("MAX_OPEN_POSITIONS", 3)
MAX_TRADES_PER_DAY = _i("MAX_TRADES_PER_DAY", 10)        # 0 = no daily limit
# Loss limits in "R": one trade's planned risk today (balance x today's risk %, so it follows VIX).
DAILY_LOSS_LIMIT_R = _f("DAILY_LOSS_LIMIT_R", 4.0)        # day's loss incl. open risk; 4R = Rs 1,600 (Rs 1,200 at VIX >= 20)
DAILY_LOSS_LIMIT_RS = _f("DAILY_LOSS_LIMIT_RS", 0)        # > 0: a fixed rupee limit instead of R
DAILY_LOSS_LIMIT_PCT = _f("DAILY_LOSS_LIMIT_PCT", 2.0)    # used only when both of the above are 0
WEEKLY_LOSS_LIMIT_R = _f("WEEKLY_LOSS_LIMIT_R", 8.0)      # week's loss that halves risk per trade until Monday (0 = off)
WEEKLY_BRAKE_FACTOR = _f("WEEKLY_BRAKE_FACTOR", 0.5)      # risk per trade multiplier while the weekly brake is on
MAX_COST_SHARE_PCT = _f("MAX_COST_SHARE_PCT", 30.0)       # block a trade whose round-trip costs exceed this % of its risk
COOLOFF_AFTER_LOSSES = _i("COOLOFF_AFTER_LOSSES", 3)      # losses in a row that start a cool-off (0 = off)
COOLOFF_MIN = _i("COOLOFF_MIN", 30)                       # minutes without new proposals
MAX_POSITION_MARGIN_PCT = _f("MAX_POSITION_MARGIN_PCT", 30.0)  # one trade's margin, % of balance
MAX_OPEN_PER_SECTOR = _i("MAX_OPEN_PER_SECTOR", 1)
MAX_TRADES_PER_SECTOR = _i("MAX_TRADES_PER_SECTOR", 2)    # per day
FOCUS_MAX = _i("FOCUS_MAX", 15)
MIN_RR = _f("MIN_RR", 1.5)
INTRADAY_LEVERAGE = _f("INTRADAY_LEVERAGE", 5.0)
SLIPPAGE_PCT = _f("SLIPPAGE_PCT", 0.05)
# Delivery (swing, long-term): STT 0.1% + exchange, stamp duty and GST, per side; depository charge per sell
DELIVERY_COST_PCT = _f("DELIVERY_COST_PCT", 0.12)
DP_CHARGE_RS = _f("DP_CHARGE_RS", 16.0)
BROKERAGE_PER_ORDER = _f("BROKERAGE_PER_ORDER", 20.0)
APPROVAL_TIMEOUT_SEC = _i("APPROVAL_TIMEOUT_SEC", 180)
# Independent risk reviewer (trader/reviewer.py); switched on by REVIEWER=on in the session workflow.
REVIEWER_MODEL = os.environ.get("REVIEWER_MODEL", "sonnet")

# Auto mode (/auto on|off on Telegram): proposals with a combined suggestion of at least AUTO_MIN_YES_PCT
# are placed without waiting for a YES; the rest are skipped. The user's 👍/👎 is recorded as feedback.
AUTO_MODE_DEFAULT = os.environ.get("AUTO_MODE_DEFAULT", "on").lower() in ("1", "on", "true", "yes")
AUTO_MIN_YES_PCT = _f("AUTO_MIN_YES_PCT", 55.0)
AUTO_MIN_AGENT_PCT = _f("AUTO_MIN_AGENT_PCT", 50.0)     # Claude's own score must also be for the trade
AUTO_NEEDS_REVIEWER = os.environ.get("AUTO_NEEDS_REVIEWER", "on").lower() in ("1", "on", "true", "yes")
AUTO_MAX_TRADES_PER_DAY = _i("AUTO_MAX_TRADES_PER_DAY", 6)
# Both minimums above must be beaten, not just met (exactly 55% / 50% is a coin flip and is skipped).
AUTO_LOSING_GROUP_BUMP = _f("AUTO_LOSING_GROUP_BUMP", 5.0)   # bar +5 if this setup/mood/hour is losing (8+ trades)
AUTO_FLIP_MIN_YES_PCT = _f("AUTO_FLIP_MIN_YES_PCT", 60.0)    # bar for the opposite side of a stock proposed earlier today
MIDDAY_SUMMARY_AT = os.environ.get("MIDDAY_SUMMARY_AT", "12:30")   # auto mode: a short summary at this time
REVIEWER_TIMEOUT_SEC = _i("REVIEWER_TIMEOUT_SEC", 90)
ENTRY_TOLERANCE_PCT = _f("ENTRY_TOLERANCE_PCT", 0.3)

ENTRY_START = os.environ.get("ENTRY_START", "09:30")
ENTRY_END = os.environ.get("ENTRY_END", "14:30")
SQUARE_OFF = os.environ.get("SQUARE_OFF", "15:10")

# --- where state lives ---
STATE_BUCKET = os.environ.get("STATE_BUCKET", "")          # set -> S3, empty -> local files
LOCAL_DATA_DIR = Path(os.environ.get("LOCAL_DATA_DIR", BASE_DIR / "journal"))
AWS_REGION = os.environ.get("AWS_REGION", "ap-southeast-2")

# --- lakehouse pre-market data (Databricks SQL), all optional ---
DATABRICKS_URL_PARAM = os.environ.get("DATABRICKS_URL_PARAM", "")
DATABRICKS_TOKEN_PARAM = os.environ.get("DATABRICKS_TOKEN_PARAM", "")
DATABRICKS_WAREHOUSE_PARAM = os.environ.get("DATABRICKS_WAREHOUSE_PARAM", "")
LANDING_VOLUME_PATH = os.environ.get("LANDING_VOLUME_PATH", "/Volumes/stocks/bronze/landing")
NEWS_VOLUME_PATH = os.environ.get("NEWS_VOLUME_PATH", "/Volumes/stocks/bronze/news")  # lakehouse news fetcher
DATA_STALE_MIN = _i("DATA_STALE_MIN", 15)   # Nifty candles older than this during market hours = stale
