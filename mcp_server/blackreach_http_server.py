"""
Blackreach HTTP Server — Async Job Queue Architecture

Runs Blackreach as a persistent Flask server with full network access.
Start this ONCE from your terminal, then Claude Code's MCP can call it over localhost.

Usage:
  python /mnt/AI_Projects/Blackreach/mcp_server/blackreach_http_server.py

Runs on: http://localhost:7432

Architecture:
  - POST /browse, /search, /scrape-jobs  → returns job_id immediately (non-blocking)
  - GET  /jobs/{job_id}                  → poll for result
  - GET  /jobs                           → list all jobs
  - GET  /health                         → server status

Why async jobs?
  Claude Code MCP tool calls time out after ~30 seconds.
  Blackreach can take 2-5 minutes. The MCP tool submits the job and polls for completion.
"""
import sys
import threading
import uuid
import time
from pathlib import Path
from enum import Enum

sys.path.insert(0, '/mnt/AI_Projects/Blackreach')

from flask import Flask, request, jsonify
from blackreach.api import BlackreachAPI, ApiConfig

app = Flask(__name__)

DOWNLOAD_DIR = Path("/mnt/AI_Projects/Blackreach/downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


# ─── Job Store ────────────────────────────────────────────────────────────────

class JobStatus(str, Enum):
    PENDING  = "pending"
    RUNNING  = "running"
    DONE     = "done"
    FAILED   = "failed"


_jobs: dict[str, dict] = {}  # job_id → job dict
_queue: list[str] = []       # ordered list of pending job_ids
_jobs_lock = threading.Lock()
_worker_thread: threading.Thread | None = None


def _make_api(max_steps: int = 60) -> BlackreachAPI:
    return BlackreachAPI(ApiConfig(
        download_dir=DOWNLOAD_DIR,
        headless=True,
        max_steps=max_steps,
        verbose=True,
    ))


def _worker_loop():
    """Single background thread that runs jobs one at a time."""
    while True:
        job_id = None

        with _jobs_lock:
            if _queue:
                job_id = _queue.pop(0)

        if job_id is None:
            time.sleep(0.5)
            continue

        with _jobs_lock:
            job = _jobs.get(job_id)
            if job:
                job["status"] = JobStatus.RUNNING
                job["started_at"] = time.time()

        try:
            goal      = job["goal"]
            start_url = job.get("start_url")
            max_steps = job.get("max_steps", 60)

            api    = _make_api(max_steps=max_steps)
            result = api.browse(goal=goal, start_url=start_url)

            with _jobs_lock:
                _jobs[job_id].update({
                    "status":        JobStatus.DONE,
                    "finished_at":   time.time(),
                    "success":       result.success,
                    "pages_visited": result.pages_visited,
                    "steps_taken":   result.steps_taken,
                    "downloads":     result.downloads,
                    "errors":        result.errors,
                    "session_id":    result.session_id,
                    "result":        result.result,
                })

        except Exception as e:
            with _jobs_lock:
                _jobs[job_id].update({
                    "status":      JobStatus.FAILED,
                    "finished_at": time.time(),
                    "errors":      [str(e)],
                })


def _start_worker():
    global _worker_thread
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_thread = threading.Thread(target=_worker_loop, daemon=True, name="blackreach-worker")
        _worker_thread.start()


def _submit_job(goal: str, start_url: str | None = None, max_steps: int = 60) -> str:
    job_id = str(uuid.uuid4())[:8]
    with _jobs_lock:
        _jobs[job_id] = {
            "job_id":     job_id,
            "status":     JobStatus.PENDING,
            "goal":       goal,
            "start_url":  start_url,
            "max_steps":  max_steps,
            "created_at": time.time(),
        }
        _queue.append(job_id)
    _start_worker()
    return job_id


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    with _jobs_lock:
        running = sum(1 for j in _jobs.values() if j["status"] == JobStatus.RUNNING)
        pending = sum(1 for j in _jobs.values() if j["status"] == JobStatus.PENDING)
    return jsonify({"status": "ok", "service": "blackreach", "running": running, "pending": pending})


@app.route("/jobs", methods=["GET"])
def list_jobs():
    with _jobs_lock:
        return jsonify(list(_jobs.values()))


@app.route("/jobs/<job_id>", methods=["GET"])
def get_job(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return jsonify({"error": "job not found"}), 404
    return jsonify(job)


@app.route("/browse", methods=["POST"])
def browse():
    data      = request.get_json(force=True)
    goal      = data.get("goal", "")
    start_url = data.get("start_url") or None
    max_steps = data.get("max_steps", 60)

    if not goal:
        return jsonify({"error": "goal is required"}), 400

    job_id = _submit_job(goal=goal, start_url=start_url, max_steps=max_steps)
    return jsonify({"job_id": job_id, "status": "pending"}), 202


@app.route("/search", methods=["POST"])
def search():
    data       = request.get_json(force=True)
    query      = data.get("query", "")
    num_results = data.get("num_results", 10)

    goal = (
        f"Search Google for: {query}\n"
        f"Extract the top {num_results} results.\n"
        f"For each result return: title, URL, and a brief description.\n"
        f"Format as a numbered list."
    )

    job_id = _submit_job(goal=goal)
    return jsonify({"job_id": job_id, "status": "pending", "query": query}), 202


@app.route("/scrape-jobs", methods=["POST"])
def scrape_jobs():
    role    = request.args.get("role", "AI engineer")
    site    = request.args.get("site", "wellfound.com")
    filters = request.args.get("filters", "remote")

    goal = (
        f"Go to {site} and search for '{role}' jobs.\n"
        f"Filter by: {filters}.\n"
        f"For each job listing extract: job title, company name, salary range if shown, "
        f"location/remote status, and the direct URL to the job posting.\n"
        f"Return at least 15 results formatted as a numbered list.\n"
        f"If one page doesn't work, try another URL or search approach on the same site."
    )

    job_id = _submit_job(goal=goal, start_url=f"https://{site}", max_steps=80)
    return jsonify({"job_id": job_id, "status": "pending", "role": role, "site": site}), 202


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Blackreach HTTP Server (Async Job Queue)")
    print("Listening on http://127.0.0.1:7432")
    print("Keep this running while using Claude Code.")
    print("Stop with Ctrl+C")
    print("=" * 60)
    _start_worker()
    app.run(host="127.0.0.1", port=7432, threaded=True, debug=False)
