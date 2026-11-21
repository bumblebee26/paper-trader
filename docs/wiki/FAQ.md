# FAQ

**Is any real money involved?** No. Ledgers are JSON files in S3; a fill is a simulated price with costs added.

**Why was a trade placed at the day's high?** Breakouts buy a new high by definition. "Stretched" breakouts and a
wait-for-the-retest entry are being measured as shadow tests.

**Why was a trade skipped?** Auto mode places only trades above the suggestion bar; the day report and `/shadow`
show what every skipped trade would have made.

**Why so few shares?** Size is set by risk: about 1% of the pool lost at the stop, not by the cash available. With
5× intraday leverage, margin in use is a fifth of the position.

**Does it close my own trades?** Yes: at the stop, the target, a real break of the exit line, or the 15:10
square-off. `/exit SYMBOL` closes one earlier.

**How honest are the fills?** 0.05% slippage on every entry and exit, full Indian charges, stops filled at the gap
price if price jumps through, and a trade is skipped if the price drifts 0.3% before the fill. Each fill is also
compared with the real 1-minute candle.

**Does it learn?** The track record steers the score and the auto bar automatically; models and new rules run as
shadows and change live trading only after a review. Your 👍/👎 ratings are never used for training.

**Where is the data?** S3 (ledger, journal) and the Databricks lakehouse (tables and the "NSE Paper Trading Desk"
dashboard).
