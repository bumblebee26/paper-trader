# Infrastructure (Terraform)

Creates everything in `aws/SETUP-AWS.md` automatically, in Mumbai (ap-south-1):

| Resource | Why |
|---|---|
| EC2 `t3.small`, Ubuntu 24.04, encrypted disk | Runs the trader and Claude Code |
| Security group with **no inbound ports** | Nothing on the internet can reach the server |
| IAM role (Session Manager + read `/paper-trader/*` params) | Console shell access without SSH; read its own secrets only |
| SSM Parameter Store (encrypted) | Telegram token, Anthropic key, GitHub deploy key |
| GitHub deploy key (generated) | Read-only access to the private repo |
| EventBridge Scheduler (optional, on by default) | Starts the server 08:30 and stops it 16:15 IST, weekdays only |
| AWS Budget (optional) | Emails you if monthly AWS spend passes your limit |

On first boot the server sets the IST timezone, clones the repo, installs Python packages
and Claude Code, writes `.env` from SSM, installs the cron schedule, and sends
"AWS server is set up" on Telegram.

## Before you start
- Install **Terraform 1.6+** and the **AWS CLI**.
- Run `aws configure` with an IAM user's access key in your AWS account.
- Have your Telegram bot token and an Anthropic API key ready (platform.claude.com).
- The region needs its **default VPC** (new accounts have one).

## Deploy
```
cd infrastructure
cp terraform.tfvars.example terraform.tfvars    # then paste your token and key in it
terraform init
terraform validate
terraform plan
terraform apply
```

Then, within 2 hours of `apply`:
1. Copy the `deploy_key_public` output.
2. Open the `add_deploy_key_url` output. Title `aws`, paste the key, leave **Allow write
   access** unticked, and click **Add key**.
3. The server is retrying the clone every 30 seconds. Within about 5 minutes you get
   "[MOCK] AWS server is set up" on Telegram.

To watch progress: AWS console → EC2 → paper-trader → **Connect → Session Manager**, then
run `sudo tail -f /var/log/paper-trader-bootstrap.log`.

Quick end-to-end check from that shell:
```
sudo -iu ubuntu
cd paper-trader && .venv/bin/python server.py --test && aws/run_session.sh test
```

## Day to day
- Code updates: push to GitHub; the server pulls at 08:45 on weekdays.
- Change a secret: edit `terraform.tfvars`, `terraform apply`, then on the server run
  `bash ~/paper-trader/aws/write_env.sh`.
- Server is **stopped outside 08:30–16:15 IST on weekdays** (if `enable_schedule = true`).
  To work on it then, start it in the EC2 console; it stops again at the next 16:15.
- Remove everything: `terraform destroy`. Your trading journal is on the server's disk,
  so copy `journal/` off first if you want to keep it.

## Costs (rough, check AWS pricing)
- t3.small running only ~39 hours a week: roughly US$5–7/month (EC2 + 12 GB disk).
  Always on: roughly US$15–20/month.
- SSM parameters (standard), Scheduler and Budgets at this size: free or cents.
- Claude API: billed by Anthropic, capped per session by `max_budget_usd`.

## Security notes
- `terraform.tfvars` and `terraform.tfstate` contain your secrets in plain text. Both are
  gitignored. Keep them on your machine only, or move state to an encrypted S3 backend.
- The deploy key is read-only; the server cannot push to GitHub.
- Claude sessions on the server can only use the paper-trader tools.
