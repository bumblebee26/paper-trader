#!/usr/bin/env bash
# Runs one part of the mock trading day with Claude Code, headless.
# Usage: run_session.sh morning|midday|afternoon|test
set -uo pipefail

PHASE="${1:-morning}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="$DIR/.venv/bin/python"
LOG_DIR="$DIR/journal/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date +%F)-$PHASE.log"

# Secrets and options: ANTHROPIC_API_KEY, CLAUDE_MODEL, MAX_BUDGET_USD live in .env
set -a; source "$DIR/.env"; set +a
MODEL="${CLAUDE_MODEL:-sonnet}"
BUDGET="${MAX_BUDGET_USD:-2}"

# Skip weekends (holidays are detected by Claude from missing market data)
[ "$PHASE" != "test" ] && [ "$(date +%u)" -ge 6 ] && exit 0

case "$PHASE" in
  morning)   END="11:30"; TASK="First call wait_for_next_candle(session_end='$END') so the 09:15 candle has closed. Then call account_status and market_snapshot for the watchlist and NIFTY. If NIFTY's last_candle is not dated today, wait one more candle and check again; if it is still not today, it is a market holiday: notify 'No market today' and stop. Otherwise notify a focus list of at most 8 stocks (one line each with gap % and why). Then loop: scan_setups on the focus list, monitor_positions, wait_for_next_candle(session_end='$END'), until session_over." ;;
  midday)    END="13:30"; TASK="Call account_status, then market_snapshot for the watchlist and pick a focus list of at most 8 stocks (no Telegram message needed). Then loop: scan_setups, monitor_positions, wait_for_next_candle(session_end='$END'), until session_over." ;;
  afternoon) END="15:20"; TASK="Call account_status, then market_snapshot for the watchlist and pick a focus list of at most 8 stocks. Then loop: scan_setups (only until 14:30), monitor_positions, wait_for_next_candle(session_end='$END'), until session_over. Make sure monitor_positions runs after 15:10 so everything is squared off, then call end_of_day_report." ;;
  test)      END="00:00"; TASK="Setup check only, no trading: call account_status, then notify exactly: '[MOCK] AWS setup OK. Dummy balance Rs <balance>.' Then stop." ;;
  *) echo "unknown phase $PHASE"; exit 1 ;;
esac

MCP_JSON="$(mktemp)"
cat > "$MCP_JSON" <<EOF
{"mcpServers": {"paper-trader": {"type": "stdio", "command": "$PY", "args": ["$DIR/server.py"]}}}
EOF

TOOLS="mcp__paper-trader__account_status,mcp__paper-trader__market_snapshot,mcp__paper-trader__scan_setups,mcp__paper-trader__propose_trade,mcp__paper-trader__wait_for_decision,mcp__paper-trader__execute_approved_trade,mcp__paper-trader__monitor_positions,mcp__paper-trader__close_position,mcp__paper-trader__notify,mcp__paper-trader__wait_for_next_candle,mcp__paper-trader__end_of_day_report"

cd "$DIR"
# flock: never run two sessions at once (Telegram allows only one reader)
flock -w 300 /tmp/paper-trader.lock \
  claude --bare -p "Today is $(date +%A\ %F). Run the $PHASE part of the mock session. $TASK" \
    --append-system-prompt-file "$DIR/aws/trading_rules.md" \
    --mcp-config "$MCP_JSON" \
    --allowedTools "$TOOLS" \
    --permission-mode dontAsk \
    --model "$MODEL" \
    --max-budget-usd "$BUDGET" \
    >> "$LOG" 2>&1
STATUS=$?
rm -f "$MCP_JSON"

if [ $STATUS -ne 0 ]; then
  "$PY" -c "import sys; sys.path.insert(0, '$DIR'); import telegram_bot as t; t.send('[MOCK] $PHASE session stopped with an error (code $STATUS). Check journal/logs on the server.')" || true
fi
exit $STATUS
