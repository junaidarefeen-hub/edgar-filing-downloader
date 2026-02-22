# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Quick Start

```bash
pip install -r requirements.txt
python app.py
# Open http://localhost:5000
```

## Run Tests

```bash
pytest test_app.py -v
```

## Project Structure

| File | Role |
|---|---|
| `app.py` | Flask routes, background download worker, SSE progress |
| `edgar_client.py` | SEC EDGAR API: CIK lookup, filing metadata, file download, rate limiter |
| `config.py` | Constants: SEC URLs, User-Agent (.env), default filing types, paths |
| `test_app.py` | Unit tests (mocked HTTP — no real SEC calls) |
| `templates/index.html` | Main UI; `templates/setup.html` for first-time User-Agent config |
| `static/app.js` | Frontend logic: API calls, filtering, progress tracking |
| `static/style.css` | Styles |

## Architecture

- **Download flow**: `/api/lookup` resolves ticker→CIK, fetches filing metadata. `/api/download` spawns a background `threading.Thread` that calls `edgar_client.download_filing` per filing. `/api/progress/<job_id>` streams status via SSE.
- **Job tracking**: In-memory `jobs` dict in `app.py` — not persistent across restarts.
- **Rate limiter**: Sliding-window (`edgar_client.RateLimiter`), thread-safe, shared across all requests. 8 req/sec (SEC allows 10).
- **Pagination**: `fetch_all_filings` handles companies with >1000 filings by following `filings.files[]` references.
- **First-run setup**: If `SEC_USER_AGENT` is empty (no `.env`), the index route serves `setup.html` which POSTs to `/api/setup` to persist the User-Agent.

## Key Conventions

- SEC API requires a `User-Agent` header — stored in `.env` file (loaded by `config.py`)
- Filing types with `/` (e.g., `10-K/A`) are sanitized to `_` in directory names → `10-K_A`
- Filings saved to `./filings/{TICKER}/{FORM_TYPE}/{DATE}_{ACCESSION}/`
- All tests mock HTTP calls — never hit the real SEC API during testing
- `_sec_get` retries once on 429 (5s delay) and 5xx (2s delay)
- CIK→ticker mapping is cached in-memory for 24h; use `clear_tickers_cache()` in tests
