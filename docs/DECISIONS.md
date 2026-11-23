# Decision register

> Every decision the system runs on today, taken from the **code**, not from the other docs: where they disagree,
> the code is described here and the gap is listed at the end. Each line names its source, so the register
> can be checked. The same file is in both repositories. As of **24 Nov 2026** (after the 23 Nov review and its
> councils: paper-trader #80, #81 and the families PR; lakehouse #73, #74). Dummy money only; nothing here is investment advice.

Status words: **live** = affects the dummy-money ledgers today; **paper** = recorded and reported, never trades;
**auto** = will turn itself on when its condition is met; **parked** = decided, waiting.

## 1. Purpose and principles

| # | Decision | Status | Source |
|---|---|---|---|
| P1 | Dummy money only: no broker, demat or bank link. Real money needs a separate decision and SEBI's retail algo rules (registered app ID, static IP, daily 2FA) | live | `docs/SYSTEM_PLAN.md` Principles, Risks |
| P2 | The trader proposes; code enforces. Every limit is ordinary code in the risk gate, so Claude can't argue around it; `execute` refuses anything not approved | live | `trader/engine.py` `risk_gate`, `execute` |
| P3 | You approve every trade on Telegram, except in auto mode (on by default), where a proposal over the bar is placed without waiting | live | `config.AUTO_MODE_DEFAULT`, `engine._auto_skip_reason` |
| P4 | An independent reviewer (a second Claude with no tools, facts only) argues against every intraday proposal | live | `trader/reviewer.py`, `REVIEWER=on` in `trading-session.yml` |
| P5 | Earn the next step: backtest → paper (shadow) → review with you → live. Nothing paper goes live without a review | live | `docs/SYSTEM_PLAN.md` |
| P6 | Plain words first in every message; numbers under the details | live | `engine` message builders |
| P7 | Your 👍/👎 ratings are kept for your own review and never train the system (since 17 Nov) | live | `docs/SYSTEM_PLAN.md` phase 5 |
| P8 | Real-money go/no-go defaults to **no go**; written criteria (200+ trades since 18 Nov net positive after costs (60 until 24 Nov), profit factor ≥ 1.2, average R > 0, largest fall < 8R, latest backtest of the live rules ≥ 0R, no reconciliation mismatch in 10 days) | parked | `docs/SYSTEM_PLAN.md` Risks and decisions |

## 2. Money and pools

| # | Decision | Status | Source |
|---|---|---|---|
| M1 | Rs 2,00,000 in three pools, each with its own ledger: intraday Rs 40,000, swing Rs 60,000, long-term Rs 1,00,000. A pool never spends another's cash | live | `config.STARTING_CAPITAL`, `SWING_CAPITAL`, `LONG_TERM_CAPITAL`; `trader/pools.py` |
| M2 | Ledger reset 17 Nov: every pool and shadow pool restarted from its cash on 18 Nov; history kept | live | `config.LEDGER_START`, `jobs/reset_ledger.py` |
| M3 | Swing pool in **auto mode** from 24 Nov (owner's decision): follows the momentum + news rules (6 stocks, 2% risk, 2×ATR trailing stop, 20 days), buys paid by selling NIFTYBEES; idle cash re-parked in NIFTYBEES at 15:14; stocks bought by hand are taken over by the rules; `/swing auto off` stops it | live | `pool_auto.py`, `swing_momentum.py` (`REAL_POOL`), `pools.park` |
| M4 | Long-term pool in cash; auto mode (weekly quality picks, Saturdays) is built but off until the 18 Dec go/no-go approves it (`/longterm auto on`) | parked | `pool_auto.py`, `jobs/longterm_shadow.py` (`REAL_POOL`) |
| M5 | Portfolio circuit breaker: all pools combined 12% below their peak stops new buys until `/resume` | live | `pools.breaker_on`, 15:40 snapshot |

## 3. Intraday: how trades are found

| # | Decision | Status | Source |
|---|---|---|---|
| I1 | Setups on completed 5-minute candles: ORB long/short (first 15 minutes), VWAP reclaim/rejection, previous-day high/low break | live | `trader/scanner.py` |
| I2 | Universe: 36-stock watchlist across 11 sectors, plus news radar (any Nifty 200 stock passing guardrails: main-board EQ, Rs 50+, Rs 50 crore+ a day; up to 12 flagged names in the morning, up to 5 extra during the day on a fresh NSE filing) | live | `trader/sectors.py`, `config.NEWS_FOCUS_MAX`, `stocks.gold.intraday_universe` |
| I3 | Focus list: up to 15 stocks, at least 6 sectors; re-ranked at 09:25 by opening relative volume ("stocks in play"); scanner strict picks +1 | live | `config.FOCUS_MAX`, `IN_PLAY_AT`, `IN_PLAY_BARS` |
| I4 | Cycle: a few seconds after each 5-minute candle closes; stops checked every minute; a fresh filing starts a cycle at once | live | `config.WAKE_AFTER_CLOSE_SEC`, `MONITOR_EVERY_SEC` |
| I5 | Sessions: Claude Code (Opus) in GitHub Actions, morning (kicker 09:08, cron backup 09:16) and afternoon (12:27 / 12:36), up to 4 h each | live | `trading-session.yml`, `infra/aws/schedules.tf` |
| I6 | Pre-market plan: risk from India VIX (NSE's own index file first, Yahoo backup), bias from Nifty 50's trend; US markets as support only; a source missing its session is left out and named, never replaced by an older day | live | `trader/brief.py`, `stocks.silver.nse_index_daily` |
| I7 | Entry window 09:30–14:30 on NSE trading days (holidays from NSE's own list) | live | `config.ENTRY_START/END`, `trader/nse_calendar.py` |

## 4. Intraday: the suggestion and auto mode

| # | Decision | Status | Source |
|---|---|---|---|
| S1 | Suggestion % = average of live checks (Nifty, sector, VWAP side, strength vs sector, how far it ran, time left, plan bias, scanner), Claude's own YES %, and the reviewer; kept within 5–95%. Scores, not probabilities | live | `trader/advisor.py` `assess`, `combine` |
| S2 | Auto mode places a proposal only when the suggestion is **over** 55%, Claude's own score over 50% and the reviewer agrees; otherwise it is skipped and its would-be result recorded | live | `config.AUTO_MIN_YES_PCT`, `AUTO_MIN_AGENT_PCT`, `AUTO_NEEDS_REVIEWER` |
| S3 | Bar +5 for a setup, mood, hour, sector or direction with a losing judged record (8+ outcomes); 60% to flip a stock already proposed that day; auto mode at most 6 trades a day; switches off at the weekly brake | live | `AUTO_LOSING_GROUP_BUMP`, `AUTO_FLIP_MIN_YES_PCT`, `AUTO_MAX_TRADES_PER_DAY` |
| S4 | Track record (last 90 days, real outcomes) read every session; Claude adjusts its own score only where a row has 8+ outcomes | live | `stocks.gold.track_record`, `prompts/trading_rules.md` |
| S5 | Manual approvals expire after 3 minutes; a fill is skipped if the price moved more than 0.3% from the proposal | live | `APPROVAL_TIMEOUT_SEC`, `ENTRY_TOLERANCE_PCT` |

## 5. Intraday: risk gate and limits

| # | Decision | Status | Source |
|---|---|---|---|
| R1 | Risk per trade 1% of balance (1R ≈ Rs 400); 0.75% at VIX ≥ 20 or when VIX is unknown; 0.5% at VIX ≥ 30 | live | `config.RISK_PER_TRADE_PCT`, `brief` VIX scaling |
| R2 | Daily loss limit 4R (Rs 1,600; Rs 1,200 at VIX ≥ 20), counting **open stop-risk** too; a new trade is sized to the room left or blocked; lifts if a winner brings the day back | live | `DAILY_LOSS_LIMIT_R`, `engine.loss_room` |
| R3 | Weekly brake: week down 8R halves risk per trade until Monday | live | `WEEKLY_LOSS_LIMIT_R`, `WEEKLY_BRAKE_FACTOR` |
| R4 | Up to **5 open positions** (3 until 23 Nov); 10 trades a day | live | `MAX_OPEN_POSITIONS`, `MAX_TRADES_PER_DAY` |
| R5 | Sector caps: 1 open and 2 trades a day per sector (kept at the 23 Nov review) | live | `MAX_OPEN_PER_SECTOR`, `MAX_TRADES_PER_SECTOR` |
| R6 | No BUY on a stock weaker than its sector today, no SELL on one stronger (more than 0.3% since the open); from 24 Nov | live | `LAGGING_SECTOR_LIVE`, `advisor.RS_GAP_PCT` |
| R7 | Cost filter: round-trip costs and slippage at most 30% of the risk (a stop of about 0.9%+); with the 1.5R minimum this means a target ≥ 5× costs | live | `MAX_COST_SHARE_PCT`, `engine.cost_share_pct` |
| R8 | Reward at least 1.5× risk; one trade's margin at most 30% of the balance; 5x intraday leverage | live | `MIN_RR`, `MAX_POSITION_MARGIN_PCT`, `INTRADAY_LEVERAGE` |
| R9 | Cool-off: 30 minutes without proposals after 3 losses in a row | live | `COOLOFF_AFTER_LOSSES`, `COOLOFF_MIN` |
| R10 | No penny stocks (under Rs 50); no stock whose price hasn't moved for 10 minutes (may be at a circuit limit) | live | `MIN_STOCK_PRICE`, `engine.price_frozen` |
| R11 | Stale data pauses proposals (Nifty candles older than 15 minutes); `/pause`, `/stop` kill switch | live | `DATA_STALE_MIN`, `engine` |

## 6. Intraday: exits

| # | Decision | Status | Source |
|---|---|---|---|
| E1 | Fixed stop and 2R target for new trades, with the early exit (E3) | live | `config.EXIT_RULE_LIVE = "current"` |
| E2 | Trailing stop (no fixed target; from +1R the stop trails 1R behind the best price) is built for live use but on only if a review sets it. An automatic switch was built and withdrawn on 23 Nov: its replay left out the live early exit; `trailing_early` now replays it as it would run | parked | `EXIT_RULE_LIVE`, `engine.trailing_exit`, `exits` `trailing_early` |
| E3 | Early exit on a real break of the trade's exit line: two 5-minute closes past it, or one 0.2%+ past it | live | `exits.early_break`, `EARLY_BREAK_PCT` |
| E4 | Square-off at 15:10 (guardian backup 15:12); kept at the 23 Nov review (brokers force-close MIS ~15:15–15:20) | live | `config.SQUARE_OFF` |
| E5 | Your own trades (`/buy`, `/short`) keep the stop and target you set; they don't use the trader's allowance or sector counts but count toward the loss limit | live | `engine.execute` (`custom`), `trader/custom.py` |

## 7. Costs and fill model (assumptions)

| # | Assumption | Source |
|---|---|---|
| A1 | Intraday: Rs 20 brokerage per order, STT 0.025% on sells, exchange 0.00297%, SEBI 0.0001%, GST 18% on brokerage and fees, stamp 0.003% on buys: about 0.26% of the position per round trip with slippage | `config` fee settings, `trader/fees.py` |
| A2 | Slippage 0.05% per side (double at the 09:15 open in the paper test); paper fills checked against the real minute since 21 Nov (about 5 bps stricter so far) | `SLIPPAGE_PCT`, `engine.fill_check` |
| A3 | Delivery (pools): 0.12% a side plus Rs 16 per sell | `DELIVERY_COST_PCT`, `DP_CHARGE_RS` |
| A4 | Prices from Yahoo Finance (unofficial, may lag minutes); a stop is filled at the stop or the candle's open if it gapped through; within one candle the stop is checked before the target | `trader/market.py`, `engine.monitor` |
| A5 | Taxes are not deducted in the ledgers; reports will show after-tax results for pools (20% STCG, 12.5% LTCG above Rs 1.25 lakh; intraday as business income) | `docs/SYSTEM_PLAN.md` phase 4 |

## 8. Paper tests (intraday)

| # | Test | Since | Decided |
|---|---|---|---|
| T1 | 14 exit rules replayed on every real trade (fixed 2R, breakeven at +1R, half at +1R, trailing, trailing with the early exit, 60-min time stop, no stop, 1.5x / 2x wider stops, stop on a 5-min close, 1.5R target, yesterday's level as target, early exit on any close, strict early exit = the live rule) and a day profit lock; grouped into families (stop placement, at +1R, target, early exit, time) and compared with the live rule | 11–24 Nov | scoreboard (section 13) |
| T2 | Background rules on each proposal: Nifty against by 0.3%, against the mood, after 14:00, below VWAP, stretched breakout, SELL in an uptrend (the sector rule went live, R6) | 16–21 Nov | 1 Dec review |
| T3 | Opening-range test on the top 10 stocks in play: 5- vs 15-min range × 2R / 1.5R / level / held to 15:10, and "wait for the retest" | 16–19 Nov | 1 Dec review |
| T4 | 09:15 opening buy of the plan's picks (double slippage, 1.2% stop); gap fade (short a 3%+ gap-up breaking its first 15 minutes' low before 11:00) | 21 Nov | 1 Dec review |
| T5 | Auto-bar ladder (50–70%) and sizing by the suggestion (0.5x / 1x / 1.5x) on every proposal | 21 Nov | 1 Dec review |
| T6 | Shadow models, walk-forward on ~2,000 setups: volatility stop, trade filter, early cut, let winners run, models combined; every rule set loses so far (current −445R, best −98R) | 17 Nov | before any real money |
| T7 | Selector: picks tomorrow's exit rule, auto bar and entry from the last 20 days (30+ trades, shrunk, switches for a Rs 40-a-trade edge); never trades | 23 Nov | weeks of results |
| T8 | News vs watchlist and the scanner, judged separately in the track record | 16–17 Nov | auto (8+ outcomes) |

## 9. Swing and long-term

| # | Decision | Status | Source |
|---|---|---|---|
| W1 | Real swing pool in auto mode (M3), measured against NIFTYBEES; your own swing trades allowed, and taken over by the auto rules | live | `pools`, `swing_momentum`, guardian 15:50 |
| W2 | Swing shadow 1, news breakouts: filing within 2 days + 20-day high on 2x volume; 5-day-low stop, 2R target, 10-day exit; 1% risk, 5 holdings | paper | `trader/swing_shadow.py`, `gold.swing_signals` |
| W3 | Swing shadow 2, technical scanner: six filters (trend, RSI 50–65, MACD, ADX, volume, R:R); next-day entry at its level; stop, T2 or 15 days | paper | `trader/swing_tech.py`, `gold.technical_screen`; review ~8 Dec |
| W4 | Swing shadow 3, momentum + news: top of `gold.momentum_ranks`, 6 holdings, 2% risk, 2×ATR trailing, 20 days; concentrated copy 3 holdings, 3% risk, no sector cap | paper | `trader/swing_momentum.py` |
| W5 | Long-term shadow 1, weekly quality picks: Nifty 200, quality tests (ROCE/ROA, debt, cash flow, growth, P/E), top 10, one or two buys a Saturday, sell what stops qualifying, trim above 18% | paper | `jobs/longterm_shadow.py`, `gold.longterm_candidates` |
| W6 | Long-term shadows 2 and 3, momentum top 15 and top 5 (monthly, volatility-adjusted 6/12-month momentum, no loss-makers or high debt) | paper | `jobs/longterm_shadow.py`, `gold.momentum_ranks` |
| W7 | Reviews: scanner ~8 Dec; swing shadows vs NIFTYBEES mid-Dec; long-term go/no-go 18 Dec | parked | `docs/SYSTEM_PLAN.md` roadmap |

## 10. Data and lakehouse

| # | Decision | Source |
|---|---|---|
| L1 | Databricks Free Edition; three pipelines (market 37 tables, research 5, trading 25), one at a time; bronze Auto Loader → silver/gold materialized views | `pipeline/*`, `infra/databricks/pipeline.tf` |
| L2 | Sources: NSE bhavcopy, delivery and indices (19:30), NSE F&O list, Massive (US, 11:00), FRED (off until a key is set), Yahoo pre-open snapshot (08:45), NSE / Business Standard / RBI / SEBI news (every minute in market hours), Yahoo fundamentals (Saturday), the trader's journal, portfolio and pool trades (15:45, 23:30) | lakehouse `fetchers/`, paper-trader `trader/export.py` |
| L3 | Raw files kept untouched in S3 and pushed to a Databricks volume; a sync Lambda repairs gaps | lakehouse `infra/aws` |
| L4 | Jobs run on file arrival (NSE, pre-open, trader export) with scheduled fallbacks; plain-words page summaries by `ai_summarize` | `infra/databricks/pipeline.tf`, `sql/dashboard_summary.sql` |
| L5 | Dashboard pages: Desk, Intraday, Swing, Long term, Your calls, Global markets; every query checked with EXPLAIN in CI | `infra/databricks/dashboard.tf`, `scripts/check_sql.py` |
| L6 | Mobile app: the dashboard's own SQL exported to one JSON six times a day; served from a private bucket by a Lambda function URL behind a secret path (CloudFront blocked until AWS verifies account 538498973473; one-line switch back) | lakehouse `infra/aws/app.tf`, `fetchers/app_web.py`, `app_export.py` |

## 11. Infrastructure, security and operations

| # | Decision | Source |
|---|---|---|
| X1 | No servers: AWS free plan in Sydney (Lambda, S3, EventBridge Scheduler, SSM, CloudWatch), public GitHub Actions, Databricks Free Edition. Free plan ends after 6 months or $200 of credits: upgrade before then | `docs/PLATFORM.md` |
| X2 | Lambdas: kicker (starts sessions), guardian (09:45 scanner check, 15:12 square-off, 15:14 park, 15:40 report, 15:45 export, 15:50 swing shadows, 23:30 late export, Fri 17:55 weekly), responder (Telegram every 2 min outside sessions), lakehouse fetchers, news, sync, pre-market, app export and app web | `infra/aws/*.tf` both repos |
| X3 | GitHub Actions: trading sessions, model shadow (16:10), fundamentals and long-term shadow (Saturdays), backtests and ledger reset (manual), Databricks run-job (manual), security scans (weekly + PRs) | `.github/workflows/` |
| X4 | Secrets only in SSM SecureString and GitHub environments; least-privilege identities (plan read-only, apply limited to `stocks-*`, trader can't delete state); CI uses IAM user keys (no OIDC on the free plan) | `docs/PLATFORM.md` Security |
| X5 | Ship via PR to `main`: tests, fmt, validate, Terraform plan as a comment, Checkov/CodeQL/zizmor; merge applies. Reviewed Checkov skips in `.checkov.yaml` (mobile app CloudFront and Lambda URL skipped at your request) | `.github/workflows/terraform.yml`, `security.yml`, `.checkov.yaml` |
| X6 | Claude usage: Opus for the trader, Sonnet for the reviewer; swing, long-term, models and backtests use no Claude | `trading-session.yml` |
| X7 | Docs kept in step by a scheduled task every 2 days (skips while PRs are open); `SYSTEM_PLAN.md` identical in both repos | scheduled task "Docs update for both repos" |
| X8 | Renewal reminders on Telegram 30, 7, 3 and 1 days before the Databricks token (~6 May 2027), the AWS free plan (~6 May 2027 or sooner), the Claude token and the GitHub dispatch token (~7–8 Nov 2027); dates are estimates, corrected in S3 `config/expiries.json` | `trader/expiry.py`, guardian 15:45 |

## 12. Governance and reviews

| # | Decision |
|---|---|
| G1 | You merge PRs unless you say "merge", "auto merge" or "push this change"; security-scan suppressions are your decision |
| G2 | Plan first, then ask before changing anything; compact answers |
| G3 | Reviews: 1 Dec (repeat of 23 Nov plus the new rules), ~8 Dec scanner, mid-Dec swing shadows, 18 Dec long-term and real-money go/no-go |
| G4 | 23 Nov council decisions: trailing auto-switch, sector filter live, 5 open, sector caps and 15:10 kept; dynamic sector scoring, shorting weak sectors, no position cap and a later square-off rejected |

## 13. Exploration: families, lanes and the pass bar (from 24 Nov)

Every paper variant keeps running; the owner wants broad exploration. To keep chance from picking winners:

| # | Decision | Source |
|---|---|---|
| F1 | Variants are grouped into families (exits ×5, entries ×2, filters ×3, score, selector, models, swing ×3, long-term ×2); stored names never change, reports group them | `trader/families.py` |
| F2 | Variant ledger: every variant ever tried, with family, lane (exploring / candidate / dead / baseline / live), start and end, and its kill rule; a test keeps it in step with the code | paper-trader `docs/VARIANTS.csv`, `tests/test_families.py` |
| F3 | Baselines: exits against what live trades do (fixed 2R + early exit); paper entries and filters against no trade; the auto bar against 55%; swing and long-term against NIFTYBEES (and the matching NSE factor index where there is one) | `families.py`, `VARIANTS.csv` |
| F4 | Luck line: the best per-trade result a family would show by chance if no variant beat its baseline (2,000 resamples of whole trading days, so variants replaying the same trades stay correlated) | `families.luck_line` |
| F5 | Plain verdicts: "too early" under 100 trades; then "could be luck" or "beating its baseline". **Pass bar** for a review to use a variant live: 200+ trades, above the luck line, and still above it on the weeks after it was picked. **Kill rule**: after 200 trades, the upper 95% bound of its Rs/trade against the baseline below 0 | `families.MIN_TRADES`, `PROMOTE_TRADES` |
| F6 | Each report also shows real trades' R before and after costs, "Is Claude adding value?" (Claude's proposals vs the plain 15-min breakout on the same days) and when the real-money trade count arrives at the current pace | `families.lines` (weekly report, `/shadow`) |
| F7 | No new exit or filter variants on the same intraday setups; new ideas must be different entries. Live rules change only at reviews (1 Dec, then monthly) | review of 23 Nov |

## 14. Suggestions from a friend's review (24 Nov; council verdict, owner approved)

A friend reviewed a skipped proposal ("UNSURE 52%; auto skipped: needs over 60%") and suggested eight ideas. The skip
came mostly from the bar being raised to 60% by +5 penalties (09:00 hour, IT sector, losing buys); the "already
rallied" check is protective. No idea below fixes the main issue (the intraday setups are about break-even before
costs); only tested new entries can.

| # | Idea | Decision | Where |
|---|---|---|---|
| B1 | Daily, weekly, monthly levels | **Shown, not a filter.** Each proposal shows the nearest level in its direction (previous day / week / month high or low, 52-week high or low; completed bars only) and the room to it in R; the journal note keeps `room=..R@level`. Not given to the agent or the reviewer. A "skip under 1R of room" filter is backtested on the 2,093 setups and replaces "stretched" only if it beats it | `trader/levels.py`, VARIANTS `room_1r` |
| B2 | Chart patterns | **One objective rule first:** 52-week-high breakout on daily bars, a swing entry family against NIFTYBEES after costs, 2025-26 held back. More patterns only if it passes | VARIANTS `breakout_52w` |
| B3 | RSI / MACD | Already used (daily scanner). Weekly RSI shown on proposals, information only | `levels.weekly_rsi` |
| B4 | No clear goal | **Goal page** and a short goal section in the trader's instructions; untested outside ideas don't go in. The agent's YES rate is compared two weeks before and after 25 Nov | `docs/GOAL.md`, `prompts/trading_rules.md` |
| B5 | 10-20 minute options trades | **No options** (thin stock options, large lots, 1-3% spreads, option STT, decay, no data feed). The trigger "rallied ~5%, ranges 1-2%, then breaks out" is tested on the share itself as a new paper entry family | VARIANTS `rally_range` |
| B6 | Morning brief | Already sent ~09:00; can move to 08:30 on request | brief |
| B7 | Ranked list | The 09:25 in-play ranking's top 5 with scores goes to Telegram, labelled a ranking, not a chance of profit | `engine.in_play_message` |
| B8 | Charting website | TradingView chart button on every proposal; chart links on the app's and dashboard's suggested lists | `links.tradingview`, lakehouse `swing_suggest` |
| B9 | NSE pre-open auction (owner, 24 Nov) | **Information only:** at ~09:09 the session reads NSE's pre-open data (IEP, gap vs yesterday, buy/sell quantity ratio) for tradable F&O stocks, saves it to S3 `preopen/` and shows the biggest gaps in the plan message and the ratio in the 09:25 ranking. Not given to the agent or the reviewer; tested before any use. NSE may block cloud servers: then that day has none | `trader/preopen.py`, VARIANTS `preopen_imbalance` |

## Gaps found while building this register (for phase 3)

| Where | Says | Code / reality |
|---|---|---|
| `SYSTEM_PLAN.md` header | "As of 19 Nov 2026" | 23 Nov |
| `SYSTEM_PLAN.md` Risk framework | 3 open; no sector filter; exits not mentioned | 5 open; sector filter live; trailing auto-switch |
| `SYSTEM_PLAN.md` Phase 1 table | Exit rules "fixed stop and 2R target … decided at a review" | auto-switch to trailing at 15 trades |
| `SYSTEM_PLAN.md` Principle 2 | automatic exits: stop, target, 15:10, early exit | plus the trailing stop |
| `SYSTEM_PLAN.md` Decisions table | Intraday loss limit "Rs 4,000 (9 Nov)" first | 4R = Rs 1,600 today (history row; reword) |
| `PLATFORM.md` Components | guardian 15:45 exports journal + portfolio | also pool trades |
| `PLATFORM.md` constraints | AWS services list | Lambda function URL now used; CloudFront blocked pending verification |
| Swing pool | "rules on hold" | your own swing buys are allowed (one placed 23 Nov) |
