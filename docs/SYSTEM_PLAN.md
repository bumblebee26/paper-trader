# Paper Trader: complete system plan

> The full plan for the dummy-money trading system across both repositories, kept in git. The same file
> is in [paper-trader](https://github.com/bumblebee26/paper-trader/blob/main/docs/SYSTEM_PLAN.md) and
> [stocks_lakehouse](https://github.com/bumblebee26/stocks_lakehouse/blob/main/docs/SYSTEM_PLAN.md); change
> both together. For how the running system fits together, see [PLATFORM.md](PLATFORM.md).
> As of 19 Nov 2026. Each section says what is **built** and what is still **planned**.

Everything is dummy money. Nothing here is investment advice; it is a system for learning how these
strategies behave.

## Summary

The paper trader is growing from one intraday strategy into a three-pool dummy portfolio of Rs 2,00,000,
driven by live market data, company news and company financials, with you approving trades on Telegram.

| Phase | Status (21 Nov 2026) | Next gate |
|---|---|---|
| 1 Intraday upgrades | Built, live from 10 Nov | Track-record review, 23 Nov |
| 2 Foundations (news, pools, alerts) | Built | Cost review, 15 Nov |
| 3 Swing pool | Rs 60,000 held in NIFTYBEES; three rule sets in shadow mode: news breakouts (from 16 Nov), the technical scanner (from 17 Nov) and momentum + news (from 18 Nov, with a concentrated top-3 copy) | Scanner signal review ~8 Dec; shadow review vs NIFTYBEES, mid-Dec |
| 4 Long-term pool | Selection, backtest and shadow mode built; a second shadow, momentum (top 15, monthly, with a concentrated top-5 copy), from 21 Nov | Go/no-go 18 Dec, buying from 21 Dec |
| 5 Track record and auto mode | Live from 12 Nov: auto mode, track record (real outcomes only from 17 Nov) fed back every session | Review 23 Nov |
| 6 Shadow models (intraday) | From 17 Nov, paper only: volatility stop, trade filter, early cut, let winners run, plus stop variants, judged walk-forward every day; from 21 Nov more paper tests (09:15 opening buy, gap fade, auto-bar ladder, score sizing) and from 23 Nov a selector that picks tomorrow's version from the last 20 days | Review with you before December go-live |

Every pool and shadow pool restarted from its starting cash on Wed 18 Nov (ledger reset of 17 Nov), so all
strategies are compared from the same day; the trade history, track record and ratings were kept.

## Principles

1. **Dummy money only.** No broker, demat or bank link. Real money would need a separate decision and SEBI's
   retail algo rules (see Risks).
2. **You approve every buy and sell** on Telegram. Automatic exits are limited to rules set in advance
   (stop-loss, target, the 15:10 intraday square-off, from 20 Nov the early exit when a trade's idea really
   breaks, and for pools, rules you agreed to).
3. **Plain words first.** Every proposal opens with a suggestion and a plain-English summary; numbers sit
   under Details.
4. **Independent review.** A second agent, which never sees the proposer's pitch, argues against every trade.
5. **Earn the next step.** A new pool is backtested, then runs in shadow mode, and goes live only after a
   review of its results.

## Three investment pools

Decided 9 Nov: Rs 2,00,000 in total, split 20 / 30 / 50, each pool with its own ledger and limits, so one
strategy's losses never hide inside another's gains. A pool never spends another pool's cash.

| Pool | Holds for | Dummy capital | Status | Ledger |
|---|---|---|---|---|
| Intraday | Minutes to hours, closed by 15:10 | Rs 40,000 | Trading (P&L since 9 Nov kept when resized) | `state/ledger.json` |
| Swing | 3 to 20 trading days | Rs 60,000 | Rules on hold; held in NIFTYBEES (bought 10 Nov 15:14) | `state/pools/swing.json` |
| Long-term | Months to years | Rs 1,00,000 | Shadow mode; real buying from 21 Dec if approved | `state/pools/long_term.json` (shadow: `long_term_shadow.json`) |

Side effects of the smaller intraday pool: a trade risks about Rs 400, so fees of Rs 40 to 60 a trade weigh
more. From 11 Nov the loss limits are set in "R" (one trade's risk, which follows VIX), see Phase 1.

## Phase 1: intraday upgrades (built, live 10 Nov)

| Setting | Before | Now |
|---|---|---|
| Trades per day | 5 | 10 |
| Daily loss limit | 2% | 4R (Rs 1,200–1,600 with VIX) including open positions' risk; lifts again if a winning exit brings the day back under it (Rs 4,000 from 9 to 11 Nov) |
| Weekly brake | none | Week down 8R: half-size trades until Monday |
| Costs | Rs 20 per order + 0.05% slippage | From 16 Nov, every order also pays STT (0.025% on sells), exchange and SEBI fees, GST and stamp duty (0.003% on buys), as on a contract note: about 0.26% of the position per round trip with slippage |
| Cost filter | none | No trade whose costs exceed 30% of its risk (in practice a stop of about 0.9% or more) |
| Exit rules | fixed stop and 2R target | Same, with alternatives and a day profit lock on trial in the background (reports; decided at a review); from 19 Nov also a nearer 1.5R target, and yesterday's high (low for a short) as the target when it comes before 2R. From 20 Nov, live: an early exit when the trade's exit line (the price that breaks its idea) breaks for real, two 5-minute closes past it or one 0.2%+ past it, enforced by code (one close just past the line was usually noise) |
| Cool-off | none | 30 minutes without new proposals after 3 losses in a row |
| Watchlist | 16 stocks, mostly banks and IT | 36 stocks across 11 sectors (`trader/sectors.py`) |
| Focus list each morning | up to 10 | up to 15, at least 6 sectors; from 16 Nov re-ranked at 09:25 by opening relative volume (the first 10 minutes vs the usual), the "stocks in play" |
| Sector caps | none | 1 open position per sector, 2 trades per sector a day |
| Position cap | margin limit only | One trade uses at most 30% of the balance as margin |
| Proposal | numbers | Suggestion % (live checks + trading agent + independent reviewer), plain words, market check, company news; from 16 Nov the live checks also score the VWAP side and strength against the stock's sector |
| Research tests (paper, from 16 Nov) | none | Background rules "no BUY below VWAP" and "no BUY on a stock weaker than its sector" (mirrored for SELL); opening-range breakout on the top 10 stocks in play with 5- vs 15-minute ranges, 2R target vs held to 15:10. From 19 Nov: a "wait for the retest" version of that test (buy only if the price comes back to the range edge within 60 minutes), and a "stretched" flag on breakouts that start already far from the day's low (more than 70% of a usual day's range) or more than 1% past VWAP, recorded on each proposal without changing its score. From 21 Nov: buying the plan's news and scanner picks at the 09:15 open (double slippage, stop 1.2% below); shorting a 3%+ gap-up once it breaks its first 15 minutes' low before 11:00 (the gap fade); every auto bar from 50% to 70% and trades sized by their suggestion (0.5x / 1x / 1.5x); and each paper fill checked against the real minute. Decided at a review |

## Phase 2: foundations (built)

| Part | What it does |
|---|---|
| Company news | NSE announcements, results, board meetings and corporate actions into the lakehouse every minute in market hours (every 30 min otherwise); from 17 Nov also Business Standard markets headlines (every 4 min; only headlines naming a tradable company reach the trader) and RBI and SEBI press releases (every 5 min; market-wide context, never a stock signal). BSE announcements are built but off (no verified public feed). Every proposal lists the company's filings with links and flags regulatory, rating, pledge filings and board meetings within 2 days |
| Morning plan, India first (from 17 Nov) | Risk per trade from India VIX (1%; 0.75% at 20+; 0.5% at 30+; from 20 Nov read from NSE's own index file first, Yahoo only as the backup, and 0.75% when neither has the last session); bias from the Nifty 50's trend; the US (S&P 500, Nasdaq, Brent, USD/INR, live from Yahoo) only as support, plus a "global shock" line at a 2%+ S&P move; the five ADRs as evidence for those stocks only. **Freshness rule:** each source must have the session it should (from the market's own calendar); one that doesn't is left out and named, never replaced by an older day; holidays are stated. Massive's US history now lands at 11:00 IST, after its free plan releases the day |
| Market mood | Each proposal says trending up, trending down, jumpy or choppy (Nifty since the open and India VIX) |
| Shadow rule variants | Each proposal records which alternative rules would have blocked it (Nifty against the trade by 0.3%, against the mood, after 14:00; from 21 Nov also no SELL while the Nifty 50 is in an uptrend), for the 23 Nov review |
| Commands | `/news SYMBOL`, `/portfolio`, `/screen` (`/screen SYMBOL`), `/stop` (kill switch) plus `/ledger`, `/plan`, `/pause`, `/resume`, `/exit`, `/help`, answered any time |
| Data alerts | Stale prices pause proposals with one alert and an all-clear; a news feed more than 90 minutes behind alerts once a day |
| Pool ledgers and circuit breaker | Separate ledgers for swing and long-term with delivery costs; all pools 12% below their peak stops new buys until `/resume` |
| Weekly report | Fridays 17:55: results, setups, your calls vs the suggestion and reviewer, background rules, all pools |
| Dashboard | Databricks pages Desk, Intraday, Swing, Long term, Your calls and Global markets (redesigned 18 Nov): headline tiles, one main chart, breakdowns, then tables; a Show filter (real / shadow / benchmark) on Intraday, Swing and Long term |

Planned later: one-tap reason buttons on YES/NO, modelled interest on idle cash, business headlines from
publishers, a macro event calendar (RBI, Budget, Fed).

## Phase 3: swing pool (on hold)

**Result:** the rules failed. On 15 May 2025 to 6 Nov 2026 every version lost money while NIFTYBEES made 3%
(pullbacks -4%, breakouts -18%). A second try tuned 16 versions on May 2025 to April 2026; the best made
+8.3% there but lost 3.8% on the unseen months May to November 2026 (5 trades) while NIFTYBEES made 4.1%.
As agreed, swing is dropped for now and its Rs 60,000 is held in NIFTYBEES.

The rules, kept for a retry (`backtest/swing.py`):

- **Setups:** pullback to the 20-day average in an uptrend; close above the 55-day high on 1.5x volume.
- **Entry:** next open, skipped if it gaps more than 2% above the signal close.
- **Exits (automatic, decided 9 Nov):** close below a 2x-ATR stop, hard intraday stop at twice that, target
  at 2.5x risk, trailing exit under the 20-day average once 1x risk in profit, 20-day time stop.
- **Limits:** 1% risk per trade, at most 5 holdings, 25% each, 2 per sector.

**Retry in shadow mode (from 16 Nov):** results- and news-driven breakouts, traded on paper in a separate
`swing_shadow` pool (never in the portfolio totals) while the real Rs 60,000 stays in NIFTYBEES:

- **Signal** (`stocks.gold.swing_signals`): an eligible Nifty 200 stock (main-board EQ series, Rs 50+, Rs 50
  crore+ traded a day) that, within 2 days of an NSE filing (results, an order, a merger...), closes above its
  20-day high on 2x its usual volume, up 2%+ and above its 50-day average.
- **Trade:** buy at the next open; stop at the 5-day low (at most 8% below); target 2x the risk; otherwise sell
  after 10 trading days. 1% risk per trade, at most 20% of the pool in one stock, 5 holdings.
- **Runs** in the guardian Lambda at 15:50 on trading days (no Claude). Results: `/shadow`, the weekly report and
  `stocks.gold.shadow_pools_daily`. It moves the real Rs 60,000 only if it beats NIFTYBEES at a review after
  enough trades (about mid-December), with your OK.

**Third shadow test (from 18 Nov): momentum + news, more risk on purpose.** From `stocks.gold.momentum_ranks`
(Nifty 200): each day the strongest stocks by 1- and 3-month return and closeness to the 52-week high, ranked up
for a fresh positive filing (results, order, merger, dividend/bonus/buyback) or a strict scanner pick and down for
a regulatory or pledge filing; only uptrends (close above the 50- and 200-day averages) with RSI under 80. Bought
at the next open, best-ranked first, up to 6 holdings; stop 2 x ATR under the entry, then trailing 2 x ATR under
the highest close; out after 20 trading days. **2% of the pool at risk per trade** (double the other shadows),
at most 1/6 of the pool in one stock. Pool `swing_mom_shadow`. A **concentrated copy** (`swing_mom_conc_shadow`,
from 18 Nov) trades the same signals with 3 holdings, up to a third of the pool each, 3% at risk per trade and no
sector cap, to see whether concentrating on the top ranks pays more than spreading (compared at the December review).

**Long-term, second shadow (from 21 Nov): momentum.** Every first Saturday of the month, the Nifty 200's top 15 by
volatility-adjusted 6- and 12-month momentum (the method behind NSE's Momentum 30 index), equal weights, leaving
out companies with a loss or debt above 1.5x equity (outside banks); a holding is kept while it stays in the top
30. Pool `long_term_mom_shadow`, next to the weekly quality picks; both compared with NIFTYBEES. A **concentrated
copy** (`long_term_mom_conc_shadow`) holds the top 5 (about a fifth of the pool each, no sector cap), kept while in
the top 10.

**Second shadow test (from 17 Nov): the technical scanner.** A daily Nifty 200 scan in the lakehouse
(`stocks.gold.technical_screen`), traded on paper in its own `swing_tech_shadow` pool, so the two ideas are
compared on the same days with the same costs:

- **Filters (all six for a strict pick):** close above its 20, 50 and 200-day averages; RSI(14) 50–65; MACD
  above its signal line (or just crossed); ADX(14) above 20 and rising; volume at least 1.5x the 20-day
  average; reward at least 2x the risk. Near-misses (4–5 filters, above all three averages) are listed with the
  filter each missed. Up to 10 strict picks a day, never forced. No chasing: RSI above 70, MFI above 80 or more
  than 8% above the 20-day average is "wait/avoid".
- **Levels (indicative, never a guaranteed return):** from swing points and ATR: entry zone, trigger, stop, T1,
  T2, R:R; a level that can't be grounded is left out. Stance: buy-on-dip, breakout-only or wait/avoid.
  Prices are adjusted for splits and bonuses from NSE's own previous close.
- **Trade:** the next trading day only, at the scanner's level (a breakout-only pick when the day trades through
  its trigger; a buy-on-dip pick when it dips into its entry zone); nothing is chased. Exit at the stop, T2 or
  after 15 trading days. 1% risk per trade, at most 20% of the pool in one stock, 5 holdings.
- **Reports:** 08:45 Telegram (with delivery %, F&O status, NSE/Groww links and a Swing buy button that asks you
  to Confirm), 09:45 check against the first half hour, `/screen`, `/screen SYMBOL`, the scanner line in
  `/analyze`. `stocks.gold.signal_performance` shows how each signal did 5, 10 and 20 days later against
  NIFTYBEES. **Review after about 15 trading days (~8 Dec):** keep, tighten or drop each filter.
- **Intraday use:** strict picks are a daily uptrend, so they get +1 in the 09:25 stocks-in-play ranking, a BUY
  on one gets +5 in the live checks and a short against one −10; the track record judges them separately
  (dimension `scanner`).

Why not single favourites or penny stocks: a stock like MRF costs over Rs 1 lakh a share (more than the whole
pool), a hunch can't be tested, and penny stocks can't be simulated honestly (trade-to-trade segment, price
limits that lock you in, wide spreads), so dummy profits there would be fiction.

## Phase 4: long-term pool (shadow mode)

**Universe (decided):** NSE's Nifty 200, read weekly from NSE's official list with each company's industry.

**Tests** (`stocks.gold.company_fundamentals`, weekly company figures from Yahoo Finance):

| Test | Most companies | Banks and lenders (no EBIT reported) |
|---|---|---|
| Capital | Return on capital >= 15% (latest year) | Return on assets >= 1.2% |
| Balance sheet | Debt < 0.5x equity | not applied |
| Cash | Operating cash flow positive in all but at most one of the last 4 years | not applied |
| Growth | Revenue and profit growing > 10% a year over 3 years | same |
| Price | P/E in the cheaper half of the companies checked | same |

Not available from this source, so left out: promoter pledges and holdings, banks' bad-loan ratios, P/E
history (P/E is ranked against the other companies instead).

**Selection** (`stocks.gold.longterm_candidates`): eligible with 3+ tests available, quality score
(passed / available) >= 0.6, positive P/E and no profit fall two quarters in a row. Score = 100 x quality +
3-year profit growth (capped at 30%) / 3 - P/E percentile / 5. At most 2 picks per industry; the top 10 are
the week's picks, 90% of the pool in equal parts (about Rs 9,000 each, max 15%), 10% cash. Each pick carries
its reasons in plain words.

**Buying and selling** (shadow from Sat 14 Nov, `jobs/longterm_shadow.py`): each Saturday buy the next pick
not yet held, two when NIFTYBEES is 5%+ below its 52-week high (so the pool fills over about 6 to 10 weeks);
sell a holding that stops qualifying; trim a holding above 18% back to 15%. Fills at Friday's close plus costs.
Planned for live: Telegram YES/NO per company, buys at the next open, weekly sell checks; "too expensive"
(P/E above twice its own history) and "better use of the money" swaps once P/E history exists.

**Backtest** (`backtest/run_longterm.py`), Rs 1,00,000 from 1 Jul 2025 to 9 Nov 2026:

| Universe | Hold the picks | Re-pick a year later | Deepest fall | NIFTYBEES |
|---|---|---|---|---|
| Most traded stocks of Apr–Jun 2025 (no hindsight) | +1.9% | +8.4% | about 9% | +0.3% (fall 13.8%) |
| Today's Nifty 200 (hindsight) | +19.7% | +25.5% | about 10–12% | +0.3% |

The honest reading is the first row: a modest edge with smaller falls. The shadow weeks decide at the 18 Dec
go/no-go.

**Taxes are part of selling decisions:** listed shares sold within 12 months are taxed at 20%; after 12 months
at 12.5% above Rs 1.25 lakh of gains a year ([ClearTax](https://cleartax.in/capital-gains/Index); unchanged in
Budget 2026 per [Precize](https://www.precize.in/blogs/capital-gains-tax-on-shares-in-india-ltcg-stcg-rates-holding-periods-and-budget-2026)).
Reports will show after-tax results. Intraday profits count as speculative business income at your slab rate.
Approximations; check with a chartered accountant before real money.

## Phase 5: track record and auto mode (live from 12 Nov)

- **Auto mode** (`/auto on|off`, on by default): a proposal with a combined suggestion over 55%, the trading
  agent itself for it (over 50%) and the reviewer's check is placed without waiting for you; otherwise it's skipped
  and what it would have made is recorded and listed in the day report. The bar rises by 5 for a setup, mood,
  hour, sector or direction (BUY or SHORT) with a losing judged record, and to 60% for flipping a stock already proposed that day. 6 trades a day;
  switches itself off at the weekly brake. Every code limit still applies.
- **Compact messages (from 16 Nov):** BUY / SHORT headline, the suggestion and its three scores, every number,
  and one line each for: for, against, agent, reviewer, market, track record, news. Buttons: 👍/👎 (the chosen
  one gets a ✅, also when you rate with a reaction), EXIT while the trade is open, Groww (any tradable stock: the
  address is made from the company name) and NSE quote links, and ℹ️ Full reasons, which replies with the long version (kept
  7 days). Reasons must agree with the live Nifty and sector moves.
- **Your 👍/👎** is recorded (`user_feedback`) for your own review: the dashboard's "Your calls" page shows how
  liked and disliked trades really did. From 17 Nov ratings never train the system. EXIT closes a position.
- **The feedback loop:** the lakehouse builds `gold.proposal_outcomes` (every proposal: scores, decision,
  real outcome) and `gold.track_record` (by setup, hour, mood, sector, direction, suggestion band,
  reviewer, mode, decision, news or watchlist and scanner status, last 90 days). Each session reads it, plus
  the last days' final results (`recent_days`, the daily feedback). Claude adjusts its own score only where a
  row has 8+ outcomes, and every proposal shows the record with its counts, including the stock's sector and
  the direction (e.g. "Banks 3/10 · shorts 2/9").
- At the 23 Nov review: calibration (do 60-69% suggestions win 60-69% of the time?), auto vs human,
  and the exit rules on trial.

**Shadow models (from 17 Nov, paper only):** after each close a GitHub job replays every scanner setup on
every tradable stock's 5-minute candles (60+ days, growing) and trains four models walk-forward, so each day is
judged only by models trained on earlier days:

| Model | What it decides | How it's used if it passes |
|---|---|---|
| Volatility stop | The next hour's likely range; the stop goes just beyond normal noise, with fewer shares so the rupee risk is unchanged | Replaces the fixed stop distance |
| Trade filter | The chance a setup ends in profit after costs | Skips setups under its threshold |
| Early cut | At each 5-minute close while a trade is losing: will holding end worse than cutting now? | Closes the trade before its stop |
| Let winners run | At the 2R target: will running on (stop to +1R, trailing 1R) beat booking it? | Lets that trade run |

They are compared with the current rules and the stop variants (no stop; stops 1.5x and 2x wider with the same
rupee risk; stop on a 5-minute close), from 19 Nov also the 1.5R target, yesterday's high/low as the target and
skipping stretched breakouts, from 20 Nov the early exits (on any close past the exit line, the old way, and only
on a real break, the live rule), and today's real trades are replayed under each. Results: a Telegram note
after the close, `/shadow`, the weekly report, and `models/results/` in S3. **Nothing goes live before a review
with you, before December**; a model is adopted only if it beats the current rules out of sample, net of costs.

**Selector (from 23 Nov, paper only):** after each day report it picks tomorrow's version of three things from the
last 20 trading days of shadow results: the exit rule, the auto bar (50 to 70%) and the entry (5- or 15-minute
breakout, the retest, the 09:15 opening buy, the gap fade). Guardrails against picking luck: a version needs 30+
trades, small samples are shrunk towards zero, and it switches only for a Rs 40-a-trade edge. Each day it is scored
on results it couldn't see when it picked, against the fixed rules (the live result, the 55% bar, the 5-minute
breakout). It never touches a real trade; beating the fixed rules for weeks would make it worth a review.
The weekly report also shows the swing shadows' trades sold after at most 5 or 10 days (holding-period study).

**News radar (from 16 Nov):** the intraday trader may go beyond its 36-stock watchlist to any Nifty 200 stock
that passes the guardrails (`stocks.gold.intraday_universe`: main-board EQ series, Rs 50+, Rs 50 crore+ traded a
day). Each morning up to 12 flagged names (fresh NSE filing, 3%+ move or 2x volume) are offered for the focus
list; during the session NSE filings arrive within about a minute (fetched every minute 09:00-15:59) and a
filing on a tradable stock adds it to the scan (up to 5 extra). Penny stocks (under Rs 50) and frozen prices
(stuck at a price limit) are blocked. Every proposal is labelled news or watchlist (`source` in the track
record), so news picks are judged on their own results. News says which stock may move, not which way: a
setup must still fire after the filing.

## Your own trades from Telegram (from 16 Nov)

Any NSE stock, any pool, in the same dummy ledgers, marked **custom** for analysis:

| Command | What it does |
|---|---|
| `/analyze TCS [intraday\|swing\|longterm]` | The trader's view: intraday levels, setup and live checks (BUY vs SHORT); swing trend, 20-day range, 1- and 3-month return vs NIFTYBEES; long-term quality tests, P/E and this week's rank from the lakehouse. Code only, no Claude |
| `/buy TCS [qty] [sl X] [tgt Y]`, `/short ...` | Intraday on the intraday ledger. Defaults: stop 1% away, target 2x the risk, size = one normal trade's risk (1R) within the 30% margin cap |
| `/swing buy\|sell TCS [qty] [sl X] [tgt Y]` | Swing pool (delivery). A buy is paid for by selling NIFTYBEES if the pool's cash is short; optional stop/target checked daily at 15:50 |
| `/longterm buy\|sell TCS [qty]` (also `/lt`) | Long-term pool (delivery). Default size 10% of the pool |

Every order first shows its numbers, costs and warnings (off the trader's list, penny stock, costs over 30% of the
risk, beyond today's loss room, bigger than a normal trade) and runs only when you tap **Confirm** within 3 minutes,
at the live price (not if it moved more than 1%). Orders run 09:15–15:00 (intraday) or 15:30 (pools) on trading days.
Your trades pay the same costs, don't use the trader's daily allowance, and count toward the daily loss limit.
In Databricks: `gold.custom_trades`; intraday ones also in `gold.trades` (setup "Custom") and `gold.track_record`
under dimension `source` = custom only, so they never change how the trader judges its own ideas.

## Agents and the decision flow

```
Market data + lakehouse (news, figures, picks)
  -> strategy agent (intraday trader in a Claude session; swing and long-term are code, no Claude)
  -> portfolio guard (code: pool cash, position and sector caps, loss brakes, circuit breaker)
  -> independent reviewer (second Claude, no tools, facts only, argues against)
  -> you on Telegram: YES or NO
  -> pool ledger and journal -> lakehouse analytics -> track record (phase 5)
```

The guard is ordinary code, so a limit can never be talked around. Swing and long-term use no Claude at all:
their rules run as plain code in GitHub Actions, which keeps Claude usage to the two intraday sessions.

## Risk framework

| Limit | Intraday | Swing (on hold) | Long-term |
|---|---|---|---|
| Risk per trade | 1% (less when VIX is high) | 1% | sized by weight |
| Largest position | 30% of balance as margin | 25% of pool | 15% (trim above 18%) |
| Positions | 3 open, 10 trades a day | 5 | 10 picks (ledger allows up to 12) |
| Per sector | 1 open, 2 trades a day | 2 | 2 picks per industry |
| Loss brake | 4R a day incl. open risk; 8R a week halves risk; 30-min cool-off after 3 losses | — | Sells what stops qualifying |
| Whole portfolio | Circuit breaker at 12% below the peak of all pools combined; `/resume` to restart | | |

Costs in the simulation: intraday Rs 20 brokerage a side plus 0.05% slippage, and from 16 Nov also STT, exchange
and SEBI fees, GST and stamp duty (about 0.26% of the position per round trip in all); delivery 0.12% a side plus
Rs 16 per sell plus slippage.

## Telegram, reports and analytics

| When | What you get |
|---|---|
| 08:45 | Pre-market brief |
| 09:08 | Day plan and focus list |
| During sessions | Proposals (suggestion, reviewer, plain words, market check, news), fills, exits, alerts |
| ~15:20 | Day report (with the day's paper tests and, from 23 Nov, the selector's picks for tomorrow) |
| Fri 17:55 | Weekly report |
| 15:50 (when something moves) | Swing shadow tests: what each paper pool bought or sold |
| After 16:10 | Shadow models: a short note on the day's results when the job finishes |
| Sat 10:15 | Long-term shadow reports (quality picks, momentum top 15, momentum top 5) |
| Any time | Answers to commands |

Databricks dashboard (redesigned 18 Nov, same order on every page: tiles, main chart, breakdowns, tables):
Desk (auto mode, VIX, mood; portfolio vs NIFTYBEES, track record by setup, today's decisions, real and shadow
leaderboard, holdings), Intraday (real vs every exit rule, model and the opening-range test in one chart, with a
Show filter; daily P&L, by setup and hour, model tests on ~200 stocks; from 21 Nov the auto-bar ladder and sizing,
paper fills vs the real minute and the selector), Swing and Long term (real pool vs shadow pools
vs NIFTYBEES with a Show filter; scanner, momentum ranks, company quality), Your calls (your 👍/👎 vs what happened:
right %, liked vs disliked results, you vs the trader, by week and setup) and Global markets (current by about 09:00
from the 08:45 pre-open snapshot; today's read written
from the data with fixed rules, world moves, who India's open follows, after-US-moves table, NSE breadth, ADRs). Money pages start at the 18 Nov ledger reset. Ask Genie on the
dashboard answers questions in plain words.

## Infrastructure and cost

Same three platforms: AWS free plan (Sydney), public GitHub Actions, Databricks Free Edition. See
[PLATFORM.md](PLATFORM.md) for every component and schedule.

| Piece | Runs on | Cost |
|---|---|---|
| Intraday sessions | GitHub Actions + Claude | Your Claude plan (the main cost) |
| Kicker, guardian, responder | AWS Lambda | Free tier |
| News fetcher | AWS Lambda (lakehouse) | Free tier |
| Fundamentals, shadow pools, shadow models, backtests | GitHub Actions, no Claude | Free (public repo) |
| Three pipelines (market, research, trading), dashboard, AI summaries | Databricks Free Edition | Daily compute quota |

Cost review: Sun 15 Nov.

## Ideas: status

| Idea | Status |
|---|---|
| Backtests on lakehouse history | Built (swing, long-term) |
| Market mood | Built |
| Shadow rule variants | Built, reviewed 23 Nov |
| You-vs-the-system scorecard | In the weekly report |
| Data health alerts | Built |
| Kill switch (`/stop`) | Built |
| One-tap reasons | Planned |
| Idle cash earns interest | Planned |
| Real-money readiness checklist | After phase 5 |
| Technical scanner (Nifty 200) | Built 17 Nov; review ~8 Dec |
| Shadow models (intraday) | Built 17 Nov, paper only; review before December |
| Early exit on a real break of the exit line | Live 20 Nov |
| India VIX from NSE's own index file | Live 20 Nov (Yahoo as the backup) |
| Opening buy, gap fade, auto-bar ladder, score sizing, fill check, swing holding period | Built 20 Nov, on paper from 21 Nov |
| Selector (picks tomorrow's version from the last 20 days) | Built 20 Nov, on paper from 23 Nov |
| Momentum shadows (swing and long-term) and concentrated copies | Built 17 Nov, on paper from 18 / 21 Nov; December review |
| Ratings as training data | Retired 17 Nov: your 👍/👎 is kept for your own review only |
| Bloomberg news | Not feasible (paid terminal licence; no free feed); free sources used instead |

## Roadmap

| Date | Step |
|---|---|
| 10 Nov | First live day of phases 1–2; swing pool into NIFTYBEES |
| 13 Nov | First weekly report |
| 14 Nov | First long-term shadow week |
| 15 Nov | Cost review: is Claude usage sustainable? |
| 16 Nov | News radar (Nifty 200 beyond the watchlist) and the swing shadow pool start; your own trades from Telegram (`/analyze`, `/buy`, `/short`, `/swing`, `/longterm`) |
| 17 Nov | Technical scanner (08:45 report, 09:45 check, `/screen`, second swing shadow pool); India-first plan with the freshness rule; more news sources; shadow models (16:10 daily); ratings no longer train the system; ledger reset; lakehouse pipeline split in three |
| 18 Nov | Day 1 after the reset: every pool from its starting cash; momentum + news swing shadow and its concentrated copy start; pre-open world-markets snapshot on the dashboard by ~09:00 |
| 19 Nov | Faster intraday cycles (a few seconds after each candle; stops every minute); on paper: 1.5R and yesterday's-level targets, wait-for-the-retest opening-range entries, stretched-breakout flag |
| 20 Nov | India VIX from NSE's own index file (Yahoo as the backup; cautious risk when neither has it); early exits only on a real break of the exit line, enforced by code |
| 21 Nov | First long-term momentum shadows (top 15, top 5); on paper: 09:15 opening buy, gap fade, auto-bar ladder, score sizing, no SELLs in an uptrend, swing holding period, fill check |
| 23 Nov | Track-record review (phase 5), including news vs watchlist picks; the selector's first picks (paper) |
| ~8 Dec | Scanner signal review (about 15 trading days): `gold.signal_performance` and the scanner shadow pool vs NIFTYBEES |
| 18 Dec | Long-term go/no-go (backtest + 5 shadow weeks) |
| 21 Dec | Long-term buying starts, if approved |

## Risks and decisions

| Risk | How it's handled |
|---|---|
| Too many messages | Long-term reports only on Saturdays; intraday caps |
| Data gaps (Yahoo lag, NSE blocking) | India VIX from NSE's own file with Yahoo as the backup, and cautious risk when neither has it; stale-data alerts; "news unavailable" shown honestly; fallbacks for the Nifty 200 list |
| Backtests flatter reality | No-hindsight universes, unseen-month tests, then shadow weeks |
| Claude usage limits | Swing and long-term use no Claude; cost review 15 Nov |
| Real money later | SEBI's retail algo framework (from 1 Apr 2026) needs a registered app ID on a whitelisted static IP, daily two-factor login and at most 10 orders a second ([FYERS notice](https://fyers.in/notice-board/new-sebi-framework-for-retail-algo-trading-from-april-01-2026/)); a static IP isn't on the AWS free plan. Out of scope |

| Decision (9 Nov 2026) | Choice |
|---|---|
| Pool sizes | Rs 2 lakh: intraday Rs 40,000, swing Rs 60,000, long-term Rs 1,00,000 |
| Intraday loss limit | Rs 4,000 (9 Nov); 4R incl. open risk, 8R weekly brake, cost filter (11 Nov) |
| Swing exits | Automatic on rules set at entry |
| Swing after failed backtests | Hold the Rs 60,000 in NIFTYBEES |
| Long-term universe | Nifty 200 |
| Phase 2 timing | Started 9 Nov |
| Ledger reset (17 Nov) | Every pool and shadow pool restarted with its starting cash on Wed 18 Nov, so all strategies are compared from the same day before the December review. Trade history, track record, ratings and test results kept; dashboard money pages start on 18 Nov |
| Concentration (17 Nov) | Pushing the limits is tested on paper, not applied: concentrated copies of both momentum shadows (swing top 3, ~33% each, 3% risk; long-term top 5, ~20% each; no sector caps) run beside the diversified ones until the December review |
| Pipelines (17 Nov) | The lakehouse pipeline split into market, research and trading (one runs at a time on Databricks Free Edition) |

## Sources

- [ClearTax: capital gains tax rates](https://cleartax.in/capital-gains/Index)
- [Precize: capital gains and Budget 2026](https://www.precize.in/blogs/capital-gains-tax-on-shares-in-india-ltcg-stcg-rates-holding-periods-and-budget-2026)
- [FYERS: SEBI retail algo framework from 1 Apr 2026](https://fyers.in/notice-board/new-sebi-framework-for-retail-algo-trading-from-april-01-2026/)
- [NSE RSS feeds](https://www.nseindia.com/rss-feed)
