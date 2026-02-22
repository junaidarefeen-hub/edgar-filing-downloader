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


# ---------------------------------------------------------------------------
# Gemini LLM configuration
# ---------------------------------------------------------------------------

def _load_env_var(key):
    """Load a variable from .env file, or return None if not configured."""
    if os.path.exists(_ENV_FILE):
        with open(_ENV_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _save_env_var(key, value):
    """Save a key=value pair to .env file."""
    lines = []
    found = False
    if os.path.exists(_ENV_FILE):
        with open(_ENV_FILE, "r") as f:
            for line in f:
                if line.strip().startswith(f"{key}="):
                    lines.append(f'{key}="{value}"\n')
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append(f'{key}="{value}"\n')
    with open(_ENV_FILE, "w") as f:
        f.writelines(lines)


def save_gemini_api_key(value):
    """Save the GEMINI_API_KEY to .env file."""
    _save_env_var("GEMINI_API_KEY", value)


GEMINI_API_KEY = _load_env_var("GEMINI_API_KEY") or ""

# ---------------------------------------------------------------------------
# Logo.dev configuration (free company logo API)
# ---------------------------------------------------------------------------

def save_logo_dev_token(value):
    """Save the LOGO_DEV_TOKEN to .env file."""
    _save_env_var("LOGO_DEV_TOKEN", value)


LOGO_DEV_TOKEN = _load_env_var("LOGO_DEV_TOKEN") or ""
GEMINI_MODEL = "gemini-3.1-pro-preview"
GEMINI_EMBEDDING_MODEL = "gemini-embedding-001"

# Vector store directory for ChromaDB persistence
VECTOR_STORE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vector_store")

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
