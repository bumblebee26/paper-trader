"""Watchlist by sector, and each sector's index for the market check (Yahoo symbols).

Sector caps in the risk gate use SECTOR_OF; a symbol not listed here counts as its own sector.
An index of None means no free index feed: the market check just skips the sector line.
"""
SECTORS = {
    "Banks": (("^NSEBANK", "Bank Nifty", "banks"),
              ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "INDUSINDBK", "BANKBARODA", "PNB",
               "FEDERALBNK", "IDFCFIRSTB", "AUBANK", "BANDHANBNK", "CANBK"]),
    "Financials": (None, ["BAJFINANCE", "BAJAJFINSV", "SHRIRAMFIN", "CHOLAFIN", "HDFCLIFE", "SBILIFE"]),
    "IT": (("^CNXIT", "Nifty IT", "IT companies"),
           ["INFY", "TCS", "HCLTECH", "WIPRO", "TECHM", "LTIM", "MPHASIS", "PERSISTENT", "COFORGE"]),
    "Pharma": (("^CNXPHARMA", "Nifty Pharma", "drug makers"), ["SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "LUPIN"]),
    "Auto": (("^CNXAUTO", "Nifty Auto", "carmakers"), ["MARUTI", "M&M", "BAJAJ-AUTO", "EICHERMOT", "HEROMOTOCO"]),
    "Metals": (("^CNXMETAL", "Nifty Metal", "metal companies"), ["TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL"]),
    "Energy": (("^CNXENERGY", "Nifty Energy", "energy companies"),
               ["RELIANCE", "ONGC", "NTPC", "POWERGRID", "COALINDIA", "BPCL"]),
    "FMCG": (("^CNXFMCG", "Nifty FMCG", "consumer-goods makers"), ["ITC", "HINDUNILVR", "NESTLEIND", "BRITANNIA"]),
    "Telecom": (None, ["BHARTIARTL"]),
    "Infra": (("^CNXINFRA", "Nifty Infra", "infrastructure companies"), ["LT", "ULTRACEMCO", "ADANIPORTS"]),
    "Consumer": (None, ["TITAN", "ASIANPAINT", "TRENT"]),
}

SECTOR_OF = {sym: name for name, (_, syms) in SECTORS.items() for sym in syms}
SECTOR_INDEX = {sym: idx for _, (idx, syms) in SECTORS.items() if idx for sym in syms}

# The default intraday watchlist: liquid large caps across 11 sectors (36 names).
WATCHLIST = [
    "HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK",
    "BAJFINANCE", "BAJAJFINSV",
    "INFY", "TCS", "HCLTECH", "WIPRO", "TECHM",
    "SUNPHARMA", "DRREDDY", "CIPLA",
    "MARUTI", "M&M", "BAJAJ-AUTO", "EICHERMOT",
    "TATASTEEL", "JSWSTEEL", "HINDALCO",
    "RELIANCE", "ONGC", "NTPC", "POWERGRID", "COALINDIA",
    "ITC", "HINDUNILVR", "NESTLEIND",
    "BHARTIARTL",
    "LT", "ULTRACEMCO", "ADANIPORTS",
    "TITAN", "ASIANPAINT",
]


def sector(symbol: str) -> str:
    return SECTOR_OF.get(symbol.upper(), symbol.upper())
