# Operations

## What runs on its own (IST)

| Time | Job | Writes |
|---|---|---|
| 08:45 | Lakehouse pre-market brief | Telegram |
| 09:08 | Kicker → morning session | Telegram plan, `plans/<date>.json`, journal |
| 09:16 | GitHub cron backup (skips if already claimed) | — |
| 09:20–12:25 | Morning cycles every 5 minutes | proposals, fills, exits |
| 12:27 / 12:36 | Afternoon session (kicker / backup) | — |
| 15:10 | Square-off, then end-of-day report | `reports/<date>.json` |
| 15:12 | Guardian: square-off if no live session | — |
| 15:14 | Guardian: puts the swing pool's idle cash into NIFTYBEES (once; nothing to do afterwards) | `state/pools/swing.json` |
| 15:40 | Guardian: report if none was sent; daily portfolio snapshot (circuit breaker at 12% below the peak) | `state/portfolio.json` |
| 15:45 | Guardian: export the day's journal and a portfolio snapshot (every pool and holding, swing and long-term prices refreshed) | `exports/date=<date>/events.jsonl` + lakehouse volume `trades/`; `exports/portfolio/date=<date>/portfolio.jsonl` + volume `portfolio/` |
| Fri 17:55 | Guardian: weekly report in plain words (results, setups, your calls vs the suggestion and reviewer, background rules, all pools) | `reports/weekly/<date>.json` |
| Sat 07:15 | GitHub Actions *Company fundamentals*: NSE's Nifty 200 list (the 250 most traded NSE stocks if the list can't be read) plus the watchlist; financials → lakehouse `landing/fundamentals/` (feeds `stocks.gold.company_fundamentals`), the list → `landing/index_members/` | `fundamentals/date=<date>/fundamentals.jsonl`, `index_members/date=<date>/nifty200.jsonl` |
| Sat 10:15 | GitHub Actions *Long-term shadow*: what the long-term pool would buy or sell this week (shadow ledger `state/pools/long_term_shadow.json`, never in the real portfolio); Telegram report | `shadow/longterm/<date>.json` |
| every 2 min | Responder: answers Telegram commands when no session is live | — |

**NSE holidays.** Fetched live from NSE's own list (`nseindia.com/api/holiday-master`, `trader/nse_calendar.py`):
each session claim on the GitHub runner reads it and caches it in S3 (`state/nse_holidays.json`); the Lambdas
only read the cache, so a slow NSE never delays them. On a holiday the morning session sends one
"No market today" message and stops before Claude starts, the afternoon session and the 15:14 park are skipped,
and `/pause` and "next session" dates skip to the next trading day. New years appear as soon as NSE publishes
them. If NSE and the cache both fail, weekdays count as trading days and the session stops itself by 09:30
when it finds no prices.

## Risk limits (intraday)

