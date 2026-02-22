"""Unit tests for edgar_client and Flask routes. All HTTP calls are mocked."""

import json
import os
import time
import threading
from unittest.mock import patch, MagicMock

import pytest

import config
import edgar_client
from app import app, jobs, _download_worker


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MOCK_TICKERS_RESPONSE = {
    "0": {"cik_str": "320193", "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": "789019", "ticker": "MSFT", "title": "Microsoft Corporation"},
    "2": {"cik_str": "1652044", "ticker": "GOOG", "title": "Alphabet Inc."},
}

MOCK_SUBMISSIONS_RESPONSE = {
    "cik": "320193",
    "entityType": "operating",
    "name": "Apple Inc.",
    "tickers": ["AAPL"],
    "filings": {
        "recent": {
            "accessionNumber": [
                "0000320193-24-000081",
                "0000320193-24-000050",
                "0000320193-23-000120",
                "0000320193-23-000090",
                "0000320193-23-000060",
            ],
            "filingDate": [
                "2024-08-02",
                "2024-05-03",
                "2023-11-03",
                "2023-08-04",
                "2023-05-05",
            ],
            "reportDate": [
                "2024-06-29",
                "2024-03-30",
                "2023-09-30",
                "2023-07-01",
                "2023-04-01",
            ],
            "form": ["10-Q", "10-Q", "10-K", "10-Q", "DEF 14A"],
            "primaryDocument": [
                "aapl-20240629.htm",
                "aapl-20240330.htm",
                "aapl-20230930.htm",
                "aapl-20230701.htm",
                "def14a2023.htm",
            ],
            "primaryDocDescription": [
                "Quarterly Report",
                "Quarterly Report",
                "Annual Report",
                "Quarterly Report",
                "Proxy Statement",
            ],
        },
        "files": [],
    },
}

MOCK_PAGINATION_PAGE = {
    "accessionNumber": ["0000320193-22-000100"],
    "filingDate": ["2022-11-04"],
    "reportDate": ["2022-09-24"],
    "form": ["10-K"],
    "primaryDocument": ["aapl-20220924.htm"],
    "primaryDocDescription": ["Annual Report"],
}


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def reset_cache():
    """Clear the tickers cache and ensure User-Agent is set for tests."""
    edgar_client.clear_tickers_cache()
    original_ua = config.SEC_USER_AGENT
    config.SEC_USER_AGENT = "TestApp test@example.com"
    yield
    config.SEC_USER_AGENT = original_ua


@pytest.fixture
def tmp_filings_dir(tmp_path):
    """Override FILINGS_DIR to use a temp directory."""
    original = config.FILINGS_DIR
    config.FILINGS_DIR = str(tmp_path / "filings")
    yield config.FILINGS_DIR
    config.FILINGS_DIR = original


# ---------------------------------------------------------------------------
# RateLimiter tests
# ---------------------------------------------------------------------------

class TestRateLimiter:
    def test_allows_within_limit(self):
        """Requests within budget should proceed without sleeping."""
        limiter = edgar_client.RateLimiter(max_requests=5, window=1.0)
        with patch("time.sleep") as mock_sleep:
            for _ in range(5):
                limiter.wait()
            mock_sleep.assert_not_called()

    def test_throttles_when_over_limit(self):
        """Should sleep when burst exceeds the limit."""
        limiter = edgar_client.RateLimiter(max_requests=2, window=1.0)
        with patch("time.sleep") as mock_sleep:
            limiter.wait()
            limiter.wait()
            limiter.wait()  # This third call should trigger a sleep
            assert mock_sleep.called


# ---------------------------------------------------------------------------
# CIK lookup tests
# ---------------------------------------------------------------------------

