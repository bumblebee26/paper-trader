# Operations

## What runs on its own (IST, Mon–Fri)

| Time | Job | Writes |
|---|---|---|
| 08:45 | Lakehouse pre-market brief | Telegram |
| 09:08 | Kicker → morning session | Telegram plan, `plans/<date>.json`, journal |
| 09:16 | GitHub cron backup (skips if already claimed) | — |
| 09:20–12:25 | Morning cycles every 5 minutes | proposals, fills, exits |
| 12:27 / 12:36 | Afternoon session (kicker / backup) | — |
| 15:10 | Square-off, then end-of-day report | `reports/<date>.json` |
| 15:12 | Guardian: square-off if no live session | — |
| 15:40 | Guardian: report if none was sent | — |
| 15:45 | Guardian: export the day's journal | `exports/date=<date>/events.jsonl` + lakehouse volume `trades/` |

## Making a change

```
1. Commit to dev and push            → CI: tests, MCP check, terraform fmt + validate
2. Open a pull request dev → main    → terraform plan posted as a PR comment
3. Merge                             → terraform apply; the next session uses the new code
```

| To change | Edit |
|---|---|
| Trade selection rules, bias handling | `prompts/trading_rules.md` |
| Default watchlist | `DEFAULT_WATCHLIST` in `trader/server.py` |
| Risk limits, capital, costs, times | defaults in `trader/config.py` (or `env:` in the workflow) |
| VIX → risk scaling, bias thresholds | `risk_for_vix` / `build_plan` in `trader/brief.py` |
| Setups (ORB, VWAP, PDH/PDL) | `trader/scanner.py` |
| Session times | `trader/session_ctl.py`, `infra/aws/schedules.tf`, cron in `trading-session.yml` |
| Kicker / guardian on or off | `infra/aws/project.auto.tfvars` |
| Model | `CLAUDE_MODEL` in `trading-session.yml` |

Run tests with `python -m unittest discover -s tests` and `terraform fmt -recursive infra` before pushing.

## Manual runs

| Want to | Do |
|---|---|
| Test without trading | Actions → Trading session → Run workflow → `test` |
| Re-run a phase today | Delete `sessions/<date>/<phase>` from the state bucket, then Run workflow |
| Skip trading today | Actions → Trading session → **Disable workflow** (enable it again afterwards) |
| Close a position | Send `EXIT SYMBOL` on Telegram |
| Reset to Rs 1 lakh | Delete `state/ledger.json` from the state bucket |
| Re-send a day (or range) to the lakehouse | `aws lambda invoke --function-name stocks-trader-guardian --region ap-southeast-2 --cli-binary-format raw-in-base64-out --payload "{\"action\":\"export\",\"date\":\"2026-11-09\"}" out.json` (or `"start"`/`"end"`) |

```
aws s3 rm s3://stocks-trader-state-<account-id>/state/ledger.json --region ap-southeast-2
```

## Monitoring

| Check | How |
|---|---|
| Session log | GitHub → Actions → Trading session → the run |
| Today's journal | `aws s3 cp s3://stocks-trader-state-<account-id>/journal/<date>.csv - --region ap-southeast-2` |
| Ledger | `aws s3 cp s3://stocks-trader-state-<account-id>/state/ledger.json - --region ap-southeast-2` |
| Guardian / kicker logs | `aws logs tail /aws/lambda/stocks-trader-guardian --since 1h --region ap-southeast-2` |
| Schedules on? | `aws scheduler list-schedules --region ap-southeast-2 --query "Schedules[?starts_with(Name,'stocks-trader')].[Name,State]" --output table` |

## Rotating secrets

| Secret | Lives in | Expires | Rotate |
|---|---|---|---|
| Claude token | GitHub env `trading` → `CLAUDE_CODE_OAUTH_TOKEN` | 1 year | `claude setup-token` → update the secret |
| GitHub dispatch token | SSM `/stocks-trader/github-dispatch-token` | 1 year | New fine-grained token → `aws ssm put-parameter ... --overwrite` |
| Session AWS key | GitHub env `trading` | Never (rotate yearly) | `aws iam create-access-key --user-name stocks-gha-trader` → update → delete old |
| CI AWS keys | Repo secrets / env `production` | Never (rotate yearly) | Same as the lakehouse |
| Telegram, Databricks | Lakehouse SSM `/stocks/*` | Databricks pusher token: 180 days | Rotated by the lakehouse; the trader picks it up next session |

## Claude usage

Two ~3-hour sessions a day use a noticeable part of a Pro plan's limits (shared with your chats). The
rules keep calls minimal (one `run_cycle` + one wait per 5 minutes). If sessions stop on limits, the
guardian still squares off; consider Max, a cheaper `CLAUDE_MODEL`, or only the morning session.
