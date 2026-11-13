"""Live-quote links for a stock, shown as buttons under each proposal.

Groww's stock pages are named after the company, not the NSE symbol, so each address is listed here (checked
by hand). On a phone with the Groww app, groww.in links normally open in the app. A stock not listed gets
only the NSE quote button, which works for any symbol.
"""
from urllib.parse import quote

GROWW = {
    "HDFCBANK": "hdfc-bank-ltd",
    "ICICIBANK": "icici-bank-ltd",
    "SBIN": "state-bank-of-india",
    "KOTAKBANK": "kotak-mahindra-bank-ltd",
    "AXISBANK": "axis-bank-ltd",
    "BAJFINANCE": "bajaj-finance-ltd",
    "BAJAJFINSV": "bajaj-finserv-ltd",
    "INFY": "infosys-ltd",
    "TCS": "tata-consultancy-services-ltd",
    "HCLTECH": "hcl-technologies-ltd",
    "WIPRO": "wipro-ltd",
    "TECHM": "tech-mahindra-ltd",
    "SUNPHARMA": "sun-pharmaceutical-industries-ltd",
    "DRREDDY": "dr-reddys-laboratories-ltd",
    "CIPLA": "cipla-ltd",
    "MARUTI": "maruti-suzuki-india-ltd",
    "M&M": "mahindra-mahindra-ltd",
    "BAJAJ-AUTO": "bajaj-auto-ltd",
    "EICHERMOT": "eicher-motors-ltd",
    "TATASTEEL": "tata-steel-ltd",
    "JSWSTEEL": "jsw-steel-ltd",
    "HINDALCO": "hindalco-industries-ltd",
    "RELIANCE": "reliance-industries-ltd",
    "ONGC": "oil-natural-gas-corporation-ltd",
    "NTPC": "ntpc-ltd",
    "POWERGRID": "power-grid-corporation-of-india-ltd",
    "COALINDIA": "coal-india-ltd",
    "ITC": "itc-ltd",
    "HINDUNILVR": "hindustan-unilever-ltd",
    "NESTLEIND": "nestle-india-ltd",
    "BHARTIARTL": "bharti-airtel-ltd",
    "LT": "larsen-toubro-ltd",
    "ULTRACEMCO": "ultratech-cement-ltd",
    "ADANIPORTS": "adani-ports-and-special-economic-zone-ltd",
    "TITAN": "titan-company-ltd",
    "ASIANPAINT": "asian-paints-ltd",
}


def groww(symbol):
    slug = GROWW.get(symbol.upper())
    return f"https://groww.in/stocks/{slug}" if slug else None


def nse(symbol):
    return f"https://www.nseindia.com/get-quotes/equity?symbol={quote(symbol.upper(), safe='')}"


def buttons(symbol, pid):
    """The second row of buttons: Groww (if known), NSE quote, and the full reasons."""
    row = [("📈 Groww", url)] if (url := groww(symbol)) else []
    return row + [("NSE quote", nse(symbol)), ("ℹ️ Full reasons", f"info:{pid}")]
