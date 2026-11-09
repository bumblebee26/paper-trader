# Troubleshooting

## AWS

### `explicit deny in a service control policy` (e.g. `ec2:DescribeVpcs`)
**Cause:** The free plan only allows certain services and regions. The first design used EC2 in Mumbai.
**Fix:** Use this repo's `infra/aws` (Lambda + S3 in Sydney). See ARCHITECTURE "free plan".

### `AccessDenied` creating `stocks-trader-*` roles from CI
**Cause:** The shared `stocks-gha-apply` user may only manage `role/stocks-*` and `user/stocks-gha-*`.
**Fix:** Keep `name_prefix` starting with `stocks-`. If you changed the lakehouse's `name_prefix`, update
`lakehouse_prefix` here.

### `Error acquiring the state lock`
**Cause:** A local apply and CI ran at the same time, or a run was killed.
**Fix:** Wait for the other run. If none is running: `terraform force-unlock <ID>` from `infra\aws`.

## GitHub Actions

### The session didn't start at 09:08
- Kicker off? `dispatch_enabled` in `project.auto.tfvars`.
- Token wrong or expired: `aws logs tail /aws/lambda/stocks-trader-kicker --since 2h --region ap-southeast-2`
  shows `HTTP 401` or `403`. Create a new fine-grained token (Actions: Read and write) and put it in SSM.
- `HTTP 422 ... Workflow does not have 'workflow_dispatch' trigger`: the workflow must be on `main`.

### The bot doesn't answer a command
- During a session (09:08–15:25 IST): wait for the next 5-minute cycle (~30 s while waiting for a candle).
- Outside sessions: the responder answers within ~2 minutes. Check it's on (`responder_enabled` in
  `project.auto.tfvars`) and its log:
  `aws logs tail /aws/lambda/stocks-trader-responder --since 30m --region ap-southeast-2`.
- `Conflict: terminated by other getUpdates request` in that log right after 09:08 or 12:27 is harmless: a session
  had just started and the responder stood back.

### "already ran today, skipping"
A phase runs once per day. To re-run: delete `sessions/<date>/<phase>` from the state bucket.

### `Telegram ... Conflict: terminated by other getUpdates request`
Two programs are reading the bot: laptop mode (Claude Desktop) and a GitHub session. Remove paper-trader
from Claude Desktop while sessions are on.

### Claude Code: `Invalid API key` / `401`
The `CLAUDE_CODE_OAUTH_TOKEN` secret is missing, expired or has spaces. Run `claude setup-token` again.
Also make sure no `ANTHROPIC_API_KEY` secret exists (the workflow unsets it anyway).

### Session ends early with a usage-limit message
Your plan's limit was reached. The guardian squares off at 15:12. See OPERATIONS "Claude usage".

## Data

### `Lakehouse data: unavailable`
`premarket_context` couldn't query Databricks. Check, in order: the lakehouse's
`/stocks/databricks-pusher-token` hasn't expired (180 days), the warehouse ID parameter exists, and the
gold tables refreshed (Databricks → Jobs & Pipelines). The day still runs with default risk and the
watchlist.

### `Prices: HTTP 429` or empty market data
Yahoo rate-limits or blocks some cloud addresses. Re-run the test later; if it persists, the data source
needs changing (`trader/market.py`).

### No Nifty candle for today
Market holiday: the morning session sends "No market today" and stops.
