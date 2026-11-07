# Run the mock trader on AWS (Claude Code, headless, weekday schedule)

> **Shortcut:** `infrastructure/` has Terraform that does steps 1–8 for you, adds a
> start/stop schedule to cut EC2 cost, and keeps secrets in encrypted SSM parameters.
> See `infrastructure/README.md`. The manual steps below are the same setup by hand.

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

Code lives at github.com/bumblebee26/paper-trader (private).

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

## 4. Get the code from GitHub
The repo is private, so the server needs a read-only key for it (a "deploy key"):
```
ssh-keygen -t ed25519 -N "" -f ~/.ssh/paper_trader -C "aws-paper-trader"
cat ~/.ssh/paper_trader.pub
```
Copy the printed line. On GitHub: **bumblebee26/paper-trader → Settings → Deploy keys →
Add deploy key**, paste it, name it `aws`, leave "Allow write access" **unticked**, Save.
Back on the server:
```
cat >> ~/.ssh/config <<'EOF'
Host github-paper-trader
  HostName github.com
  User git
  IdentityFile ~/.ssh/paper_trader
EOF
ssh-keyscan github.com >> ~/.ssh/known_hosts
cd ~ && git clone git@github-paper-trader:bumblebee26/paper-trader.git
cd paper-trader
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

## Getting the latest code
Whenever the code on GitHub changes, run this on the server outside market hours:
```
cd ~/paper-trader && git pull && .venv/bin/pip install -r requirements.txt
```
Your `.env` and `journal/` are never touched by `git pull` (they're in `.gitignore`).
You usually don't need to: the schedule in `aws/crontab.txt` already pulls the latest code
every weekday at 08:45, before the first session. After a pull that changes
`aws/crontab.txt` itself, run `crontab aws/crontab.txt` again.

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
