"""Expiry calendar: reminders 30, 7, 3 and 1 days before, then daily once expired; once a day."""
import unittest
from datetime import date, datetime

from helpers import Env

import expiry
import storage

ITEMS = [{"name": "Databricks token", "expires": "2027-05-06", "renew": "new token"}]


class ExpiryTest(unittest.TestCase):
    def test_due_days(self):
        self.assertEqual(expiry.due(date(2027, 4, 6), ITEMS)[0][1], 30)
        self.assertEqual(expiry.due(date(2027, 4, 29), ITEMS)[0][1], 7)
        self.assertEqual(expiry.due(date(2027, 4, 20), ITEMS), [])
        self.assertEqual(expiry.due(date(2027, 5, 8), ITEMS)[0][1], -2)

    def test_check_sends_once_a_day_and_reads_overrides(self):
        with Env(datetime(2027, 4, 29, 15, 45)) as env:
            storage.put_json("config/expiries.json", ITEMS)
            self.assertEqual(expiry.check(date(2027, 4, 29)), {"reminders": 1})
            self.assertEqual(expiry.check(date(2027, 4, 29)), {"skipped": "already checked today"})
            self.assertEqual(len([m for m in env.sent if "Renewal reminder" in m]), 1)
            self.assertIn("in 7 days", env.sent[-1])

    def test_defaults_cover_the_four_deadlines(self):
        names = " ".join(i["name"] for i in expiry.DEFAULTS)
        for word in ("Databricks", "AWS free plan", "Claude", "GitHub"):
            self.assertIn(word, names)


if __name__ == "__main__":
    unittest.main()
