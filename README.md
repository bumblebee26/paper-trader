# Paper Trader – mock NSE intraday trading with Telegram approvals

Dummy money only (Rs 10,00,000). No broker, demat or bank account. Prices come free from
Yahoo Finance (can lag a few minutes). Every trade needs your YES on Telegram.

## 1. Install (once)

Install Python 3.10 or newer from python.org (on Windows, tick "Add Python to PATH").

Unzip this folder somewhere permanent, for example:
- Windows: `C:\Users\<you>\paper-trader`
- Mac: `/Users/<you>/paper-trader`

Open a terminal in that folder (Windows: PowerShell, Mac: Terminal) and run:

```
python -m pip install -r requirements.txt
```
(On Mac use `python3` instead of `python` everywhere.)

## 2. Add your secrets

Copy `.env.example` to a new file named `.env` and paste your bot token on the first line.
Your ID 8674025639 is already filled in. Never share `.env`.

## 3. Test Telegram

```
python server.py --test
```
You should see "Telegram bot OK: @mock_26_trader_bot", then a test message with YES / NO
buttons arrives on your phone. Tap one; the terminal prints "Setup complete".

## 4. Connect to Claude Desktop

Claude Desktop → Settings → Developer → Edit Config. Add this inside the file
(use your real folder path; on Windows double the backslashes):

Windows:
```json
{
  "mcpServers": {
    "paper-trader": {
      "command": "python",
      "args": ["C:\\Users\\<you>\\paper-trader\\server.py"]
    }
  }
}
```
Mac:
```json
{
  "mcpServers": {
    "paper-trader": {
      "command": "python3",
      "args": ["/Users/<you>/paper-trader/server.py"]
    }
  }
}
```
Fully quit and reopen Claude Desktop. In a new chat, ask "What paper-trader tools do you
have?" and you should see the tools listed below.

## 5. Run a trading day (weekdays)

Keep the computer awake and Claude Desktop open. Around 09:20 IST start a chat with:

> Start today's mock intraday session with paper-trader. Use my intraday-strategy-india,
> trade-approval-telegram and trade-journal skills. Watchlist: RELIANCE, HDFCBANK, ICICIBANK,
> INFY, TCS, SBIN, AXISBANK, LT. Send me the focus list, then every 5 minutes run
> scan_setups and monitor_positions, propose only the good setups, and send the
> end-of-day report after square-off.

On your phone:
- Tap **YES - place** or **NO - skip** on each proposal. No reply in 3 minutes = skipped.
- Send `EXIT RELIANCE` any time to close that dummy position.

## Tools Claude gets

| Tool | What it does |
|---|---|
| account_status | Dummy balance, P&L, open positions, margin |
| market_snapshot | Gap %, previous-day high/low, VWAP, opening range |
| scan_setups | ORB / VWAP / previous-day high-low candidates with numbers + Nifty context |
| propose_trade | Risk gate + position size + Telegram message with YES/NO buttons |
| wait_for_decision | Waits for your tap (call again while pending) |
| execute_approved_trade | Simulated fill, only for approved proposals; re-checks price first |
| monitor_positions | Stop/target hits, your EXIT messages, 15:10 square-off |
| close_position | Close one dummy position |
| notify | Plain Telegram message |
| end_of_day_report | Day P&L, win rate, and what skipped trades would have made |

## Files it writes (in `journal/`)

- `ledger.json` – dummy balance and positions. Delete it to reset to Rs 10 lakh.
- `YYYY-MM-DD.csv` – every proposal, reply, fill and exit.
- `YYYY-MM-DD-report.json` – the day's summary.

## Rules (change in `.env`)

1% risk per trade, max 3 open positions, max 5 trades a day, stop after a 2% daily loss,
minimum reward:risk 1.5, entries 09:30–14:30, square-off 15:10, 5x simulated intraday margin,
0.05% slippage and Rs 20 brokerage per order. NSE holidays are not detected, so skip those days.

## Troubleshooting

- "TELEGRAM_BOT_TOKEN is not set" → `.env` missing or token not pasted.
- "Conflict: terminated by other getUpdates request" → another copy is running; close the
  terminal test or restart Claude Desktop.
- Test message arrives but taps are ignored → ALLOWED_USER_ID must be 8674025639.
- "no data" before 09:15 or on holidays is normal.

## Running it on AWS instead

See `aws/SETUP-AWS.md`. Claude Code runs the session on a server on a weekday schedule,
so your computer can stay off.

This is a learning simulator, not investment advice.
