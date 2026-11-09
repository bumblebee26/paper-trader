"""Shared fakes: in-memory storage, fixed clock, fake Telegram and prices. No network/AWS."""
import os
import sys
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "trader"))
sys.modules.setdefault("boto3", mock.MagicMock())

import config  # noqa: E402
import market  # noqa: E402
import news  # noqa: E402
import nse_calendar  # noqa: E402
import storage  # noqa: E402
import telegram_bot as tg  # noqa: E402

USER_ID = "8674025639"
NSE_2026 = {"2026-11-08": "Diwali Laxmi Pujan", "2026-11-10": "Diwali-Balipratipada",
            "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev", "2026-12-25": "Christmas"}


class MemoryStore:
    def __init__(self):
        self.files = {}

    def get(self, key):
        return self.files.get(key)

    def put(self, key, text):
        self.files[key] = text

    def exists(self, key):
        return key in self.files


class Env:
    """Patches clock, storage, Telegram and prices for one test."""

    def __init__(self, when=datetime(2026, 11, 9, 10, 40), capital=100_000):
        self.capital = capital  # tests of the mechanics use Rs 1 lakh; the live intraday pool is config's
        self.clock = when.replace(tzinfo=market.IST)
        self.store = MemoryStore()
        self.sent, self.edits, self.updates = [], [], []
        self.prices, self.bars = {}, {}
        self._patches = []

    def __enter__(self):
        storage.reset_backend(self.store)
        os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"
        os.environ["TELEGRAM_CHAT_ID"] = USER_ID
        os.environ.pop("ALLOWED_USER_ID", None)

        def fake_api(method, http_timeout=30, **p):
            if method == "getUpdates":
                return [u for u in self.updates if u["update_id"] >= p.get("offset", 0)]
            return True

        def fake_send(text, buttons=None):
            self.sent.append(text)
            return len(self.sent)

        self._patches = [
            mock.patch.object(market, "now_ist", lambda: self.clock),
            mock.patch.object(tg, "_api", fake_api),
            mock.patch.object(tg, "send", fake_send),
            mock.patch.object(tg, "edit", lambda m, t: self.edits.append(t)),
            mock.patch.object(market, "last_price", lambda s: (self.prices.get(s), "2026-11-09 10:40")),
            mock.patch.object(market, "candles", lambda s, i="5m", d=1: self.bars.get((s, i), [])),
            mock.patch.object(config, "STARTING_CAPITAL", self.capital),
        ]
        news.reset_cache()
        nse_calendar.reset_cache()
        self.nse_holidays = dict(NSE_2026)  # what the fake NSE holiday list returns; None = NSE unreachable
        self._patches.append(mock.patch.object(nse_calendar, "fetch", self._fake_nse))
        self.news_files = {}  # date -> list of items; empty = feed unavailable
        self._patches.append(mock.patch.object(news, "_read", self._fake_news))
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()
        storage.reset_backend(None)

    def _fake_nse(self):
        if self.nse_holidays is None:
            raise RuntimeError("NSE unreachable")
        return dict(self.nse_holidays)

    def _fake_news(self, day):
        if day not in self.news_files:
            raise RuntimeError("HTTP 404")
        return self.news_files[day]

    def add_news(self, symbol, category, description, when="2026-11-09 10:05:00", link="https://x/y.pdf"):
        day = when[:10]
        self.news_files.setdefault(day, []).append({
            "id": f"{symbol}{len(self.news_files[day])}", "feed": "announcement", "symbol": symbol,
            "category": category, "title": symbol, "description": description, "link": link,
            "published_ist": when, "fetched_at_ist": when})
        news.reset_cache()

    def at(self, h, m):
        self.clock = self.clock.replace(hour=h, minute=m)

    def tap(self, verdict, pid, user=USER_ID):
        n = len(self.updates) + 1
        self.updates.append({"update_id": n, "callback_query": {"id": str(n), "from": {"id": int(user)},
                                                                  "data": f"{verdict}:{pid}"}})

    def text(self, body, user=USER_ID):
        n = len(self.updates) + 1
        self.updates.append({"update_id": n, "message": {"from": {"id": int(user)}, "text": body}})


def minute_bars(start, rows):
    """rows: list of (open, high, low, close) starting at `start` (datetime), 1-minute apart."""
    return [{"time": (start + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M"), "open": o, "high": h,
             "low": lo, "close": c, "volume": 1000} for i, (o, h, lo, c) in enumerate(rows)]
