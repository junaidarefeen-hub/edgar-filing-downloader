"""SEC EDGAR API client — CIK lookup, filing metadata, file download, rate limiting."""

import os
import time
import threading
from collections import deque

import requests

import config


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    """Sliding-window rate limiter. Thread-safe."""

    def __init__(self, max_requests=None, window=None):
        self._max = max_requests or config.RATE_LIMIT_REQUESTS
        self._window = window or config.RATE_LIMIT_WINDOW
        self._lock = threading.Lock()
        self._timestamps: deque[float] = deque()

    def wait(self):
        """Block until a request slot is available."""
        with self._lock:
            now = time.monotonic()
            # Purge timestamps older than the window
            while self._timestamps and now - self._timestamps[0] >= self._window:
                self._timestamps.popleft()
            if len(self._timestamps) >= self._max:
                sleep_until = self._timestamps[0] + self._window
                sleep_time = sleep_until - now
                if sleep_time > 0:
                    time.sleep(sleep_time)
            self._timestamps.append(time.monotonic())


_rate_limiter = RateLimiter()


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _sec_get(url, stream=False):
    """GET a URL from SEC with User-Agent and rate limiting.

    Returns the Response object if stream=True, otherwise parsed JSON.
    Retries once on 429 or 5xx.
    """
    headers = {"User-Agent": config.SEC_USER_AGENT}

    for attempt in range(2):
        _rate_limiter.wait()
        resp = requests.get(url, headers=headers, stream=stream, timeout=30)

        if resp.status_code == 200:
            if stream:
                return resp
            return resp.json()

        if resp.status_code == 429 and attempt == 0:
            time.sleep(5)
            continue

        if resp.status_code >= 500 and attempt == 0:
            time.sleep(2)
            continue

        resp.raise_for_status()

    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Ticker → CIK lookup (cached)
# ---------------------------------------------------------------------------

_tickers_cache: dict | None = None
_tickers_cache_time: float = 0


def _get_tickers_map() -> dict:
    """Fetch and cache the SEC ticker→CIK mapping. Refreshes every 24 hours."""
    global _tickers_cache, _tickers_cache_time

    if _tickers_cache is not None and (time.time() - _tickers_cache_time < 86400):
        return _tickers_cache

    data = _sec_get(config.COMPANY_TICKERS_URL)
    # data is {"0": {"cik_str": "320193", "ticker": "AAPL", "title": "Apple Inc."}, ...}
    mapping = {}
    for entry in data.values():
        ticker = entry["ticker"].upper()
        cik = str(entry["cik_str"]).zfill(10)
        mapping[ticker] = {"cik": cik, "name": entry["title"]}

    _tickers_cache = mapping
    _tickers_cache_time = time.time()
    return _tickers_cache


def clear_tickers_cache():
    """Clear the cached ticker map (useful for testing)."""
    global _tickers_cache, _tickers_cache_time
    _tickers_cache = None
    _tickers_cache_time = 0


def lookup_cik(ticker: str) -> dict | None:
    """Look up a ticker symbol and return {"cik": "0000320193", "name": "Apple Inc."} or None."""
    mapping = _get_tickers_map()
    return mapping.get(ticker.upper())


# ---------------------------------------------------------------------------
# Fetch filing metadata
# ---------------------------------------------------------------------------

def _parse_recent(recent: dict) -> list[dict]:
    """Convert the parallel arrays from the submissions endpoint into a list of dicts."""
    count = len(recent.get("accessionNumber", []))
    result = []
    for i in range(count):
        result.append({
            "accessionNumber": recent["accessionNumber"][i],
            "filingDate": recent["filingDate"][i],
            "reportDate": recent.get("reportDate", [""])[i] if i < len(recent.get("reportDate", [])) else "",
            "form": recent["form"][i],
            "primaryDocument": recent.get("primaryDocument", [""])[i] if i < len(recent.get("primaryDocument", [])) else "",
            "primaryDocDescription": recent.get("primaryDocDescription", [""])[i] if i < len(recent.get("primaryDocDescription", [])) else "",
        })
    return result


def fetch_all_filings(cik: str) -> list[dict]:
    """Fetch all filing metadata for a CIK, handling pagination for large filers.

    Returns a list of dicts with keys: accessionNumber, filingDate, reportDate,
    form, primaryDocument, primaryDocDescription.
    """
    url = config.SUBMISSIONS_URL_TEMPLATE.format(cik=cik)
    data = _sec_get(url)

    filings = _parse_recent(data["filings"]["recent"])

    # Fetch additional pages for companies with >1000 filings
    for file_ref in data["filings"].get("files", []):
        page_url = f"https://data.sec.gov/submissions/{file_ref['name']}"
        page_data = _sec_get(page_url)
        filings.extend(_parse_recent(page_data))

    return filings


# ---------------------------------------------------------------------------
# Download a single filing
# ---------------------------------------------------------------------------

def download_filing(cik: str, accession_number: str, primary_document: str, save_dir: str) -> str:
    """Download a filing's primary document to save_dir. Returns the filepath.

    The accession number format is like '0000320193-24-000081'.
    The URL path needs it without dashes: '000032019324000081'.
    The CIK in the URL has no leading zeros.
    """
    if not primary_document:
        raise ValueError(f"No primary document for accession {accession_number}")

    accession_no_dashes = accession_number.replace("-", "")
    cik_no_pad = str(int(cik))  # Remove leading zeros

    url = config.ARCHIVES_URL_TEMPLATE.format(
        cik=cik_no_pad,
        accession_no_dashes=accession_no_dashes,
        filename=primary_document,
    )

    os.makedirs(save_dir, exist_ok=True)
    filepath = os.path.join(save_dir, primary_document)

    resp = _sec_get(url, stream=True)
    with open(filepath, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

    return filepath
