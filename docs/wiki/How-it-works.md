# How it works

## A trading day (IST, Mon–Fri)
| Time | What happens |
|---|---|
| 08:45 | Pre-market brief and technical scanner on Telegram (lakehouse); world-markets snapshot saved |
| 09:08 | Morning session starts: India VIX sets the risk per trade, Nifty 50 trend sets the bias, focus list = watchlist + news radar |
| 09:30–14:30 | New entries allowed. The session wakes ~3 s after each 5-minute candle, scans 4 stocks at a time, proposes trades |
| every minute | Stops, targets, your `/exit` and the trade's early-exit line are checked between candles |
| 12:27 | Afternoon session takes over (GitHub jobs are capped at 6 hours) |
| 15:10 | Every intraday position is squared off; ~15:20 day report with all shadow tests |
| 15:45 | Journal and portfolio snapshot exported to the lakehouse |
| 15:50 | Swing shadow pools updated (guardian Lambda) |
| 16:10 | Shadow models: walk-forward backtest on ~200 stocks |
| Fri 17:55 | Weekly report |

## The life of one trade
1. **Claude finds a setup** (opening-range breakout, VWAP reclaim/rejection, yesterday's high/low break) and writes the case: entry, stop, target, an exit line, at most three reasons including one honest risk.
2. **Code checks the risk gate**: kill switch, pause, loss limits, max 5 open / 10 a day, sector caps, the stock not weaker than its sector (from 24 Nov), reward ≥ 1.5× risk, margin. A failure is journaled and nothing is sent.
3. **Code sizes it**: about 1% of the pool lost if the stop is hit.
4. **Three scores** are averaged into the suggestion: live market checks, Claude's own view and an independent reviewer (a second Claude that never sees the pitch).
5. **Auto mode** places it only if the suggestion clears the bar; manual mode waits for your YES.
6. **Fill**: price + 0.05% slippage + full charges (brokerage, STT, exchange, SEBI, GST, stamp duty).
7. **Exit**: stop, target, a real break of the exit line, your `/exit`, or the 15:10 square-off.
8. **Learning**: every proposal, placed or skipped, gets a real outcome; the track record by setup, hour, mood and sector feeds the next session.

## Who does what
| Part | Role |
|---|---|
| Claude (session) | Finds and explains trades; never moves money itself |
| `trader/engine.py` | Risk officer and accountant: gate, sizing, fills, exits, ledger |
| `trader/reviewer.py` | Devil's advocate: a second Claude with no tools |
| Guardian Lambda | Backstops: square-off, reports, exports if the session didn't |
| Responder Lambda | Answers Telegram outside sessions |
| S3 state bucket | Ledger, journal, plans, reports: the only memory between runs |
