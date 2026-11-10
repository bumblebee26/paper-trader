# Setup

Builds on a working **stocks_lakehouse** (its state bucket, Telegram parameters and CI users). Windows
Command Prompt commands; run them from `C:\D Drive\Claude Code`. About 45 minutes.

## Part A: AWS stack

### A1. Sign in (same as the lakehouse)

```
aws login --profile stocks --region ap-southeast-2
set AWS_PROFILE=stocks
for /f "delims=" %i in ('aws configure export-credentials --profile stocks --format windows-cmd') do %i
aws sts get-caller-identity
```

### A2. Get the code

```
cd "C:\D Drive\Claude Code\paper-trader"
git fetch
git checkout dev
git pull
```

### A3. Create the stack

```
cd infra\aws
copy backend.hcl.example backend.hcl
notepad backend.hcl
```

Put in the lakehouse state bucket name (`stocks-lakehouse-tfstate-<account-id>`), keep
`key = "paper-trader/aws/terraform.tfstate"`, save, then:

```
terraform init -backend-config=backend.hcl
terraform apply
```

Expect about 30 resources: the state bucket, three Lambdas (kicker, guardian, responder) and their
schedules (kicker ones disabled until Part F), IAM roles, the `stocks-gha-trader` user and one SSM
placeholder. Note the outputs.

Commit the new `infra\aws\.terraform.lock.hcl` to `dev` so CI uses the same provider versions.

## Part B: Secrets

### B1. Session access key

```
aws iam create-access-key --user-name stocks-gha-trader
```

Keep the `AccessKeyId` and `SecretAccessKey` for C3.

### B2. Claude token (Pro/Max)

```
claude setup-token
```

Sign in in the browser; copy the `sk-ant-oat01-...` token. It lasts a year: add a calendar reminder.

### B3. GitHub token for the kicker

GitHub → your picture → **Settings → Developer settings → Personal access tokens → Fine-grained tokens →
Generate new token**:
- Name `paper-trader-kicker`, expiration 1 year.
- Repository access: **Only select repositories** → `paper-trader`.
- Permissions → Repository → **Actions: Read and write**.

```
aws ssm put-parameter --name /stocks-trader/github-dispatch-token --type SecureString --overwrite --value YOUR_GITHUB_TOKEN --region ap-southeast-2
```

### B4. CI keys for this repo

GitHub won't show the lakehouse's secret values again, so give this repo its own keys for the same two
users (an IAM user can have two keys):

```
aws iam create-access-key --user-name stocks-gha-plan
aws iam create-access-key --user-name stocks-gha-apply
```

## Part C: GitHub

### C1. Make the repo public

**Settings → General → Danger Zone → Change visibility → Public.** The repo holds no secrets (they're in
SSM and GitHub secrets). This enables branch rules on a free account and unlimited Actions minutes.

Then **Settings → Actions → General → Fork pull request workflows from outside collaborators** →
**Require approval for all outside collaborators**.

### C2. Branch rules (same as the lakehouse)

**Settings → Rules → Rulesets → New branch ruleset**, name `protect-main`:
- Enforcement **Active**, Target **Include default branch**.
- Tick **Restrict deletions**, **Block force pushes**, **Require a pull request before merging**
  (required approvals **0**).
- Tick **Require status checks to pass** and add: `Python tests and MCP server`, `terraform fmt`,
  `terraform validate (aws)` (they appear after CI has run once on `dev`).

### C3. Secrets and variables

**Settings → Secrets and variables → Actions:**

| Tab | Name | Value |
|---|---|---|
| Variables | `TF_STATE_BUCKET` | `stocks-lakehouse-tfstate-<account-id>` |
| Variables | `TRADER_STATE_BUCKET` | output `state_bucket` from A3 |
| Secrets | `AWS_PLAN_ACCESS_KEY_ID`, `AWS_PLAN_SECRET_ACCESS_KEY` | `stocks-gha-plan` key from B4 |

**Settings → Environments:**

| Environment | Secrets |
|---|---|
| `production` | `AWS_APPLY_ACCESS_KEY_ID`, `AWS_APPLY_SECRET_ACCESS_KEY` (`stocks-gha-apply` key from B4) |
| `trading` | `AWS_TRADER_ACCESS_KEY_ID`, `AWS_TRADER_SECRET_ACCESS_KEY` (B1), `CLAUDE_CODE_OAUTH_TOKEN` (B2) |

For both environments, under **Deployment branches and tags** choose **Selected branches** → `main`.

## Part D: First pull request

Open a PR from `dev` into `main`. CI runs, and a comment shows the `terraform plan`. If A3 was applied,
expect **No changes** (or only a Lambda code re-upload). Merge it.

## Part E: Test a session

GitHub → **Actions → Trading session → Run workflow** → branch `main`, phase **test**.

Within about 3 minutes Telegram shows:
`[MOCK] GitHub session OK. Dummy balance Rs 40000. Lakehouse data: stocks_lakehouse gold tables. Prices: ok ...`

| If it says | Do |
|---|---|
| `Lakehouse data: unavailable` | Check the lakehouse's Databricks token and warehouse parameters (TROUBLESHOOTING) |
| `Prices:` with an error | Yahoo is blocking GitHub runners; tell Claude to switch the data source |
| Nothing arrives | Open the run's log in the Actions tab |

## Part F: Turn on exact-time starts

On `dev`, set `dispatch_enabled = true` in `infra/aws/project.auto.tfvars`, push, open a PR, merge.
From then on sessions start at 09:08 and 12:27 IST; GitHub's cron stays as a backup.

The first real session is the next weekday morning.
