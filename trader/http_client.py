"""HTTP helper (standard library only), same behaviour as stocks_lakehouse fetchers/common.py."""
import json
import logging
import time
import urllib.error
import urllib.request

log = logging.getLogger("trader")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) paper-trader/2.0"


class NotFound(Exception):
    pass


def request(method, url, *, headers=None, data=None, retries=3, backoff_seconds=3, timeout=30,
            log_url=None):
    """Return the response body (bytes). Retries network errors, 429 and 5xx."""
    shown = log_url or url
    hdrs = {"User-Agent": USER_AGENT}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    last = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as err:
            if err.code == 404:
                raise NotFound(shown) from err
            if err.code not in (429, 500, 502, 503, 504):
                detail = err.read()[:300] if hasattr(err, "read") else b""
                raise RuntimeError(f"HTTP {err.code} from {method} {shown}: {detail!r}") from err
            last = err
        except urllib.error.URLError as err:
            last = err
        if attempt < retries:
            time.sleep(backoff_seconds * attempt)
    raise RuntimeError(f"{method} {shown} failed after {retries} attempts: {last}")


def get_json(url, **kw):
    return json.loads(request("GET", url, **kw))
