import os

# MANDATORY: SEC requires a User-Agent header identifying the requester.
# This is loaded from .env file at startup. If not set, the app will prompt on first run.
_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def _load_user_agent():
    """Load SEC_USER_AGENT from .env file, or return None if not configured."""
    if os.path.exists(_ENV_FILE):
        with open(_ENV_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("SEC_USER_AGENT="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def save_user_agent(value):
    """Save the SEC_USER_AGENT to .env file."""
    lines = []
    found = False
    if os.path.exists(_ENV_FILE):
        with open(_ENV_FILE, "r") as f:
            for line in f:
                if line.strip().startswith("SEC_USER_AGENT="):
                    lines.append(f'SEC_USER_AGENT="{value}"\n')
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append(f'SEC_USER_AGENT="{value}"\n')
    with open(_ENV_FILE, "w") as f:
        f.writelines(lines)


SEC_USER_AGENT = _load_user_agent() or ""

# SEC API endpoints
COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVES_URL_TEMPLATE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/{filename}"

# Rate limiting: SEC allows max 10 req/sec. We use 8 for safety margin.
RATE_LIMIT_REQUESTS = 8
RATE_LIMIT_WINDOW = 1.0  # seconds

# Default filing types shown as pre-checked in the UI
DEFAULT_FILING_TYPES = [
    "10-K", "10-Q", "8-K", "DEF 14A", "20-F", "S-1",
    "10-K/A", "10-Q/A", "8-K/A", "S-3", "S-4",
    "SC 13D", "SC 13G", "6-K", "DEFA14A",
]

# Where downloaded filings are saved
FILINGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "filings")
