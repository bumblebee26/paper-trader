# paper-trader wiki

📝 Project write-up on LinkedIn: <https://lnkd.in/p/deDTKYeT>

An AI-assisted **NSE paper-trading desk**. Claude (in a GitHub Actions job) finds and explains trades; plain Python
enforces every risk rule and keeps the ledger; you rate or approve on Telegram. **Dummy money only**: no broker, no bank.

| Pool | Dummy capital | Live mode today |
|---|---|---|
| Intraday | Rs 40,000 | Trading in auto mode (55% bar, +5 for losing groups), 1% risk per trade (less when India VIX is high), squared off at 15:10 |
| Swing | Rs 60,000 | Parked in NIFTYBEES; every swing idea runs as a shadow pool |
| Long-term | Rs 1,00,000 | Cash; quality and momentum picks run as shadow pools |

## Pages
- [How it works](How-it-works.md): the day, the life of one trade, who does what
- [Strategies and shadow tests](Strategies-and-shadow-tests.md): what trades live, what is only measured, and how changes go live
- [Telegram commands](Telegram-commands.md)
- [FAQ](FAQ.md)

## Deeper docs
[Architecture](../ARCHITECTURE.md) · [Platform](../PLATFORM.md) · [Setup](../SETUP.md) ·
[Operations](../OPERATIONS.md) · [Troubleshooting](../TROUBLESHOOTING.md) · [System plan](../SYSTEM_PLAN.md)

The market data, scanner, news radar and dashboard live in
[stocks_lakehouse](https://github.com/bumblebee26/stocks_lakehouse).

Licensed under Apache 2.0 · © 2026 Viren Baria · not investment advice.
