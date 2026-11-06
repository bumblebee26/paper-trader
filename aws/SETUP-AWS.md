# Run the mock trader on AWS (Claude Code, headless, weekday schedule)

What happens each weekday (IST), with no computer of yours switched on:

| Time | What runs |
|---|---|
| 09:18 | Claude: holiday check, focus list on Telegram, then trades until 11:30 |
| 11:31 | Claude: trades until 13:30 |
| 13:31 | Claude: trades until 14:30, watches positions, squares off at 15:10, sends the day report |
| 15:12 | Backstop without Claude: squares off anything still open |
| 15:40 | Backstop without Claude: sends the report if Claude didn't |

The day is split into three sessions so no single session gets too long or too expensive.
State lives in `journal/ledger.json`, so each session picks up where the last one stopped.

You need: an AWS account, an Anthropic API key (platform.claude.com, pay per use), and
your Telegram bot token.

## 1. Launch a server
AWS Console → EC2 → **Launch instance**:
- Region (top right): **Asia Pacific (Mumbai) ap-south-1**
- Name: `paper-trader`
- Image: **Ubuntu Server 24.04 LTS**
- Instance type: **t3.small** (2 GB RAM; 1 GB is too tight for Claude Code)
- Key pair: create one and download the `.pem` file
- Network: allow SSH **from My IP** only. No other ports are needed.
- Storage: 8–10 GB is plenty

## 2. Connect
Easiest: select the instance → **Connect** → **EC2 Instance Connect** → Connect (opens a
terminal in your browser). Run everything below in that terminal.

## 3. Prepare the server
```
sudo timedatectl set-timezone Asia/Kolkata
sudo apt update && sudo apt install -y python3-venv unzip
```

## 4. Upload the kit
From your own computer (Mac Terminal or Windows PowerShell), in the folder with the zip:
```
scp -i path/to/your-key.pem paper-trader.zip ubuntu@<server-public-ip>:~
```
(Windows alternative: WinSCP, with the same .pem file.) Then on the server:
```
cd ~ && unzip paper-trader.zip && cd paper-trader
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
chmod +x aws/run_session.sh
cp .env.example .env
nano .env
```
In nano, paste your Telegram token and your Anthropic API key. Save with Ctrl+O, Enter,
then exit with Ctrl+X.

## 5. Check Telegram and market data from AWS
```
.venv/bin/python server.py --test
```
Tap the button on your phone. If it says "Market data: no data" **while the market is
open**, Yahoo is blocking the AWS address. Tell Claude and switch data sources before going
further. (Outside market hours "no data" can be normal.)

## 6. Install Claude Code
```
curl -fsSL https://claude.ai/install.sh | bash
claude --version
```
If `claude` isn't found, close and reopen the terminal.

## 7. Test the headless session
```
aws/run_session.sh test
```
Within about a minute you should get "[MOCK] AWS setup OK. Dummy balance Rs 1000000." on
Telegram. If not, read `journal/logs/<today>-test.log`.

## 8. Turn on the schedule
```
crontab aws/crontab.txt
crontab -l
```
If you unzipped anywhere other than `/home/ubuntu/paper-trader`, edit the paths in
`aws/crontab.txt` first.

**Stop the copy on your laptop** (close Claude Desktop's paper-trader, or remove it from
the config). Telegram lets only one program read your bot at a time.

## Everyday use
- Approve or skip on Telegram; send `EXIT RELIANCE` to close a position.
- Logs: `journal/logs/`. Trades: `journal/<date>.csv`. Reset money: delete `journal/ledger.json`.
- Change rules, model or budget: edit `.env` (`CLAUDE_MODEL`, `MAX_BUDGET_USD` per session).
- Change the watchlist or trade-selection rules: edit `aws/trading_rules.md`.
- Pause everything: `crontab -r` (turn it back on with step 8).

## Costs
- EC2 t3.small left on all month: roughly US$15–20 in Mumbai. Check the EC2 pricing page;
  new accounts usually get free credits.
- Claude API: each session stops at `MAX_BUDGET_USD` (default $2, so at most $6 a day).
  If a session hits the cap it stops early and Telegram tells you. The 15:12 backstop
  still squares off your positions. Raise the cap or use a cheaper model if this happens
  often. Check spend at platform.claude.com.

## Safety
- The Claude session can use only the paper-trader tools; everything else is denied
  (`--permission-mode dontAsk`). No shell, no file edits, no internet beyond the tools.
- Trades still need your YES; the code refuses to execute anything not approved.
- Keep `.env` private. It holds your bot token and API key.
