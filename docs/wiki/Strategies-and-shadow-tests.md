# Strategies and shadow tests

**Live** means it moves the dummy ledger. **Shadow** means it is only measured: replayed or paper-traded next to
the live rules, never placing an order. A shadow becomes live only after a review shows it beats the live rules
out of sample.

## Live
- **Intraday setups**: opening-range breakout, VWAP reclaim/rejection, yesterday's high/low break; 2R target; strict
  early exit on a real break of the trade's line (two 5-minute closes past it, or one 0.2%+ past it).
- **Auto mode**: placed only above the suggestion bar (55%, +5 for groups with a losing record, 60% to flip a
  stock's direction); at most 6 a day.

## Shadow (paper only)
| Family | Versions |
|---|---|
| Exit rules (every real trade replayed) | the fixed 2R target (live until the switch), stop to entry at +1R, half at +1R, trailing (built for live use, switched on only by a review), 60-min time stop, no stop, 1.5×/2× wider stops, stop on a 5-min close, 1.5R target, yesterday's high/low as target, early exit on any close, strict early exit, stop and target only; day profit lock |
| Entries | opening-range test (5- and 15-min ranges; 2R, 1.5R, level, held to 15:10), wait for the retest, 09:15 opening buy (double slippage), gap fade short |
| Background rules | first 15 minutes, after 14:00, wrong side of VWAP, weaker than its sector, stretched breakouts, SELL in an uptrend |
| Filtering and size | auto-bar ladder (50–70%), size by score (0.5× / 1× / 1.5×), skipped proposals' outcomes |
| Models | trade filter, volatility stop, early cut, let winners run: walk-forward on ~200 stocks every night |
| Selector | picks tomorrow's exit rule, bar and entry from the last 20 days (min 30 trades, shrunk averages, switch on a clear edge) |
| Fills | each paper fill vs the real 1-minute candle of its minute |
| Swing pools | news breakouts, technical scanner, momentum + news, momentum concentrated; 5- and 10-day holding periods; vs NIFTYBEES |
| Long-term pools | quality picks, momentum top 15, momentum top 5; vs NIFTYBEES |

## Backtests
Price-only. Swing dip and 55-day breakout rules lost money (hence NIFTYBEES); intraday setups are re-tested
walk-forward every night. News strategies are not backtested: there is no unbiased news history yet. NSE filings
are archived with their real arrival times, so they can be backtested honestly after a few months.

## How a change goes live
Backtest (where possible) → shadow for weeks → review of out-of-sample results → a pull request to `main`.
Reviews: 23 Nov, then before the December go-live.
