# What the trader is for

*From 24 Nov 2026. Dummy money only; no broker or bank is connected.*

## The goal
**Learn which setups survive costs on the NSE, so that any real-money decision rests on evidence.**

- The bar for real money: 200 trades with the lower 95% bound of the average R above 0 (`DECISIONS.md` section 12).
  Until then the default answer is "no go".
- A skipped trade costs nothing; a weak trade costs fees and teaches little. Skipping is fine.
- Every idea is a paper variant first, with a baseline and a kill rule (`VARIANTS.csv`). Live rules change only at
  reviews (1 Dec, then monthly).

## What wins
- After costs, not before: fees and slippage are about 0.26R a trade on intraday setups.
- Beating the baseline by more than luck (the luck line in the weekly report), on weeks it didn't see.
- Swing and long term: beating simply holding NIFTYBEES over the same days.

## What it must avoid
- Trading to fill a day, or widening a stop to get past a limit.
- Invented news or numbers; every reason uses numbers from the tools.
- Untested outside ideas going straight into the rules (a chat suggestion becomes a paper variant, see
  `DECISIONS.md` section 14).

## Exactly what the trader is told
- The trading session's instructions: `prompts/trading_rules.md` (its "The goal" section repeats the three lines above).
- The independent reviewer's instructions: `trader/reviewer.py` (it sees only facts, no tools).
- What each proposal shows you: the compact message, plus "Full reasons" behind the button.

## Checking the goal line didn't change behaviour by accident
The trading agent's YES % is in every `proposed` journal row. Compare the two weeks before 25 Nov with the two
weeks after; a big shift is discussed at the next review.
