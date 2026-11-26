# Operations

## What runs on its own (IST)

| Time | Job | Writes |
|---|---|---|
| 08:30 | Lakehouse pre-market brief | Telegram |
| 08:55 | Kicker → morning session (plan held) | `plans/<date>.json`, journal |
| 09:00 | GitHub cron backup (skips if already claimed) | — |
| 09:04 / ~09:10 | Pre-open auction read: indicative (saved) / final, sent with the plan | Telegram plan, `preopen/` |
| 09:20–12:25 | Morning cycles every 5 minutes, starting a few seconds after each candle closes (stops checked every minute between them; a fresh filing on a tradable stock starts a cycle at once). From 20 Nov a trade also closes early on a real break of its exit line (two 5-minute closes past it, or one 0.2%+ past it) | proposals, fills, exits |
| 09:45 | Guardian: scanner check (`screen.py`): the morning's strict picks from `gold.technical_screen` against the live price and opening volume; Telegram note with a Swing buy button for triggered or in-zone picks | journal `scanner_check` |
| 12:27 / 12:36 | Afternoon session (kicker / backup) | — |
| 15:10 | Square-off, then end-of-day report, with the day's paper tests (opening-range test, from 21 Nov the 09:15 opening buy and the gap fade; each fill checked against the real minute) and, from 23 Nov, the selector's score for today and picks for tomorrow | `reports/<date>.json`, journal `exit_variants`, `orb_shadow`, `fill_check`, `selector`; `state/selector.json` |
| 15:12 | Guardian: square-off if no live session | — |
| 15:14 | Guardian: puts the swing pool's idle cash into NIFTYBEES (once; nothing to do afterwards) | `state/pools/swing.json` |
| 15:40 | Guardian: report if none was sent; daily portfolio snapshot (circuit breaker at 12% below the peak) | `state/portfolio.json` |
| 15:45 | Guardian: export the last 4 days' journals (so late events get through) and a portfolio snapshot (every pool and holding, swing and long-term prices refreshed; shadow pools as `shadow_pool` rows), and every pool buy and sell (real and shadow pools; the first run backfills from 18 Nov). Also, once a day, renewal reminders 30, 7, 3 and 1 days before a token or the AWS free plan expires (`trader/expiry.py`; dates in S3 `config/expiries.json` override the built-in estimates) | `exports/date=<date>/events.jsonl` + lakehouse volume `trades/`; `exports/portfolio/date=<date>/portfolio.jsonl` + volume `portfolio/`; `exports/pool_trades/date=<date>/trades.jsonl` + volume `pool_trades/` |
| 15:50 | Guardian: swing shadow test (`swing_shadow.py`): yesterday's `gold.swing_signals` bought at today's open, exits on today's candle; then the technical-scanner shadow (`swing_tech.py`): yesterday's strict picks from `gold.technical_indicators`, entered only if today reaches the scanner's level, exits at its stop, T2 or after 15 trading days; then the momentum + news shadow (`swing_momentum.py`, from 18 Nov): yesterday's top of `gold.momentum_ranks` bought at today's open (6 holdings, 2% risk), stop 2 x ATR trailing under the highest close, out after 20 trading days, and its concentrated copy (`swing_mom_conc_shadow`: 3 holdings, up to a third each, 3% risk, no sector cap). Telegram note when something moves. From 24 Nov the **real swing pool runs the same momentum + news rules in auto mode** (`/swing auto on|off`): buys at the day's open paid for by selling NIFTYBEES, the same trailing stops and 20-day exit, a Telegram message for every move; a stock bought by hand is taken over by these rules. Also sells your own swing holdings whose stop or target was hit today | `state/pools/swing_shadow.json`, `state/pools/swing_tech_shadow.json`, `state/pools/swing.json`, `state/pools/swing_mom_shadow.json`, `state/pools/swing_mom_conc_shadow.json`, `shadow/swing/<date>.json`, `shadow/swing_tech/<date>.json`, `shadow/swing_mom/<date>.json`, `shadow/swing_mom_conc/<date>.json`, `journal/pools/<pool>/` |
| 16:10 | GitHub Actions *Model shadow*, started by the AWS kicker (from 26 Nov; GitHub's own cron ran it 8-11 hours late). It scores the last finished session, so a late or manual run still scores the right day; Actions -> Model shadow -> Run workflow takes a `day` to re-score one (`jobs/model_shadow.py`, no Claude, paper only): 5-minute candles for every tradable stock in `gold.intraday_universe` (cached in S3 so history grows past Yahoo's 60 days); the four shadow models and the stop variants judged walk-forward on every scanner setup, and today's real trades replayed under each; short Telegram note | `models/bars5m/`, `models/results/<date>.json`, `models/results/history.json`, journal `model_shadow` and `model_backtest` (reach the lakehouse with the 23:30 export) |
| 23:30 | Guardian: export the last 4 days' journals again, so late events reach the lakehouse overnight | same journal files |
| Fri 17:55 | Guardian: weekly report in plain words (results, setups, your calls vs the suggestion and reviewer, background rules, all pools; from 21 Nov also the auto-bar ladder, score sizing, the selector, paper fills vs the real minute and the swing holding-period study) | `reports/weekly/<date>.json` |
| Sat 07:15 | GitHub Actions *Company fundamentals*: NSE's Nifty 200 list (the 250 most traded NSE stocks if the list can't be read) plus the watchlist; financials → lakehouse `landing/fundamentals/` (feeds `stocks.gold.company_fundamentals`), the list → `landing/index_members/` | `fundamentals/date=<date>/fundamentals.jsonl`, `index_members/date=<date>/nifty200.jsonl` |
| Sat 10:15 | GitHub Actions *Long-term shadow*: what the long-term pool would buy or sell this week (and, once `/longterm auto on` after the 18 Dec review, the real long-term pool doing the same) (shadow ledger `state/pools/long_term_shadow.json`, never in the real portfolio), plus the momentum shadow (`long_term_mom_shadow`: top 15 of `gold.momentum_ranks`, rebalanced on the first Saturday of each month, kept while in the top 30) and its concentrated copy (`long_term_mom_conc_shadow`: top 5, kept while in the top 10); Telegram reports | `shadow/longterm/<date>.json`, `shadow/longterm_mom/<date>.json`, `shadow/longterm_mom_conc/<date>.json` |
| every 2 min | Responder: answers Telegram commands (including `/analyze` and your own orders) when no session is live | ledger and pool files when you place an order |

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
| 1R (one trade's risk) | Start-of-day balance × today's risk %: Rs 400, or Rs 300 at VIX ≥ 20 (and when India VIX isn't available for the last session), Rs 200 at VIX ≥ 30. India VIX comes from NSE's own index file (`stocks.silver.nse_index_daily`), Yahoo only as the backup | `RISK_PER_TRADE_PCT` (VIX scaling in `trader/brief.py`) |
| Daily loss limit | 4R (Rs 1,600; Rs 1,200 at VIX ≥ 20), counting today's loss **plus the risk in open positions**: a new trade is sized to fit what's left, or blocked. Lifts again if a winner brings the loss back under | `DAILY_LOSS_LIMIT_R` (`DAILY_LOSS_LIMIT_RS` > 0 = a fixed rupee limit instead) |
| Weekly brake | Week's loss reaches 8R: risk per trade halved until Monday | `WEEKLY_LOSS_LIMIT_R`, `WEEKLY_BRAKE_FACTOR` |
| Cost filter | Blocks a trade whose round-trip costs (brokerage, STT, exchange and SEBI fees, GST, stamp duty) and slippage exceed 30% of its risk at full size (stops tighter than about 0.9% of the price, about 1.2% at VIX ≥ 30); trades made smaller by the weekly brake or the room left aren't penalised for it | `MAX_COST_SHARE_PCT` |
| Cool-off | 30 min without new proposals after 3 losses in a row | `COOLOFF_AFTER_LOSSES`, `COOLOFF_MIN` |
| Open positions | 5 in total (3 until 23 Nov), 1 per sector; together their stops must fit under the daily loss limit (above) | `MAX_OPEN_POSITIONS`, `MAX_OPEN_PER_SECTOR` |
| Sector strength | From 24 Nov: no BUY on a stock weaker than its sector today (no SELL on one stronger), by more than 0.3% since the open; blocked before the reviewer is asked | `LAGGING_SECTOR_LIVE` (0 = paper only) |
| Trades per sector | 2 a day | `MAX_TRADES_PER_SECTOR` |
| Position size | 1% risk (less when VIX is high), and at most 30% of the balance as margin | `RISK_PER_TRADE_PCT`, `MAX_POSITION_MARGIN_PCT` |
| Focus list | up to 15 stocks, at least 6 sectors | `FOCUS_MAX`, morning prompt in `trader/session_ctl.py` |

`/ledger` shows today's limit, the room left and the week so far.

**Auto mode** (`/auto on`, `/auto off`; on by default via `AUTO_MODE_DEFAULT`): a proposal is placed without a YES
only when the combined suggestion is over `AUTO_MIN_YES_PCT` (55%), the trading agent's own score is over
`AUTO_MIN_AGENT_PCT` (50%; exactly 55% / 50% is a coin flip and is skipped) and the independent reviewer answered
(`AUTO_NEEDS_REVIEWER`). The bar rises by `AUTO_LOSING_GROUP_BUMP` (5) when the trade's setup, mood, hour, sector
or direction (BUY or SHORT) has a judged, losing track record, and to `AUTO_FLIP_MIN_YES_PCT` (60%) for the
opposite side of a stock already proposed today; the rest are skipped (journal
`rejected` with `AUTO-NO`, and `passed_outcome` with `auto_skipped`). `AUTO_MAX_TRADES_PER_DAY` (6) replaces the
10-trade cap; the weekly brake switches auto mode off. Each message has 👍/👎 buttons (journal `user_feedback`)
and, while a placed trade is open, EXIT; the chosen 👍/👎 gets a ✅, and a 👍 or 👎 *reaction* counts too, for 7 days.
From 17 Nov ratings are for your own review only (dashboard page "Your calls": how liked and disliked trades
really did) and never train the system: the track record and Claude's context use real outcomes only. A midday
summary arrives at `MIDDAY_SUMMARY_AT` (12:30) and the day report counts placed, skipped and rated decisions.

**Track record** (`trader/track.py`): read once per session from `stocks.gold.track_record` and shown in every
proposal with its counts by setup, suggestion band, mood, hour, the stock's sector and the direction (rows under 8 outcomes are marked "too few to judge"); `premarket_context` gives it to
Claude, whose rules allow adjusting its own score only on judged rows.

**Exit rules on trial** (`trader/exits.py`, from 11 Nov; nothing changes real trades): after the close each
trade is replayed on its 1-minute candles under the current exits and twelve alternatives (stop to entry at
+1R, sell half at +1R, a trailing stop, a 60-minute time stop; from 17 Nov also no stop, the stop 1.5x and 2x
wider with fewer shares for the same rupee risk, and a stop that counts only on a 5-minute close with a hard
stop at 2x; from 19 Nov a 1.5R target, and yesterday's high (low for a short) as the target when it sits
between +0.5R and the planned target), plus a day profit lock (once the day is +3R, no
new trades if it falls back to half). The day report and the weekly report show how each would have done;
journal events `exit_variants` and `profit_lock_shadow`. Adopt one only at a review, after 15+ trades. The two
new targets also run in the opening-range test and the nightly model backtest, so the review has a larger sample;
the weekly report compares each rule with the current one on the trades it has a result for. From 20 Nov two more:
`early_any` (leave on any 5-minute close past the trade's exit line: how the session used to act on "invalid if")
and `early_strict` (only on a real break: two closes in a row past it, or one at least `EARLY_BREAK_PCT` (0.2%)
past it). `early_strict` is also the live rule: the engine closes the trade (`early_exit`), and Claude no longer
closes one itself when `invalid_if` is touched. The exit line is journaled on the `filled` row (`exit_line=` in the note).

**Live exit rule** (`EXIT_RULE_LIVE`, default `current`): the fixed stop and 2R target, with the strict early exit.
`trailing` (no fixed target; from +1R the stop follows 1R behind the best price, `engine.trailing_exit`, exit reason
`trailing_stop`) is built but used only if a review sets it. An automatic switch to it was built on 23 Nov and withdrawn
the same evening: the replay it relied on compared the rules without the live early exit. Your own trades always keep
the stop and target you set; each fill's note has `exit_rule=`.

**More paper tests** (from 21 Nov; nothing changes a real trade): at the day report, the 09:15 opening buy of the
morning plan's news-radar names and scanner strict picks (twice the normal slippage, stop 1.2% below, 2R target or
held to 15:10) and the gap fade (short a 3%+ gap-up from the news radar or stocks in play once a 5-minute close
breaks its first 15 minutes' low before 11:00, stop at the day's high), both as `orb_shadow` rows (`open_2r`,
`open_eod`, `fade_2r`, `fade_eod`); `fill_check` rows compare each paper entry fill with the real 1-minute candle
of its minute. The weekly report and `/shadow` add the auto-bar ladder (every proposal with a known outcome at bars
50/55/60/65/70%), score sizing (0.5x at 55–59%, 1x at 60–69%, 1.5x at 70%+), the background rule `sell_uptrend`
(no SELL while the plan's Nifty regime is uptrend) and the swing holding-period study (`trader/swing_hold.py`:
each swing shadow trade sold after at most 5 or 10 trading days).

**Selector** (`trader/selector.py`, from 23 Nov, paper only): after each day report it scores yesterday's picks
on today against the fixed rules (the live result, the 55% bar, the 5-minute breakout), then picks tomorrow's
exit rule, auto bar and entry from the last 20 trading days. Guardrails against luck: at least 30 trades,
averages shrunk towards zero by 30 trades, and a switch only for a Rs 40-a-trade edge (bar: per day). It never
places, sizes or closes a real trade.

**Breakouts on trial** (from 19 Nov, paper only): `trader/stretch.py` marks an ORB, PDH or PDL breakout
"stretched" when the price is already more than 70% of a usual day's range (last 5 sessions) off today's low
(high for a SELL), or more than 1% past VWAP. It is recorded on the proposal as a background rule (no points)
and judged in the weekly report; the nightly model backtest also tests skipping them (`skip_stretched`). The
opening-range test adds a "retest" version: a limit order at the range edge, filled only if the price comes
back within 60 minutes (a fill and stop-out in the same candle counts as a loss).

**Cycle timing** (from 19 Nov): the settings `WAKE_AFTER_CLOSE_SEC` (3), `CANDLE_WAIT_MAX_SEC` (25),
`MONITOR_EVERY_SEC` (60) and `SCAN_WORKERS` (4 stocks fetched at a time) set how fast each cycle starts. Journal
events `cycle_timing` and `proposal_timing` record, in seconds after the candle close, when each cycle and each
proposal happened (logging only).

**Your own trades** (`trader/custom.py`, from 16 Nov): `/analyze SYMBOL` gives the trader's view of any NSE stock
(code only, no Claude). `/buy` and `/short` trade on the intraday ledger (defaults: stop 1% away, target 2x the
risk, size one normal trade's risk within the 30% margin cap); `/swing buy|sell` and `/longterm buy|sell` (also
`/lt`) trade in the pools with delivery costs (a pool buy defaults to 10% of the pool; a swing buy sells NIFTYBEES if
the pool's cash is short). Each order is shown first with its costs and warnings and runs only when you tap
**Confirm** within 3 minutes, at the live price (not if it moved more than 1%). Orders run 09:15–15:00 (intraday)
or 09:15–15:30 (pools) on trading days. Your intraday trades don't use the trader's daily allowance or sector
counts, but their profit or loss counts toward the daily loss limit. Journal: setup `Custom` and `source custom`
(intraday), `pool_trade` events (pools); in the lakehouse `stocks.gold.custom_trades`.

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
| VIX → risk scaling, bias thresholds, India VIX source | `risk_for_vix` / `build_plan` in `trader/brief.py` |
| Early exit on a real break | `EARLY_BREAK_PCT` and `early_break` in `trader/exits.py`; `monitor` in `trader/engine.py` |
| Paper tests (opening buy, gap fade), selector guardrails | constants at the top of `open_buy_trade` / `gap_fade_trade` in `trader/engine.py`; `trader/selector.py` |
| Setups (ORB, VWAP, PDH/PDL) | `trader/scanner.py` |
| Technical scanner: `/screen`, the 09:45 check, its shadow pool (risk, 15-day limit) | `trader/screen.py`, `trader/swing_tech.py` (the filters themselves are in the lakehouse) |
| Your own trades (`/analyze`, `/buy`, `/short`, `/swing`, `/longterm`) | `trader/custom.py` (defaults at the top) |
| Groww and NSE buttons | `trader/links.py` (hand-checked Groww addresses for the watchlist) |
| Session times | `trader/session_ctl.py`, `infra/aws/schedules.tf`, cron in `trading-session.yml` |
| Kicker / guardian on or off | `infra/aws/project.auto.tfvars` |
| Models | `CLAUDE_MODEL` (trading agent) and `REVIEWER_MODEL` in `trading-session.yml` |

Run tests with `python -m unittest discover -s tests` and `terraform fmt -recursive infra` before pushing.

## Manual runs

| Want to | Do |
|---|---|
| Test without trading | Actions → Trading session → Run workflow → `test` |
| Long-term backtest | Actions → Long-term backtest → Run workflow (about 2 min, no Claude): the phase 4 selection as of 1 Jul 2025, held to today, vs NIFTYBEES. Universe `nifty200` (default, today's index list) or `traded` (the 250 most traded stocks of Apr–Jun 2025, no hindsight). Results in `backtests/longterm/<date>.json` |
| Re-run a phase today | Delete `sessions/<date>/<phase>` from the state bucket, then Run workflow |
| Skip trading today | Actions → Trading session → **Disable workflow** (enable it again afterwards) |
| All commands | Send `/help` (or `/prompts`, or "what can you do?") on Telegram; they're also in the bot's `/` menu |
| Stop new proposals for a day | Send `/pause`: before 14:30 on a trading day it applies to today, otherwise to the next trading day. Open positions are still managed; `/resume` undoes it |
| See today's plan again | Send `/plan` |
| The technical scanner | Send `/screen` for today's strict picks and near-misses with their levels, or `/screen TCS` for one stock against the six filters |
| A company's latest filings | Send `/news SYMBOL` (NSE announcements, results, board meetings; from the lakehouse news feed) |
| All pools | Send `/portfolio`: intraday (live), swing Rs 60,000 (held in NIFTYBEES) and long-term Rs 1,00,000 (shadow mode until the 18 Dec review) |
| Analyse any stock | Send `/analyze TCS` (add `intraday`, `swing` or `longterm` for one view) |
| Place your own trade | `/buy TCS 10 sl 2400 tgt 2480`, `/short TCS`, `/swing buy TCS 5`, `/longterm sell TCS`; then tap **Confirm** within 3 minutes |
| Kill switch | Send `/stop`: no new proposals on any day until `/resume`; open positions are still managed |
| Close a position | Send `EXIT SYMBOL` on Telegram |
| See the money | Send `LEDGER` (or `BALANCE`) on Telegram: balance, in use (margin), free, exposure, open positions with live P&L. Answered within ~30 s during a session, ~2 min otherwise |
| Reset the intraday pool only | Delete `state/ledger.json` from the state bucket (also forgets the Telegram offset and settings) |
| Pool ledgers | Swing, long-term and the shadow pools (`long_term_shadow`, `long_term_mom_shadow`, `long_term_mom_conc_shadow`, `swing_shadow`, `swing_tech_shadow`, `swing_mom_shadow`, `swing_mom_conc_shadow`): `state/pools/<pool>.json` and `journal/pools/<pool>/<date>.csv` |
| Resize a pool | Change `STARTING_CAPITAL` (intraday), `SWING_CAPITAL` or `LONG_TERM_CAPITAL` in `trader/config.py`; the next day the balance moves by the same amount, keeping the P&L so far |
| Fetch company fundamentals now | Actions → Company fundamentals → Run workflow |
| Run the shadow models now | Actions → **Model shadow** → Run workflow (untick *send* to keep it off Telegram; *max_symbols* limits the universe for a quick test). Up to 75 minutes |
| Re-send a day (or range) to the lakehouse | `aws lambda invoke --function-name stocks-trader-guardian --region ap-southeast-2 --cli-binary-format raw-in-base64-out --payload "{\"action\":\"export\",\"date\":\"2026-11-09\"}" out.json` (or `"start"`/`"end"`). A single `date` also writes a portfolio snapshot under that date, taken from the pools as they are now; a range re-sends journals only |

```
aws s3 rm s3://stocks-trader-state-<account-id>/state/ledger.json --region ap-southeast-2
```

## Monitoring

| Check | How |
|---|---|
| Session log | GitHub → Actions → Trading session → the run |
| Saturday jobs and backtests | GitHub → Actions → Company fundamentals, Long-term shadow, History backtests or Long-term backtest → the run |
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

