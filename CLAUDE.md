# SEC EDGAR Filing Downloader

Python/Flask web app that downloads SEC filings (10-K, 10-Q, proxy, 8-K, etc.) for any public company by ticker symbol.

## Tech Stack

- Python 3, Flask, requests (backend)
- Vanilla HTML/CSS/JS (frontend)
- Server-Sent Events for real-time progress

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
| `config.py` | Constants: SEC URLs, User-Agent, default filing types, paths |
| `test_app.py` | Unit tests (mocked HTTP — no real SEC calls) |
| `templates/index.html` | Single-page UI |
| `static/app.js` | Frontend logic: API calls, filtering, progress tracking |
| `static/style.css` | Styles |
| `filings/` | Downloaded filings (created at runtime) |

## API Routes

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Serve the UI |
| `/api/lookup` | POST | Ticker → company info + filing metadata |
| `/api/download` | POST | Start background download of selected filings |
| `/api/progress/<job_id>` | GET | SSE stream for download progress |

## Key Conventions

- SEC API requires a `User-Agent` header — configured in `config.py` (update before production use)
- Rate limit: 8 req/sec (SEC allows 10, we use 8 for safety margin)
- Filing types with `/` (e.g., `10-K/A`) are sanitized to `_` in directory names → `10-K_A`
- Filings saved to `./filings/{TICKER}/{FORM_TYPE}/{DATE}_{ACCESSION}/`
- All tests mock HTTP calls — never hit the real SEC API during testing