class TestLookupCik:
    @patch("edgar_client._sec_get")
    def test_found(self, mock_get):
        mock_get.return_value = MOCK_TICKERS_RESPONSE
        result = edgar_client.lookup_cik("AAPL")
        assert result is not None
        assert result["cik"] == "0000320193"
        assert result["name"] == "Apple Inc."

    @patch("edgar_client._sec_get")
    def test_not_found(self, mock_get):
        mock_get.return_value = MOCK_TICKERS_RESPONSE
        result = edgar_client.lookup_cik("ZZZZZ")
        assert result is None

    @patch("edgar_client._sec_get")
    def test_case_insensitive(self, mock_get):
        mock_get.return_value = MOCK_TICKERS_RESPONSE
        result = edgar_client.lookup_cik("aapl")
        assert result is not None
        assert result["cik"] == "0000320193"

    @patch("edgar_client._sec_get")
    def test_caching(self, mock_get):
        mock_get.return_value = MOCK_TICKERS_RESPONSE
        edgar_client.lookup_cik("AAPL")
        edgar_client.lookup_cik("MSFT")
        # Should only fetch once due to caching
        mock_get.assert_called_once()


# ---------------------------------------------------------------------------
# Filing metadata tests
# ---------------------------------------------------------------------------

class TestFetchFilings:
    @patch("edgar_client._sec_get")
    def test_basic(self, mock_get):
        mock_get.return_value = MOCK_SUBMISSIONS_RESPONSE
        filings = edgar_client.fetch_all_filings("0000320193")
        assert len(filings) == 5
        assert filings[0]["form"] == "10-Q"
        assert filings[0]["accessionNumber"] == "0000320193-24-000081"
        assert filings[2]["form"] == "10-K"

    @patch("edgar_client._sec_get")
    def test_pagination(self, mock_get):
        submissions_with_pages = dict(MOCK_SUBMISSIONS_RESPONSE)
        submissions_with_pages["filings"] = dict(MOCK_SUBMISSIONS_RESPONSE["filings"])
        submissions_with_pages["filings"]["files"] = [
            {"name": "CIK0000320193-submissions-001.json"}
        ]

        def side_effect(url):
            if "submissions-001" in url:
                return MOCK_PAGINATION_PAGE
            return submissions_with_pages

        mock_get.side_effect = side_effect
        filings = edgar_client.fetch_all_filings("0000320193")
        # 5 from recent + 1 from pagination page
        assert len(filings) == 6
        assert filings[-1]["form"] == "10-K"
        assert filings[-1]["filingDate"] == "2022-11-04"

    def test_parse_recent_parallel_arrays(self):
        recent = {
            "accessionNumber": ["A-001", "A-002"],
            "filingDate": ["2024-01-01", "2024-02-02"],
            "reportDate": ["2023-12-31", "2024-01-31"],
            "form": ["10-K", "10-Q"],
            "primaryDocument": ["doc1.htm", "doc2.htm"],
            "primaryDocDescription": ["Annual", "Quarterly"],
        }
        result = edgar_client._parse_recent(recent)
        assert len(result) == 2
        assert result[0]["accessionNumber"] == "A-001"
        assert result[0]["form"] == "10-K"
        assert result[1]["primaryDocument"] == "doc2.htm"


# ---------------------------------------------------------------------------
# Download filing tests
# ---------------------------------------------------------------------------

class TestDownloadFiling:
    @patch("edgar_client._sec_get")
    def test_writes_file(self, mock_get, tmp_filings_dir):
        mock_response = MagicMock()
        mock_response.iter_content.return_value = [b"<html>Filing content</html>"]
        mock_get.return_value = mock_response

        save_dir = os.path.join(tmp_filings_dir, "AAPL", "10-K", "2024-11-01_0000320193-24-000081")
        filepath = edgar_client.download_filing(
            "0000320193", "0000320193-24-000081", "aapl-20240930.htm", save_dir
        )

        assert os.path.exists(filepath)
        with open(filepath, "rb") as f:
            assert f.read() == b"<html>Filing content</html>"

    @patch("edgar_client._sec_get")
    def test_creates_dirs(self, mock_get, tmp_filings_dir):
        mock_response = MagicMock()
        mock_response.iter_content.return_value = [b"data"]
        mock_get.return_value = mock_response

        save_dir = os.path.join(tmp_filings_dir, "DEEP", "NESTED", "DIR")
        edgar_client.download_filing("0000320193", "0000320193-24-000081", "doc.htm", save_dir)

        assert os.path.isdir(save_dir)

    def test_raises_on_empty_document(self):
        with pytest.raises(ValueError, match="No primary document"):
            edgar_client.download_filing("0000320193", "0000320193-24-000081", "", "/tmp/test")