| Limit | Default | Setting in `trader/config.py` |
|---|---|---|
| Trades per day | 10 (0 = no limit) | `MAX_TRADES_PER_DAY` |
| 1R (one trade's risk) | Start-of-day balance × today's risk %: Rs 400, or Rs 300 at VIX ≥ 20, Rs 200 at VIX ≥ 30 | `RISK_PER_TRADE_PCT` (VIX scaling in `trader/brief.py`) |
| Daily loss limit | 4R (Rs 1,600; Rs 1,200 at VIX ≥ 20), counting today's loss **plus the risk in open positions**: a new trade is sized to fit what's left, or blocked. Lifts again if a winner brings the loss back under | `DAILY_LOSS_LIMIT_R` (`DAILY_LOSS_LIMIT_RS` > 0 = a fixed rupee limit instead) |
| Weekly brake | Week's loss reaches 8R: risk per trade halved until Monday | `WEEKLY_LOSS_LIMIT_R`, `WEEKLY_BRAKE_FACTOR` |
| Cost filter | Blocks a trade whose round-trip fees and slippage exceed 30% of its risk at full size (stops tighter than about 0.6% of the price, about 1% at VIX ≥ 30); trades made smaller by the weekly brake or the room left aren't penalised for it | `MAX_COST_SHARE_PCT` |
| Cool-off | 30 min without new proposals after 3 losses in a row | `COOLOFF_AFTER_LOSSES`, `COOLOFF_MIN` |
| Open positions | 3 in total, 1 per sector | `MAX_OPEN_POSITIONS`, `MAX_OPEN_PER_SECTOR` |
| Trades per sector | 2 a day | `MAX_TRADES_PER_SECTOR` |
| Position size | 1% risk (less when VIX is high), and at most 30% of the balance as margin | `RISK_PER_TRADE_PCT`, `MAX_POSITION_MARGIN_PCT` |
| Focus list | up to 15 stocks, at least 6 sectors | `FOCUS_MAX`, morning prompt in `trader/session_ctl.py` |

`/ledger` shows today's limit, the room left and the week so far.

**Auto mode** (`/auto on`, `/auto off`; on by default via `AUTO_MODE_DEFAULT`): proposals with a combined
suggestion of at least `AUTO_MIN_YES_PCT` (55%) are placed without a YES; the rest are skipped (journal
`rejected` with `AUTO-NO`, and `passed_outcome` with `auto_skipped`). `AUTO_MAX_TRADES_PER_DAY` (6) replaces the
10-trade cap; the weekly brake switches auto mode off. Each message has 👍/👎 buttons (journal `user_feedback`;
feedback never changes a trade) and, for placed trades, EXIT. A midday summary arrives at `MIDDAY_SUMMARY_AT`
(12:30) and the day report counts placed, skipped and rated decisions.

**Track record** (`trader/track.py`): read once per session from `stocks.gold.track_record` and shown in every
proposal with its counts (rows under 8 outcomes are marked "too few to judge"); `premarket_context` gives it to
Claude, whose rules allow adjusting its own score only on judged rows.

**Exit rules on trial** (`trader/exits.py`, from 11 Nov; nothing changes real trades): after the close each
trade is replayed on its 1-minute candles under the current exits and four alternatives (stop to entry at
+1R, sell half at +1R, a trailing stop, a 60-minute time stop), plus a day profit lock (once the day is +3R, no
new trades if it falls back to half). The day report and the weekly report show how each would have done;
journal events `exit_variants` and `profit_lock_shadow`. Adopt one only at a review, after 15+ trades.

## Making a change

```
1. Commit to dev and push            → CI: tests, MCP check, terraform fmt + validate
2. Open a pull request dev → main    → terraform plan posted as a PR comment
3. Merge                             → terraform apply; the next session uses the new code
```

| To change | Edit |
|---|---|
| Trade selection rules, bias handling | `prompts/trading_rules.md` |
| Watchlist and sectors | `trader/sectors.py` (36 stocks, 11 sectors; sector index for the market check) |
| Pools, circuit breaker, weekly report | `trader/pools.py`, `trader/weekly.py`, capitals in `trader/config.py` |
| Fundamentals universe | `NIFTY200_URLS` and fallback query in `jobs/fundamentals.py`; schedule in `.github/workflows/fundamentals.yml` |
| Risk limits, capital, costs, times | defaults in `trader/config.py` (or `env:` in the workflow) |
| VIX → risk scaling, bias thresholds | `risk_for_vix` / `build_plan` in `trader/brief.py` |
| Setups (ORB, VWAP, PDH/PDL) | `trader/scanner.py` |
| Session times | `trader/session_ctl.py`, `infra/aws/schedules.tf`, cron in `trading-session.yml` |
| Kicker / guardian on or off | `infra/aws/project.auto.tfvars` |
| Models | `CLAUDE_MODEL` (trading agent) and `REVIEWER_MODEL` in `trading-session.yml` |

Run tests with `python -m unittest discover -s tests` and `terraform fmt -recursive infra` before pushing.

## Manual runs

| Want to | Do |
|---|---|
| Test without trading | Actions → Trading session → Run workflow → `test` |
| Long-term backtest | Actions → Long-term backtest → Run workflow (about 2 min, no Claude): the phase 4 selection as of 1 Jul 2025, held to today, vs NIFTYBEES. Universe `nifty200` (default, today's index list) or `traded` (the 250 most traded stocks of Apr–Jun 2025, no hindsight). Results in `backtests/longterm/<date>.json` |
| Swing backtest | Actions → Swing backtest → Run workflow (about 2 min, no Claude). Mode `study` (default) tunes 16 rule versions on 15 May 2025–30 Apr 2026 and judges the best once on the months from 1 May 2026, against the original rules and holding NIFTYBEES; `original` runs the first four variants over the whole period. Telegram summary; full results in `backtests/swing/<date>.json` in the state bucket |
| Re-run a phase today | Delete `sessions/<date>/<phase>` from the state bucket, then Run workflow |
| Skip trading today | Actions → Trading session → **Disable workflow** (enable it again afterwards) |
| All commands | Send `/help` (or `/prompts`, or "what can you do?") on Telegram; they're also in the bot's `/` menu |
| Stop new proposals for a day | Send `/pause`: before 14:30 on a trading day it applies to today, otherwise to the next trading day. Open positions are still managed; `/resume` undoes it |
| See today's plan again | Send `/plan` |
| A company's latest filings | Send `/news SYMBOL` (NSE announcements, results, board meetings; from the lakehouse news feed) |
| All pools | Send `/portfolio`: intraday (live), swing Rs 60,000 (held in NIFTYBEES) and long-term Rs 1,00,000 (shadow mode until the 18 Dec review) |
| Kill switch | Send `/stop`: no new proposals on any day until `/resume`; open positions are still managed |
| Close a position | Send `EXIT SYMBOL` on Telegram |
| See the money | Send `LEDGER` (or `BALANCE`) on Telegram: balance, in use (margin), free, exposure, open positions with live P&L. Answered within ~30 s during a session, ~2 min otherwise |
| Reset the intraday pool to Rs 40,000 | Delete `state/ledger.json` from the state bucket |
| Pool ledgers | Swing, long-term and the long-term shadow: `state/pools/<pool>.json` and `journal/pools/<pool>/<date>.csv` |
| Resize a pool | Change `STARTING_CAPITAL` (intraday), `SWING_CAPITAL` or `LONG_TERM_CAPITAL` in `trader/config.py`; the next day the balance moves by the same amount, keeping the P&L so far |
| Fetch company fundamentals now | Actions → Company fundamentals → Run workflow (tick *dry run* to only print) |
| Re-send a day (or range) to the lakehouse | `aws lambda invoke --function-name stocks-trader-guardian --region ap-southeast-2 --cli-binary-format raw-in-base64-out --payload "{\"action\":\"export\",\"date\":\"2026-11-09\"}" out.json` (or `"start"`/`"end"`). A single `date` also writes a portfolio snapshot under that date, taken from the pools as they are now; a range re-sends journals only |

```
aws s3 rm s3://stocks-trader-state-<account-id>/state/ledger.json --region ap-southeast-2
```

## Monitoring

| Check | How |
|---|---|
| Session log | GitHub → Actions → Trading session → the run |
| Saturday jobs and backtests | GitHub → Actions → Company fundamentals, Long-term shadow, Swing backtest or Long-term backtest → the run |
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

Models (set in `.github/workflows/trading-session.yml`): the trading agent runs **Opus** (`CLAUDE_MODEL: opus`)
and the independent reviewer **Sonnet** (`REVIEWER_MODEL`). Opus uses the plan's limits
noticeably faster than Sonnet; set `CLAUDE_MODEL: sonnet` to go back.

## Security checks

`.github/workflows/security.yml` runs on every pull request, on main and every Monday 03:47 IST; it uses no
secrets. Findings are in the repository's **Security → Code scanning** tab and in each job's log.

| Check | Looks for | Fails the PR on |
|---|---|---|
| CodeQL (Python, workflows) | Injection, request forgery, unsafe deserialisation, secrets in logs | High severity (`.github/scripts/sarif_summary.py`) |
| zizmor | Attacks on GitHub Actions: script injection, over-broad tokens, risky triggers, unpinned actions | High severity |
| Checkov | Risky Terraform settings: public buckets, wide IAM, missing encryption | Report only; reviewed exceptions with reasons in `.checkov.yaml` |
| pip-audit (paper-trader) | Known vulnerabilities in `requirements.txt` | Any |
| Dependabot | Saturday grouped update PRs for actions and pip, minor and patch versions only (majors by hand); the Terraform plan is skipped on its PRs (no secrets) | — |

Every action is pinned to a commit hash with its version in a comment, so a hijacked tag can't swap in
other code; Dependabot keeps the hashes current. Turn on in **Settings → Code security**: secret scanning
with push protection, Dependabot alerts and security updates.

