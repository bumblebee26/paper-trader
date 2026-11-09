# Paper Trader: complete system plan

> The full plan for the dummy-money trading system across both repositories, kept in git. The same file
> is in [paper-trader](https://github.com/bumblebee26/paper-trader/blob/main/docs/SYSTEM_PLAN.md) and
> [stocks_lakehouse](https://github.com/bumblebee26/stocks_lakehouse/blob/main/docs/SYSTEM_PLAN.md); change
> both together. For how the running system fits together, see [PLATFORM.md](PLATFORM.md).
> As of 9 Nov 2026. Each section says what is **built** and what is still **planned**.

Everything is dummy money. Nothing here is investment advice; it is a system for learning how these
strategies behave.

## Summary

The paper trader is growing from one intraday strategy into a three-pool dummy portfolio of Rs 2,00,000,
driven by live market data, company news and company financials, with you approving trades on Telegram.

| Phase | Status (9 Nov 2026) | Next gate |
|---|---|---|
| 1 Intraday upgrades | Built, live from 10 Nov | Track-record review, 23 Nov |
| 2 Foundations (news, pools, alerts) | Built | Cost review, 15 Nov |
| 3 Swing pool | On hold: rules failed two backtests; Rs 60,000 held in NIFTYBEES | Retry later with new ideas |
| 4 Long-term pool | Selection, backtest and shadow mode built | Go/no-go 18 Dec, buying from 21 Dec |
| 5 Track record in suggestions | Collecting data | Review 23 Nov |

## Principles

1. **Dummy money only.** No broker, demat or bank link. Real money would need a separate decision and SEBI's
   retail algo rules (see Risks).
2. **You approve every buy and sell** on Telegram. Automatic exits are limited to rules set in advance
   (stop-loss, target, the 15:10 intraday square-off, and for pools, rules you agreed to).
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
more; the daily loss limit was scaled to Rs 4,000 to stay at 10% of the pool.

## Phase 1: intraday upgrades (built, live 10 Nov)

| Setting | Before | Now |
|---|---|---|
| Trades per day | 5 | 10 |
| Daily loss limit | 2% | Rs 4,000; lifts again if a winning exit brings the day back under it |
| Cool-off | none | 30 minutes without new proposals after 3 losses in a row |
| Watchlist | 16 stocks, mostly banks and IT | 36 stocks across 11 sectors (`trader/sectors.py`) |
| Focus list each morning | up to 10 | up to 15, at least 6 sectors |
| Sector caps | none | 1 open position per sector, 2 trades per sector a day |
| Position cap | margin limit only | One trade uses at most 30% of the balance as margin |
| Proposal | numbers | Suggestion % (live checks + trading agent + independent reviewer), plain words, market check, company news |

## Phase 2: foundations (built)

| Part | What it does |
|---|---|
| Company news | NSE announcements, results, board meetings and corporate actions every 30 min into the lakehouse; every proposal lists the company's filings with links and flags regulatory, rating, pledge filings and board meetings within 2 days |
| Market mood | Each proposal says trending up, trending down, jumpy or choppy (Nifty since the open and VIX) |
| Shadow rule variants | Each proposal records which alternative rules would have blocked it (Nifty against the trade by 0.3%, against the mood, after 14:00), for the 23 Nov review |
| Commands | `/news SYMBOL`, `/portfolio`, `/stop` (kill switch) plus `/ledger`, `/plan`, `/pause`, `/resume`, `/exit`, `/help`, answered any time |
| Data alerts | Stale prices pause proposals with one alert and an all-clear; a news feed more than 90 minutes behind alerts once a day |
| Pool ledgers and circuit breaker | Separate ledgers for swing and long-term with delivery costs; all pools 12% below their peak stops new buys until `/resume` |
| Weekly report | Fridays 17:55: results, setups, your calls vs the suggestion and reviewer, background rules, all pools |
| Dashboard | Databricks pages Portfolio, Intraday trading, Company quality and Details, KPI tiles, charts, captions, and an AI "In plain words" summary per page |

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

A retry would add results-based setups once a few months of news history exist, judged the same way: tune on
one stretch, test once on unseen months, and beat holding the index.

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

## Phase 5: track record (collecting data)

Every proposal now records the suggestion %, the checks, trading agent and reviewer scores, and the shadow
rule blocks. At the 23 Nov review: how the suggestion and the reviewer lined up with your YES/NO calls and
the results, which background rules would have helped, then whether to feed your own track record (by setup,
hour and market mood) into the suggestion %.

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
| Loss brake | Rs 4,000 a day; 30-min cool-off after 3 losses | — | Sells what stops qualifying |
| Whole portfolio | Circuit breaker at 12% below the peak of all pools combined; `/resume` to restart | | |

Costs in the simulation: intraday about Rs 20 a side plus 0.05% slippage; delivery 0.12% a side plus Rs 16
per sell plus slippage.

## Telegram, reports and analytics

| When | What you get |
|---|---|
| 08:45 | Pre-market brief |
| 09:08 | Day plan and focus list |
| During sessions | Proposals (suggestion, reviewer, plain words, market check, news), fills, exits, alerts |
| ~15:20 | Day report |
| Fri 17:55 | Weekly report |
| Sat 10:15 | Long-term shadow report |
| Any time | Answers to commands |

Databricks dashboard: Portfolio (value vs NIFTYBEES, fall from peak, allocation, holdings), Intraday trading
(net P&L, win rate, profit factor, expectancy, equity curve, daily P&L, by setup and hour), Company quality
(quality vs price, this week's picks), Details; each page starts with an AI summary. Ask Genie on the
dashboard answers questions in plain words.

## Infrastructure and cost

Same three platforms: AWS free plan (Sydney), public GitHub Actions, Databricks Free Edition. See
[PLATFORM.md](PLATFORM.md) for every component and schedule.

| Piece | Runs on | Cost |
|---|---|---|
| Intraday sessions | GitHub Actions + Claude | Your Claude plan (the main cost) |
| Kicker, guardian, responder | AWS Lambda | Free tier |
| News fetcher | AWS Lambda (lakehouse) | Free tier |
| Fundamentals, shadow, backtests | GitHub Actions, no Claude | Free (public repo) |
| Pipeline, dashboard, AI summaries | Databricks Free Edition | Daily compute quota |

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

## Roadmap

| Date | Step |
|---|---|
| 10 Nov | First live day of phases 1–2; swing pool into NIFTYBEES |
| 13 Nov | First weekly report |
| 14 Nov | First long-term shadow week |
| 15 Nov | Cost review: is Claude usage sustainable? |
| 23 Nov | Track-record review (phase 5) |
| 18 Dec | Long-term go/no-go (backtest + 5 shadow weeks) |
| 21 Dec | Long-term buying starts, if approved |

## Risks and decisions

| Risk | How it's handled |
|---|---|
| Too many messages | Long-term reports only on Saturdays; intraday caps |
| Data gaps (Yahoo lag, NSE blocking) | Stale-data alerts; "news unavailable" shown honestly; fallbacks for the Nifty 200 list |
| Backtests flatter reality | No-hindsight universes, unseen-month tests, then shadow weeks |
| Claude usage limits | Swing and long-term use no Claude; cost review 15 Nov |
| Real money later | SEBI's retail algo framework (from 1 Apr 2026) needs a registered app ID on a whitelisted static IP, daily two-factor login and at most 10 orders a second ([FYERS notice](https://fyers.in/notice-board/new-sebi-framework-for-retail-algo-trading-from-april-01-2026/)); a static IP isn't on the AWS free plan. Out of scope |

| Decision (9 Nov 2026) | Choice |
|---|---|
| Pool sizes | Rs 2 lakh: intraday Rs 40,000, swing Rs 60,000, long-term Rs 1,00,000 |
| Intraday loss limit | Rs 4,000 |
| Swing exits | Automatic on rules set at entry |
| Swing after failed backtests | Hold the Rs 60,000 in NIFTYBEES |
| Long-term universe | Nifty 200 |
| Phase 2 timing | Started 9 Nov |

## Sources

- [ClearTax: capital gains tax rates](https://cleartax.in/capital-gains/Index)
- [Precize: capital gains and Budget 2026](https://www.precize.in/blogs/capital-gains-tax-on-shares-in-india-ltcg-stcg-rates-holding-periods-and-budget-2026)
- [FYERS: SEBI retail algo framework from 1 Apr 2026](https://fyers.in/notice-board/new-sebi-framework-for-retail-algo-trading-from-april-01-2026/)
- [NSE RSS feeds](https://www.nseindia.com/rss-feed)
