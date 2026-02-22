# SEC EDGAR Filing Downloader

A web application that downloads SEC filings (10-K, 10-Q, proxy statements, 8-K, and more) for any publicly traded company by ticker symbol.

## Features

- Look up any company by stock ticker
- Browse all available SEC filings with type and date filtering
- Select which filing types to download (10-K, 10-Q, DEF 14A, 8-K, etc.)
- Choose a custom date range
- Download filings to your local machine with real-time progress tracking
- Automatic rate limiting to comply with SEC EDGAR API rules

## Quick Start

### Windows

Double-click `setup.bat`, or run in a terminal:

```cmd
setup.bat
```

### macOS / Linux

```bash
chmod +x setup.sh
./setup.sh
```

### Manual Setup

```bash
pip install -r requirements.txt
python app.py
```

Then open **http://localhost:5000** in your browser.

On first launch, the app will ask for your name and email. The SEC requires this to identify API users — it's stored locally in a `.env` file and never shared.

## How It Works

1. Enter a ticker symbol (e.g., `AAPL`)
2. The app fetches all available filings from the SEC EDGAR API
3. Filter by filing type and date range
4. Click **Download Selected** to save filings to the `filings/` folder
5. Watch the progress bar as files download

Downloaded filings are organized as:
```
filings/
  AAPL/
    10-K/
      2024-11-01_0000320193-24-000123/
        aapl-20240928.htm
    10-Q/
      ...
```

## Running Tests

```bash
pytest test_app.py -v
```

All tests use mocked HTTP calls — no real SEC API requests are made during testing.

## Requirements

- Python 3.10+
- Internet connection (to reach SEC EDGAR API)

## Project Structure

| File | Description |
|---|---|
| `app.py` | Flask web server, routes, background download worker |
| `edgar_client.py` | SEC EDGAR API client (CIK lookup, filing fetch, download) |
| `config.py` | Configuration constants and .env loading |
| `test_app.py` | Unit tests |
| `templates/` | HTML templates |
| `static/` | CSS and JavaScript |
| `filings/` | Downloaded filings (created at runtime, git-ignored) |

## Notes

- The SEC EDGAR API is free and requires no API key
- Rate limited to 8 requests/second (SEC allows 10; we use 8 for safety)
- Filing types containing `/` (like `10-K/A`) are saved with `_` in directory names
- The `.env` file stores your User-Agent identity and is git-ignored