# ---------------------------------------------------------------------------
# _sec_get retry tests
# ---------------------------------------------------------------------------

class TestSecGet:
    @patch("edgar_client._rate_limiter")
    @patch("requests.get")
    def test_retries_on_5xx(self, mock_get, mock_limiter):
        mock_limiter.wait.return_value = None
        fail_resp = MagicMock()
        fail_resp.status_code = 500
        fail_resp.raise_for_status.side_effect = Exception("Server error")

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"success": True}

        mock_get.side_effect = [fail_resp, ok_resp]
        result = edgar_client._sec_get("https://example.com/test")
        assert result == {"success": True}
        assert mock_get.call_count == 2

    @patch("edgar_client._rate_limiter")
    @patch("requests.get")
    def test_retries_on_429(self, mock_get, mock_limiter):
        mock_limiter.wait.return_value = None
        rate_resp = MagicMock()
        rate_resp.status_code = 429
        rate_resp.raise_for_status.side_effect = Exception("Rate limited")

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"data": "ok"}

        mock_get.side_effect = [rate_resp, ok_resp]
        result = edgar_client._sec_get("https://example.com/test")
        assert result == {"data": "ok"}
        assert mock_get.call_count == 2


# ---------------------------------------------------------------------------
# Flask route tests
# ---------------------------------------------------------------------------

class TestIndexRoute:
    def test_returns_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"html" in resp.data.lower()


