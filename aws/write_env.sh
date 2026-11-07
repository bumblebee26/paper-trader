#!/usr/bin/env bash
# Builds .env from .env.example plus secrets in SSM Parameter Store (Terraform setup).
# Re-run after changing secrets:  bash ~/paper-trader/aws/write_env.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
REGION="${AWS_REGION:-ap-south-1}"
PREFIX="${PARAM_PREFIX:-/paper-trader}"
AWS="$(command -v aws || echo /snap/bin/aws)"

param() {
  "$AWS" ssm get-parameter --region "$REGION" --with-decryption \
    --name "$PREFIX/$1" --query Parameter.Value --output text
}

TG_TOKEN="$(param telegram_bot_token)"
TG_USER="$(param telegram_user_id)"
API_KEY="$(param anthropic_api_key)"

# Keep model/budget from an existing .env unless given explicitly
if [ -f "$DIR/.env" ]; then
  MODEL_OLD="$(grep -E '^CLAUDE_MODEL=' "$DIR/.env" | cut -d= -f2- || true)"
  BUDGET_OLD="$(grep -E '^MAX_BUDGET_USD=' "$DIR/.env" | cut -d= -f2- || true)"
fi
MODEL="${CLAUDE_MODEL:-${MODEL_OLD:-sonnet}}"
BUDGET="${MAX_BUDGET_USD:-${BUDGET_OLD:-2}}"

umask 077
"$DIR/.venv/bin/python" - "$DIR" "$TG_TOKEN" "$TG_USER" "$API_KEY" "$MODEL" "$BUDGET" <<'EOF'
import sys
from pathlib import Path
d, token, user, key, model, budget = sys.argv[1:]
values = {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": user, "ALLOWED_USER_ID": user,
          "ANTHROPIC_API_KEY": key, "CLAUDE_MODEL": model, "MAX_BUDGET_USD": budget}
lines = []
for line in (Path(d) / ".env.example").read_text().splitlines():
    k = line.split("=", 1)[0]
    lines.append(f"{k}={values[k]}" if "=" in line and k in values else line)
(Path(d) / ".env").write_text("\n".join(lines) + "\n")
EOF
echo ".env written (secrets from SSM $PREFIX)."
