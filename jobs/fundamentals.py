"""Company fundamentals for the long-term pool: weekly, from Yahoo Finance into the lakehouse.

  python jobs/fundamentals.py            # needs AWS credentials (SSM: Databricks settings)
  python jobs/fundamentals.py --dry-run  # fetch and print, push nothing

Companies: NSE's official Nifty 200 list (with each company's industry, also pushed to the
lakehouse as index_members/) plus the intraday watchlist. If the list can't be read, the 250 most
traded NSE stocks of the last 120 sessions stand in for it. For each, Yahoo's fundamentals
time series gives 4 fiscal years of annual figures and the last quarters. One JSON line per
company x metric x period goes to the lakehouse landing volume, fundamentals/date=<today>/,
where stocks.gold.company_fundamentals turns them into the long-term quality tests.

Runs in GitHub Actions (workflow "Company fundamentals", Saturdays 07:15 IST): Yahoo answers
GitHub's runners reliably, and this repo already holds the Databricks push token.
"""
import json
import os
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "trader"), os.path.join(HERE, "..", "backtest")]

import market  # noqa: E402
import sectors  # noqa: E402
import storage  # noqa: E402
from http_client import log, request  # noqa: E402

ANNUAL = ["TotalRevenue", "NetIncome", "EBIT", "TotalDebt", "StockholdersEquity", "OperatingCashFlow",
          "InvestedCapital", "DilutedEPS", "TotalAssets"]
QUARTERLY = ["TotalRevenue", "NetIncome", "DilutedEPS"]
URL = ("https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{sym}"
       "?type={types}&period1=1500000000&period2={now}")
HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/130.0 Safari/537.36"), "Accept": "application/json"}
UNIVERSE_SQL = """
WITH recent AS (SELECT DISTINCT trade_date FROM stocks.silver.nse_eq_daily ORDER BY trade_date DESC LIMIT 120)
SELECT symbol, percentile_approx(turnover, 0.5) AS median_turnover, count(*) AS sessions
FROM stocks.silver.nse_eq_daily JOIN recent USING (trade_date)
WHERE series = 'EQ'
GROUP BY symbol HAVING sessions >= 100 ORDER BY median_turnover DESC LIMIT 250
"""
SKIP = {"NIFTYBEES", "BANKBEES", "GOLDBEES", "LIQUIDBEES", "SILVERBEES"}  # funds have no accounts
NIFTY200_URLS = [
    "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv",
    "https://archives.nseindia.com/content/indices/ind_nifty200list.csv",
    "https://niftyindices.com/IndexConstituent/ind_nifty200list.csv",
]


def parse_members(text):
    """NSE index list CSV (Company Name, Industry, Symbol, Series, ISIN Code) -> {symbol: row}."""
    import csv
    import io

    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        sym = row.get("symbol")
        if sym:
            out[sym] = {"company": row.get("company name", ""), "industry": row.get("industry", ""),
                        "isin": row.get("isin code", "")}
    return out


def nifty200():
    for url in NIFTY200_URLS:
        try:
            body = request("GET", url, headers={**HEADERS, "Accept": "text/csv,*/*",
                                                "Referer": "https://www.nseindia.com/"}, timeout=30)
            members = parse_members(body.decode("utf-8-sig", "replace") if isinstance(body, bytes) else body)
            if len(members) >= 150:
                print(f"Nifty 200 list: {len(members)} companies from {url}")
                return members
        except Exception as err:  # noqa: BLE001 - try the next source
            log.warning("Nifty 200 list from %s failed: %s", url, err)
    return {}


def universe(members=None):
    if members:
        syms = list(members)
    else:
        import run_swing  # SQL with result chunks

        print("Nifty 200 list unavailable: using the 250 most traded stocks instead")
        syms = [r["symbol"] for r in run_swing.sql_all(UNIVERSE_SQL)]
    return [s for s in dict.fromkeys(syms + list(sectors.WATCHLIST)) if s not in SKIP and not s.endswith("BEES")]


def parse(symbol, body, fetched_at):
    """Yahoo timeseries JSON -> rows (one per metric x period)."""
    data = json.loads(body)
    rows = []
    for r in (data.get("timeseries") or {}).get("result") or []:
        kind = (r.get("meta", {}).get("type") or [""])[0]
        prefix, metric = ("12M", kind[6:]) if kind.startswith("annual") else \
                         ("3M", kind[9:]) if kind.startswith("quarterly") else (None, None)
        if not prefix:
            continue
        for v in r.get(kind) or []:
            if not v or v.get("reportedValue", {}).get("raw") is None:
                continue
            rows.append({"symbol": symbol, "source_symbol": f"{symbol}.NS", "metric": metric,
                         "period_type": v.get("periodType") or prefix, "period_end": v["asOfDate"],
                         "value": float(v["reportedValue"]["raw"]), "currency": v.get("currencyCode") or "INR",
                         "source": "yahoo_fundamentals", "fetched_at_ist": fetched_at})
    return rows


def fetch(symbol, fetched_at, now_epoch):
    types = ",".join([f"annual{m}" for m in ANNUAL] + [f"quarterly{m}" for m in QUARTERLY])
    url = URL.format(sym=urllib.parse.quote(f"{symbol}.NS", safe=""), types=types, now=now_epoch)
    for attempt in range(3):
        try:
            return parse(symbol, request("GET", url, headers=HEADERS, timeout=30), fetched_at)
        except Exception as err:  # noqa: BLE001
            if attempt == 2:
                log.warning("%s: %s", symbol, err)
                return None
            time.sleep(2 * (attempt + 1))


def main(argv):
    dry = "--dry-run" in argv
    now = market.now_ist()
    fetched_at = now.strftime("%Y-%m-%d %H:%M:%S")
    members = nifty200()
    symbols = universe(members)
    print(f"{len(symbols)} companies")
    rows, failed, empty = [], [], []
    for i, s in enumerate(symbols):
        got = fetch(s, fetched_at, int(time.time()))
        if got is None:
            failed.append(s)
        elif not got:
            empty.append(s)
        else:
            rows += got
        time.sleep(0.4)
        if i and i % 50 == 0:
            print(f"  {i} done, {len(rows)} figures")
    print(f"{len(rows)} figures for {len(symbols) - len(failed) - len(empty)} companies; "
          f"no data: {len(empty)} {empty[:20]}; failed: {len(failed)} {failed[:20]}")
    if len(failed) > len(symbols) / 2:
        raise SystemExit("More than half the companies failed: Yahoo is probably blocking; nothing pushed.")
    body = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows)
    day = now.strftime("%Y-%m-%d")
    members_body = "".join(json.dumps({"symbol": s, **m, "index_name": "NIFTY 200", "as_of": day},
                                      separators=(",", ":")) + "\n" for s, m in members.items())
    if dry:
        print(body[:1500])
        print(members_body[:600])
        return 0
    storage.backend().put(f"fundamentals/date={day}/fundamentals.jsonl", body)
    import export

    print("pushed to", export._push(f"fundamentals/date={day}/fundamentals.jsonl", body))
    if members_body:
        storage.backend().put(f"index_members/date={day}/nifty200.jsonl", members_body)
        print("pushed to", export._push(f"index_members/date={day}/nifty200.jsonl", members_body))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