class TestLookupRoute:
    @patch("edgar_client.fetch_all_filings")
    @patch("edgar_client.lookup_cik")
    def test_valid_ticker(self, mock_lookup, mock_filings, client):
        mock_lookup.return_value = {"cik": "0000320193", "name": "Apple Inc."}
        mock_filings.return_value = [
            {
                "accessionNumber": "0000320193-24-000081",
                "filingDate": "2024-08-02",
                "reportDate": "2024-06-29",
                "form": "10-Q",
                "primaryDocument": "aapl-20240629.htm",
                "primaryDocDescription": "Quarterly Report",
            }
        ]

        resp = client.post("/api/lookup", json={"ticker": "AAPL"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["company"] == "Apple Inc."
        assert data["cik"] == "0000320193"
        assert "filingTypes" in data
        assert "filings" in data
        assert "dateRange" in data

    @patch("edgar_client.lookup_cik")
    def test_invalid_ticker(self, mock_lookup, client):
        mock_lookup.return_value = None
        resp = client.post("/api/lookup", json={"ticker": "ZZZZZ"})
        assert resp.status_code == 404
        assert "not found" in resp.get_json()["error"].lower()

    def test_missing_ticker(self, client):
        resp = client.post("/api/lookup", json={})
        assert resp.status_code == 400
        assert "required" in resp.get_json()["error"].lower()


class TestDownloadRoute:
    @patch("edgar_client.download_filing")
    def test_starts_job(self, mock_download, client):
        mock_download.return_value = "/tmp/test.htm"
        resp = client.post("/api/download", json={
            "ticker": "AAPL",
            "cik": "0000320193",
            "filings": [
                {
                    "accessionNumber": "0000320193-24-000081",
                    "filingDate": "2024-08-02",
                    "form": "10-Q",
                    "primaryDocument": "aapl-20240629.htm",
                }
            ],
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert "jobId" in data

    @patch("edgar_client.download_filing")
    def test_creates_files(self, mock_download, client, tmp_filings_dir):
        # Make download_filing actually create a file
        def fake_download(cik, accession, primary_doc, save_dir):
            os.makedirs(save_dir, exist_ok=True)
            filepath = os.path.join(save_dir, primary_doc)
            with open(filepath, "w") as f:
                f.write("test content")
            return filepath

        mock_download.side_effect = fake_download

        resp = client.post("/api/download", json={
            "ticker": "TEST",
            "cik": "0000000001",
            "filings": [
                {
                    "accessionNumber": "0000000001-24-000001",
                    "filingDate": "2024-01-01",
                    "form": "10-K",
                    "primaryDocument": "test.htm",
                }
            ],
        })
        job_id = resp.get_json()["jobId"]

        # Wait for background thread to finish
        for _ in range(20):
            job = jobs.get(job_id)
            if job and job["status"] == "done":
                break
            time.sleep(0.1)

        expected_dir = os.path.join(
            tmp_filings_dir, "TEST", "10-K", "2024-01-01_0000000001-24-000001"
        )
        assert os.path.exists(os.path.join(expected_dir, "test.htm"))


class TestProgressRoute:
    def test_unknown_job(self, client):
        resp = client.get("/api/progress/nonexistent-id")
        # Read the SSE data
        data = b""
        for chunk in resp.response:
            data += chunk
        assert b"Job not found" in data

    def test_stream_format(self, client):
        # Create a completed job manually
        test_id = "test-format-job"
        jobs[test_id] = {
            "status": "done",
            "total": 1,
            "completed": 1,
            "current": "",
            "errors": [],
        }

        resp = client.get(f"/api/progress/{test_id}")
        data = b""
        for chunk in resp.response:
            data += chunk

        decoded = data.decode()
        assert decoded.startswith("data: ")
        assert "\n\n" in decoded
        parsed = json.loads(decoded.split("data: ")[1].split("\n\n")[0])
        assert parsed["status"] == "done"
        assert parsed["completed"] == 1

        # Cleanup
        del jobs[test_id]


class TestSetupRoute:
    def test_shows_setup_when_no_user_agent(self, client):
        original = config.SEC_USER_AGENT
        config.SEC_USER_AGENT = ""
        resp = client.get("/")
        config.SEC_USER_AGENT = original
        assert resp.status_code == 200
        assert b"First-Time Setup" in resp.data

    def test_shows_main_page_when_configured(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Filing Downloader" in resp.data
        assert b"First-Time Setup" not in resp.data

    def test_save_user_agent(self, client, tmp_path):
        original_env = config._ENV_FILE
        config._ENV_FILE = str(tmp_path / ".env")
        resp = client.post("/api/setup", json={"userAgent": "Test User test@test.com"})
        assert resp.status_code == 200
        assert config.SEC_USER_AGENT == "Test User test@test.com"
        config._ENV_FILE = original_env

    def test_save_empty_user_agent(self, client):
        resp = client.post("/api/setup", json={"userAgent": ""})
        assert resp.status_code == 400


class TestDirectorySanitization:
    @patch("edgar_client.download_filing")
    def test_slash_in_form_type(self, mock_download, client, tmp_filings_dir):
        """Filing type 10-K/A should create directory 10-K_A, not a nested path."""
        created_dirs = []

        def fake_download(cik, accession, primary_doc, save_dir):
            created_dirs.append(save_dir)
            os.makedirs(save_dir, exist_ok=True)
            filepath = os.path.join(save_dir, primary_doc)
            with open(filepath, "w") as f:
                f.write("test")
            return filepath

        mock_download.side_effect = fake_download

        resp = client.post("/api/download", json={
            "ticker": "TEST",
            "cik": "0000000001",
            "filings": [
                {
                    "accessionNumber": "0000000001-24-000001",
                    "filingDate": "2024-01-01",
                    "form": "10-K/A",
                    "primaryDocument": "test.htm",
                }
            ],
        })
        job_id = resp.get_json()["jobId"]

        # Wait for background thread
        for _ in range(20):
            job = jobs.get(job_id)
            if job and job["status"] == "done":
                break
            time.sleep(0.1)

        # The directory should contain 10-K_A, not 10-K/A
        assert len(created_dirs) == 1
        assert "10-K_A" in created_dirs[0]
        assert "10-K/A" not in created_dirs[0].replace("\\", "/").split("filings/")[-1] if "filings/" in created_dirs[0].replace("\\", "/") else True
