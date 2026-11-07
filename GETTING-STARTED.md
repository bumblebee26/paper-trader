# Getting started: from zero to a running mock trader on AWS

Written for Windows (commands in **PowerShell**). Mac notes are in brackets.
Total time: about 45 minutes. Everything runs on dummy money.

## Part A – Accounts and keys (15 min)

**A1. Telegram bot** – already done: bot `@mock_26_trader_bot`, your ID `8674025639`.
Keep the bot token handy (BotFather → `/mybots` → your bot → API Token).

**A2. Claude access for the server – pick one**

*Option 1 – your Claude Pro or Max subscription (no platform.claude.com needed):*
1. Install Claude Code on your laptop. In PowerShell: `irm https://claude.ai/install.ps1 | iex`
   (Mac: `curl -fsSL https://claude.ai/install.sh | bash`). Reopen PowerShell after.
2. Run `claude setup-token`, sign in with your Claude account in the browser, and copy the
   token it prints (starts with `sk-ant-oat01-`). It is valid for **1 year**; put a reminder
   in your calendar for 11 months from now.
3. Usage counts against the same limits as your normal Claude chats. On Pro, a full trading
   day can use a lot of that; if you hit limits, use the API key option or Max.

*Option 2 – pay-per-use API key:*
1. https://platform.claude.com → **Billing**: add a small amount of credit (e.g. US$10).
2. **API Keys → Create Key**, name it `paper-trader`, copy it (starts with `sk-ant-api`).
   Each session stops at `max_budget_usd` (default $2).

**A3. AWS account and an access key for Terraform**
1. Sign up at https://aws.amazon.com if you don't have an account.
2. Console → **IAM → Users → Create user**, name `terraform`.
   Permissions: **Attach policies directly → AdministratorAccess**. Create.
3. Open the user → **Security credentials → Create access key → Command Line Interface**.
   Copy the **Access key ID** and **Secret access key** (shown once).

## Part B – Tools on your laptop (10 min)

**B1. Install** (PowerShell):
```
winget install Hashicorp.Terraform
winget install Amazon.AWSCLI
winget install Git.Git
```
(Mac: `brew install terraform awscli git`.) Close and reopen PowerShell, then check:
```
terraform -version
aws --version
git --version
```

**B2. Connect the AWS CLI**:
```
aws configure
```
Paste the access key ID and secret, region `ap-south-1`, output `json`.
Check it with `aws sts get-caller-identity` (it should print your account number).

**B3. Get the code** (a browser window asks you to sign in to GitHub the first time):
```
cd $HOME
git clone https://github.com/bumblebee26/paper-trader
cd paper-trader\infrastructure
```

## Part C – Create the AWS server (10 min)

**C1. Fill in your secrets**:
```
copy terraform.tfvars.example terraform.tfvars
notepad terraform.tfvars
```
(Mac: `cp` and `nano`.) Fill in `telegram_bot_token`, and **either** `claude_oauth_token` (Option 1) **or**
`anthropic_api_key` (Option 2; remove the `#` in front of it). Optionally
remove the `#` before `alert_email` and put your email for a budget alert. Save.
This file is gitignored and stays on your laptop.

**C2. Create everything**:
```
terraform init
terraform validate
terraform plan
terraform apply
```
Type `yes` when asked. It takes about 2 minutes. At the end it prints outputs.
If `validate` or `apply` shows an error, copy it to Claude.

**C3. Give the server read access to the repo** (within 2 hours of apply):
```
terraform output -raw deploy_key_public
terraform output -raw add_deploy_key_url
```
Open the URL, Title `aws`, paste the key, leave **Allow write access** unticked, then click
**Add key**.

**C4. Wait for Telegram.** Within about 5 minutes you get
"[MOCK] AWS server is set up". That's it.

## Part D – Check it works (5 min)

AWS console → **EC2 → Instances → paper-trader → Connect → Session Manager → Connect**.
In that browser terminal:
```
sudo -iu ubuntu
cd paper-trader
.venv/bin/python server.py --test
aws/run_session.sh test
```
- The first command sends a YES/NO test to Telegram. Tap one.
- The second runs Claude Code once (using your subscription token or API key) and you get "[MOCK] AWS setup OK. Dummy balance Rs 1000000."
- On weekends, "Market data: no data" is normal.

If setup hasn't finished: `sudo tail -f /var/log/paper-trader-bootstrap.log`.

## Part E – First trading day

- **Turn off any copy on your laptop** (remove paper-trader from Claude Desktop's config).
  Only one program can read your bot at a time.
- Next weekday: the server starts at 08:30 and pulls the latest code at 08:45. At about
  09:20 the focus list arrives on Telegram. Then tap **YES / NO** on proposals as they come.
- Send `EXIT RELIANCE` any time to close a position. The day report arrives around 15:20.
- **If the focus list says data is missing on a market day**, Yahoo may be blocking AWS.
  Tell Claude, and it will switch the data source.

## Later

| Want to... | Do this |
|---|---|
| Change code or rules | Edit on GitHub (or ask Claude); the server pulls at 08:45 |
| Change watchlist / selection rules | `aws/trading_rules.md` |
| Change risk limits | `.env.example` defaults, or `.env` on the server |
| Change a secret | Edit `terraform.tfvars`, `terraform apply`, then on the server `bash ~/paper-trader/aws/write_env.sh` |
| Pause trading | `terraform apply -var enable_schedule=false`, then stop the instance in EC2 |
| Delete everything | `terraform destroy` (copy `journal/` off the server first if you want it) |

Learning simulator only, not investment advice.
