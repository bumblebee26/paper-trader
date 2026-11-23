# Telegram commands

Commands work with or without the `/`. During sessions (09:08–15:25 IST) replies come within seconds; otherwise
within about 2 minutes.

| Command | What it does |
|---|---|
| `/ledger` (`/balance`, `/status`) | Balance, money in use, free, open positions with live P&L |
| `/plan` | Today's plan: India VIX and risk, Nifty 50 trend, scanner picks, focus list |
| `/portfolio` | All dummy pools: intraday, swing, long-term |
| `/exit SYMBOL` | Close that intraday position now (the symbol is required) |
| `/buy SYMBOL [qty] [sl X] [tgt Y]` | Your own intraday buy; defaults: 1% stop, 2× target, a normal trade's size |
| `/short SYMBOL [qty] [sl X] [tgt Y]` | Your own intraday short (sell first, buy back by 15:10) |
| `/swing buy\|sell SYMBOL [qty] [sl X] [tgt Y]` | Your own swing trade (delivery) |
| `/longterm buy\|sell SYMBOL [qty]` (`/lt`) | Your own long-term trade |
| `/swing auto on\|off`, `/longterm auto on\|off` | Auto mode for the pool: it follows its rules by itself, with a message for every buy and sell (swing: momentum + news, on from 24 Nov; long-term: quality picks, off until the 18 Dec review) |
| `/analyze SYMBOL` | The live checks on any NSE stock: intraday, swing, long-term |
| `/screen [SYMBOL]` | Today's technical scanner, or one stock |
| `/news SYMBOL` | The company's latest NSE filings |
| `/shadow` | Everything tested on paper this week |
| `/auto on\|off` | Auto mode on (placed above the bar, you rate 👍/👎) or off (every trade needs your YES) |
| `/pause` · `/stop` · `/resume` | No new proposals today · kill switch · undo both |
| `YES` / `NO` | Answer the latest proposal (or tap its buttons) |
| `/help` | The full list |

Your own orders always ask you to Confirm first and are marked custom. Sizing: the stop costs about 1% of the pool
unless you give a quantity; above 1.5× a normal risk you get a warning.
