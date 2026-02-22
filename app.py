"""Flask application — routes, background download worker, SSE progress."""

import json
import os
import time
import uuid
import threading

from flask import Flask, Response, jsonify, render_template, request

import config
import edgar_client

app = Flask(__name__)

# In-memory job tracking for download progress
jobs: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    if not config.SEC_USER_AGENT:
        return render_template("setup.html")
    return render_template("index.html")


@app.route("/api/setup", methods=["POST"])
def api_setup():
    data = request.get_json(silent=True) or {}
    user_agent = data.get("userAgent", "").strip()
    if not user_agent:
        return jsonify({"error": "Please provide your name and email."}), 400
    config.save_user_agent(user_agent)
    config.SEC_USER_AGENT = user_agent
    return jsonify({"ok": True})


@app.route("/api/lookup", methods=["POST"])
def api_lookup():
    data = request.get_json(silent=True) or {}
    ticker = data.get("ticker", "").strip()

    if not ticker:
        return jsonify({"error": "Ticker is required."}), 400

    result = edgar_client.lookup_cik(ticker)
    if result is None:
        return jsonify({"error": f"Ticker '{ticker.upper()}' not found. Check the symbol and try again."}), 404

    cik = result["cik"]
    company_name = result["name"]

    try:
        filings = edgar_client.fetch_all_filings(cik)
    except Exception as e:
        return jsonify({"error": f"Failed to fetch filings: {e}"}), 502

    # Extract unique filing types and date range
    filing_types = sorted(set(f["form"] for f in filings))
    years = [int(f["filingDate"][:4]) for f in filings if f["filingDate"]]
    date_range = {"min": min(years) if years else 2000, "max": max(years) if years else 2025}

    return jsonify({
        "company": company_name,
        "cik": cik,
        "filingTypes": filing_types,
        "filings": filings,
        "dateRange": date_range,
    })


@app.route("/api/download", methods=["POST"])
def api_download():
    data = request.get_json(silent=True) or {}
    ticker = data.get("ticker", "").strip().upper()
    cik = data.get("cik", "")
    filings_to_download = data.get("filings", [])

    if not ticker or not cik or not filings_to_download:
        return jsonify({"error": "ticker, cik, and filings are required."}), 400

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "running",
        "total": len(filings_to_download),
        "completed": 0,
        "current": "",
        "errors": [],
    }

    thread = threading.Thread(
        target=_download_worker,
        args=(job_id, ticker, cik, filings_to_download),
        daemon=True,
    )
    thread.start()

    return jsonify({"jobId": job_id})


@app.route("/api/progress/<job_id>")
def api_progress(job_id):
    def generate():
        while True:
            job = jobs.get(job_id)
            if not job:
                yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
                return
            yield f"data: {json.dumps(job)}\n\n"
            if job["status"] in ("done", "error"):
                return
            time.sleep(0.5)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Background download worker
# ---------------------------------------------------------------------------

def _download_worker(job_id: str, ticker: str, cik: str, filings_to_download: list[dict]):
    """Download filings in a background thread, updating job state as we go."""
    total = len(filings_to_download)
    errors = []

    for i, filing in enumerate(filings_to_download):
        form_type = filing.get("form", "UNKNOWN")
        filing_date = filing.get("filingDate", "")
        accession = filing.get("accessionNumber", "")
        primary_doc = filing.get("primaryDocument", "")

        # Sanitize form type for filesystem: 10-K/A → 10-K_A
        safe_form = form_type.replace("/", "_")
        accession_clean = accession

        save_dir = os.path.join(
            config.FILINGS_DIR,
            ticker,
            safe_form,
            f"{filing_date}_{accession_clean}",
        )

        # Update current status
        desc = f"{form_type} {filing_date}"
        jobs[job_id] = {
            "status": "running",
            "total": total,
            "completed": i,
            "current": desc,
            "errors": list(errors),
        }

        try:
            edgar_client.download_filing(cik, accession, primary_doc, save_dir)
        except Exception as e:
            errors.append({"filing": desc, "error": str(e)})

    # Mark done
    jobs[job_id] = {
        "status": "done",
        "total": total,
        "completed": total,
        "current": "",
        "errors": list(errors),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    os.makedirs(config.FILINGS_DIR, exist_ok=True)
    app.run(debug=True, threaded=True, port=5000)
