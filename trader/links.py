"""Live-quote links for a stock, shown as buttons under each proposal.

Groww's stock pages are named after the company, not the NSE symbol: "Avanti Feeds Ltd." is
groww.in/stocks/avanti-feeds-ltd. The 36 watchlist stocks are listed here (checked by hand); any other tradable
stock's address is made from its company name in the lakehouse's tradable list (state/universe.json). On a phone
with the Groww app, groww.in links normally open in the app. A stock with no known name gets only the NSE button.
"""
import re
from urllib.parse import quote

import storage

_names = {"loaded": False, "map": {}}

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


def slug(company):
    """'Dr. Reddy's Laboratories Ltd.' -> 'dr-reddys-laboratories-ltd'; 'Larsen & Toubro Ltd.' -> 'larsen-toubro-ltd'."""
    s = (company or "").lower().replace("&", " ").replace("'", "").replace("’", "").replace(".", " ")
    s = re.sub(r"\blimited\b", "ltd", s)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _company(symbol):
    if not _names["loaded"]:
        try:
            _names["map"] = (storage.get_json("state/universe.json") or {}).get("names") or {}
        except Exception:  # noqa: BLE001 - a button is a convenience
            _names["map"] = {}
        _names["loaded"] = True
    return _names["map"].get(symbol.upper())


def reset_cache():
    _names.update(loaded=False, map={})


def groww(symbol):
    s = GROWW.get(symbol.upper()) or slug(_company(symbol))
    return f"https://groww.in/stocks/{s}" if s else None


def nse(symbol):
    return f"https://www.nseindia.com/get-quotes/equity?symbol={quote(symbol.upper(), safe='')}"


def buttons(symbol, pid):
    """The second row of buttons: Groww (if known), NSE quote, and the full reasons."""
    row = [("📈 Groww", url)] if (url := groww(symbol)) else []
    return row + [("NSE quote", nse(symbol)), ("ℹ️ Full reasons", f"info:{pid}")]
