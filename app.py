"""Flask application — routes, background download worker, SSE progress."""

import json
import os
import time
import uuid
import threading

from flask import Flask, Response, jsonify, render_template, request

import config
import edgar_client
import rag_engine

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
    return render_template("index.html", logo_dev_token=config.LOGO_DEV_TOKEN)


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
# Query page routes
# ---------------------------------------------------------------------------

@app.route("/query")
def query_page():
    return render_template("query.html", gemini_configured=bool(config.GEMINI_API_KEY), logo_dev_token=config.LOGO_DEV_TOKEN)


@app.route("/api/gemini-setup", methods=["POST"])
def api_gemini_setup():
    data = request.get_json(silent=True) or {}
    api_key = data.get("apiKey", "").strip()
    if not api_key:
        return jsonify({"error": "Please provide a Gemini API key."}), 400
    config.save_gemini_api_key(api_key)
    config.GEMINI_API_KEY = api_key
    return jsonify({"ok": True})


@app.route("/api/logo-setup", methods=["POST"])
def api_logo_setup():
    data = request.get_json(silent=True) or {}
    token = data.get("token", "").strip()
    if not token:
        return jsonify({"error": "Please provide a Logo.dev token."}), 400
    config.save_logo_dev_token(token)
    config.LOGO_DEV_TOKEN = token
    return jsonify({"ok": True})


@app.route("/api/filings")
def api_filings():
    """Scan the filings directory and return a structured listing."""
    filings_dir = config.FILINGS_DIR
    result = {}

    if not os.path.isdir(filings_dir):
        return jsonify(result)

    for ticker in sorted(os.listdir(filings_dir)):
        ticker_path = os.path.join(filings_dir, ticker)
        if not os.path.isdir(ticker_path):
            continue

        indexed_files = rag_engine.get_indexed_files(ticker)
        result[ticker] = {}

        for form_type in sorted(os.listdir(ticker_path)):
            form_path = os.path.join(ticker_path, form_type)
            if not os.path.isdir(form_path):
                continue

            filings_list = []
            for date_acc in sorted(os.listdir(form_path), reverse=True):
                date_acc_path = os.path.join(form_path, date_acc)
                if not os.path.isdir(date_acc_path):
                    continue

                date = date_acc.split("_")[0] if "_" in date_acc else date_acc

                # Find the primary document file
                files = [f for f in os.listdir(date_acc_path) if os.path.isfile(os.path.join(date_acc_path, f))]
                for file_name in files:
                    file_path = os.path.join(date_acc_path, file_name)
                    filings_list.append({
                        "date": date,
                        "path": file_path,
                        "filename": file_name,
                        "indexed": file_path in indexed_files,
                    })

            if filings_list:
                result[ticker][form_type] = filings_list

    return jsonify(result)


@app.route("/api/index", methods=["POST"])
def api_index():
    """Index selected filings into ChromaDB. Streams progress via SSE."""
    data = request.get_json(silent=True) or {}
    ticker = data.get("ticker", "").strip().upper()
    file_paths = data.get("filings", [])

    if not ticker or not file_paths:
        return jsonify({"error": "ticker and filings are required."}), 400

    if not config.GEMINI_API_KEY:
        return jsonify({"error": "Gemini API key not configured."}), 400

    # Validate file paths exist
    valid_paths = [p for p in file_paths if os.path.isfile(p)]
    if not valid_paths:
        return jsonify({"error": "No valid file paths provided."}), 400

    progress_events = []
    lock = threading.Lock()

    def progress_callback(current, total, message):
        with lock:
            progress_events.append({
                "current": current,
                "total": total,
                "message": message,
            })

    def generate():
        # Run indexing in this thread (SSE generator)
        try:
            stats = rag_engine.index_filings(valid_paths, ticker, progress_callback=progress_callback)
            yield f"data: {json.dumps({'status': 'done', 'stats': stats})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'status': 'error', 'error': str(e)})}\n\n"

    # For SSE, we need to run indexing in a background thread and yield progress
    index_result = {"stats": None, "error": None, "done": False}

    def run_index():
        try:
            stats = rag_engine.index_filings(valid_paths, ticker, progress_callback=progress_callback)
            index_result["stats"] = stats
        except Exception as e:
            index_result["error"] = str(e)
        finally:
            index_result["done"] = True

    thread = threading.Thread(target=run_index, daemon=True)
    thread.start()

    def generate_sse():
        sent = 0
        while not index_result["done"]:
            with lock:
                new_events = progress_events[sent:]
                sent += len(new_events)
            for evt in new_events:
                yield f"data: {json.dumps({'status': 'progress', **evt})}\n\n"
            time.sleep(0.3)

        # Send any remaining events
        with lock:
            new_events = progress_events[sent:]
        for evt in new_events:
            yield f"data: {json.dumps({'status': 'progress', **evt})}\n\n"

        if index_result["error"]:
            yield f"data: {json.dumps({'status': 'error', 'error': index_result['error']})}\n\n"
        else:
            yield f"data: {json.dumps({'status': 'done', 'stats': index_result['stats']})}\n\n"

    return Response(
        generate_sse(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/query", methods=["POST"])
def api_query():
    """Query indexed filings using RAG. Streams Gemini response via SSE."""
    data = request.get_json(silent=True) or {}
    ticker = data.get("ticker", "").strip().upper()
    question = data.get("question", "").strip()
    model = data.get("model", "").strip() or config.GEMINI_MODEL
    date_from = data.get("dateFrom", "").strip() or None
    date_to = data.get("dateTo", "").strip() or None
    filing_types = data.get("filingTypes") or None  # list or None

    if not ticker or not question:
        return jsonify({"error": "ticker and question are required."}), 400

    if not config.GEMINI_API_KEY:
        return jsonify({"error": "Gemini API key not configured."}), 400

    def generate():
        try:
            for chunk in rag_engine.query(
                question, ticker, model=model,
                date_from=date_from, date_to=date_to, filing_types=filing_types,
            ):
                if isinstance(chunk, dict) and "_sources" in chunk:
                    yield f"data: {json.dumps({'status': 'sources', 'sources': chunk['_sources']})}\n\n"
                elif isinstance(chunk, dict) and "_thinking" in chunk:
                    yield f"data: {json.dumps({'status': 'thinking', 'text': chunk['_thinking']})}\n\n"
                else:
                    yield f"data: {json.dumps({'status': 'streaming', 'text': chunk})}\n\n"
            yield f"data: {json.dumps({'status': 'done'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'status': 'error', 'error': str(e)})}\n\n"

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
