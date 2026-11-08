"""Where the dummy ledger, journal and reports live.

STATE_BUCKET set   -> S3 (GitHub Actions sessions and the guardian Lambda share it)
STATE_BUCKET empty -> local folder (laptop / Claude Desktop)

Layout (same keys in both):
  state/ledger.json              balance, positions, proposals, Telegram offset
  journal/YYYY-MM-DD.csv         one row per event
  reports/YYYY-MM-DD.json        end-of-day summary
  plans/YYYY-MM-DD.json          day plan built from the lakehouse pre-market data
  sessions/YYYY-MM-DD/<phase>    marker: that session already started
  heartbeat.json                 last time a live session checked in
"""
import csv
import io
import json

import config

LEDGER_KEY = "state/ledger.json"


class _Local:
    def __init__(self, root):
        self.root = root

    def get(self, key):
        p = self.root / key
        return p.read_text(encoding="utf-8") if p.exists() else None

    def put(self, key, text):
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(p)

    def exists(self, key):
        return (self.root / key).exists()


class _S3:
    def __init__(self, bucket):
        import boto3

        self.bucket = bucket
        self.s3 = boto3.client("s3", region_name=config.AWS_REGION)

    def get(self, key):
        try:
            return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read().decode("utf-8")
        except self.s3.exceptions.NoSuchKey:
            return None

    def put(self, key, text):
        ctype = "text/csv" if key.endswith(".csv") else "application/json"
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=text.encode("utf-8"), ContentType=ctype)

    def exists(self, key):
        return self.get(key) is not None


_backend = None


def backend():
    global _backend
    if _backend is None:
        _backend = _S3(config.STATE_BUCKET) if config.STATE_BUCKET else _Local(config.LOCAL_DATA_DIR)
    return _backend


def reset_backend(obj=None):
    """For tests: swap the backend."""
    global _backend
    _backend = obj


def get_json(key, default=None):
    text = backend().get(key)
    return json.loads(text) if text else default


def put_json(key, obj):
    backend().put(key, json.dumps(obj, indent=2, default=str))


def exists(key):
    return backend().exists(key)


def append_row(key, columns, row):
    """Append one CSV row (read-modify-write; fine for a few dozen rows a day)."""
    text = backend().get(key) or ""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    if not text:
        w.writeheader()
    w.writerow({c: row.get(c, "") for c in columns})
    backend().put(key, text + buf.getvalue())


def read_rows(key):
    text = backend().get(key)
    return list(csv.DictReader(io.StringIO(text))) if text else []
