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

### Proposals say `News: unavailable` (or a "news feed is ... minutes old" alert)
The trader reads `/Volumes/stocks/bronze/news/date=<today>/latest.jsonl`, written every 30 minutes
(08:00–19:30 IST, Mon–Fri) by the lakehouse's `stocks-fetch-news` Lambda. Check that Lambda's log
(`aws logs tail /aws/lambda/stocks-fetch-news --since 2h --region ap-southeast-2`): `All NSE news feeds failed`
means NSE blocked the request; anything else is usually the Databricks pusher token. Trading continues
without news.

### No long-term shadow report on Saturday
Actions → Long-term shadow → the run. `TABLE_OR_VIEW_NOT_FOUND ... longterm_candidates` means the lakehouse
hadn't refreshed after the 07:15 fundamentals job yet: re-run the workflow later. Nothing is bought for real
either way; the shadow ledger is `state/pools/long_term_shadow.json`.

### A backtest fails with `TABLE_OR_VIEW_NOT_FOUND`
The lakehouse table it reads is created by the pipeline after new files land (for example
`silver.index_members` after the first fundamentals run). Wait for the next refresh and run it again.

### `Data alert: market prices look stale`
No fresh Nifty candle for 20 minutes during market hours (Yahoo delay or outage). New proposals pause until
prices are fresh again; open positions are still checked. Nothing to do unless it lasts all day.

### `Prices: HTTP 429` or empty market data
Yahoo rate-limits or blocks some cloud addresses. Re-run the test later; if it persists, the data source
needs changing (`trader/market.py`).

### Company fundamentals: `More than half the companies failed: Yahoo is probably blocking; nothing pushed.`
Yahoo refused most requests from the GitHub runner. Nothing was written, so last week's figures stay in the
lakehouse. Run the workflow again later (Actions → Company fundamentals → Run workflow).

### Company fundamentals log says `Nifty 200 list unavailable: using the 250 most traded stocks instead`
NSE's index list couldn't be downloaded from any of `NIFTY200_URLS` in `jobs/fundamentals.py`. The run still
works on the 250 most traded NSE stocks, but no new `index_members/` file is pushed, so the lakehouse keeps the
last Nifty 200 list it had.

### Guardian export returns `portfolio_error`
The 15:45 journal export worked, but the portfolio snapshot failed; the reason is in `portfolio_error` and in
the guardian log (`portfolio export failed: ...`). Fix the cause, then re-send the day with the `export`
payload in OPERATIONS "Manual runs".

### No Nifty candle for today
Usually a market holiday that NSE's holiday list didn't cover (or the list couldn't be read: see
`state/nse_holidays.json`). The session finds no prices and stops by 09:30; nothing is traded.
