#!/usr/bin/env python3
"""
Huginn CLI — command-line interface for the Huginn web intelligence API.

Usage:
  huginn probe <url>                          Scrape a single URL
  huginn sweep <url> [--depth N] [--max-pages N]  Start a crawl job
  huginn chart <url>                          Generate site map
  huginn distill <url> <prompt>               Extract structured data
  huginn seek <query>                         Search the web
  huginn flock <url> [url ...]               Batch scrape multiple URLs
  huginn jobs                                 List recent jobs
  huginn jobs <job_id>                        Get job status
  huginn schedule                              List schedules
  huginn schedule create <job_type> <payload>  Create a schedule
  huginn schedule delete <schedule_id>          Delete a schedule

Environment:
  HUGINN_BASE_URL  API base URL (default: http://localhost:7432)
  HUGINN_API_KEY  API key for authentication
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

BASE_URL = os.getenv("HUGINN_BASE_URL", "http://localhost:7432").rstrip("/")
API_KEY = os.getenv("HUGINN_API_KEY", "")
TIMEOUT = float(os.getenv("HUGINN_TIMEOUT", "60"))


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if API_KEY:
        h["Authorization"] = f"Bearer {API_KEY}"
    return h


def _post(path: str, data: dict) -> dict:
    if not _HAS_HTTPX:
        req = urllib.request.Request(
            f"{BASE_URL}{path}",
            data=json.dumps(data).encode(),
            headers=_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.post(f"{BASE_URL}{path}", json=data, headers=_headers())
        r.raise_for_status()
        return r.json()


def _get(path: str) -> dict:
    if not _HAS_HTTPX:
        req = urllib.request.Request(
            f"{BASE_URL}{path}",
            headers=_headers(),
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.get(f"{BASE_URL}{path}", headers=_headers())
        r.raise_for_status()
        return r.json()


def _delete(path: str) -> dict:
    if not _HAS_HTTPX:
        req = urllib.request.Request(
            f"{BASE_URL}{path}",
            headers=_headers(),
            method="DELETE",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.delete(f"{BASE_URL}{path}", headers=_headers())
        r.raise_for_status()
        return r.json()


def _poll_job(job_id: str, timeout: int = 300) -> dict:
    start = time.time()
    while time.time() - start < timeout:
        result = _get(f"/v1/sweep/{job_id}")
        status = result.get("status", result.get("data", {}).get("status", "unknown"))
        print(f"  [{status}] ", end="", flush=True)
        if status in ("completed", "success"):
            print()
            return result
        if status in ("failed", "cancelled"):
            print()
            return result
        time.sleep(2)
    raise TimeoutError(f"Job {job_id} timed out after {timeout}s")


def cmd_probe(args: argparse.Namespace):
    data = {
        "url": args.url,
        "formats": args.formats or ["markdown"],
        "only_main_content": not args.no_filter,
        "include_tags": args.include_tags.split(",") if args.include_tags else None,
        "exclude_tags": args.exclude_tags.split(",") if args.exclude_tags else None,
    }
    result = _post("/v1/probe", data)
    _print_result(result, args.formats or ["markdown"])


def cmd_sweep(args: argparse.Namespace):
    data = {
        "url": args.url,
        "max_depth": args.depth or 2,
        "max_pages": args.max_pages or 50,
        "formats": ["markdown"],
        "concurrency": args.concurrency or 3,
    }
    if args.start_urls:
        data["start_urls"] = args.start_urls.split(",")
    print(f"Starting sweep: {args.url} (depth={data['max_depth']}, max_pages={data['max_pages']})")
    result = _post("/v1/sweep", data)
    job_id = result.get("job_id")
    if not job_id:
        print(json.dumps(result, indent=2))
        return
    print(f"Job ID: {job_id}")
    if args.wait:
        print("Polling... ", end="", flush=True)
        _poll_job(job_id, timeout=args.timeout or 300)
        final = _get(f"/v1/sweep/{job_id}")
        print(f"Completed! {final.get('data', {}).get('crawl_size', '?')} pages scraped")
        _print_result(final, ["markdown"])


def cmd_chart(args: argparse.Namespace):
    data = {"url": args.url, "search": args.search}
    print(f"Charting: {args.url}")
    result = _post("/v1/chart", data)
    job_id = result.get("job_id")
    if not job_id:
        print(json.dumps(result, indent=2))
        return
    print(f"Job ID: {job_id}")
    if args.wait:
        print("Polling... ", end="", flush=True)
        start = time.time()
        while time.time() - start < (args.timeout or 300):
            r = _get(f"/v1/chart/{job_id}")
            status = r.get("status", "unknown")
            print(f"{status} ", end="", flush=True)
            if status in ("completed", "success"):
                print()
                break
            if status in ("failed",):
                print()
                break
            time.sleep(2)
        final = _get(f"/v1/chart/{job_id}")
        print(json.dumps(final, indent=2)[:500])


def cmd_distill(args: argparse.Namespace):
    data = {"urls": [args.url], "prompt": args.prompt, "schema": args.schema}
    if args.format:
        data["format"] = args.format
    print(f"Distilling: {args.url}")
    result = _post("/v1/distill", data)
    job_id = result.get("job_id")
    if not job_id:
        print(json.dumps(result, indent=2))
        return
    print(f"Job ID: {job_id}")
    if args.wait:
        print("Polling... ", end="", flush=True)
        start = time.time()
        while time.time() - start < (args.timeout or 300):
            r = _get(f"/v1/distill/{job_id}")
            status = r.get("status", "unknown")
            print(f"{status} ", end="", flush=True)
            if status in ("completed", "success"):
                print()
                break
            if status in ("failed",):
                print()
                break
            time.sleep(2)
        final = _get(f"/v1/distill/{job_id}")
        print(json.dumps(final, indent=2)[:1000])


def cmd_seek(args: argparse.Namespace):
    data = {"query": args.query, "recency_days": args.recency_days}
    print(f"Searching: {args.query}")
    result = _post("/v1/seek", data)
    print(json.dumps(result, indent=2)[:1000])


def cmd_flock(args: argparse.Namespace):
    data = {"urls": args.urls, "formats": args.formats or ["markdown"]}
    print(f"Flocking {len(args.urls)} URLs...")
    result = _post("/v1/flock", data)
    job_id = result.get("job_id")
    if not job_id:
        print(json.dumps(result, indent=2))
        return
    print(f"Job ID: {job_id}")
    if args.wait:
        print("Polling... ", end="", flush=True)
        start = time.time()
        while time.time() - start < (args.timeout or 300):
            r = _get(f"/v1/flock/{job_id}")
            status = r.get("status", "unknown")
            print(f"{status} ", end="", flush=True)
            if status in ("completed", "success"):
                print()
                break
            if status in ("failed",):
                print()
                break
            time.sleep(2)
        final = _get(f"/v1/flock/{job_id}")
        print(json.dumps(final, indent=2)[:1000])


def cmd_jobs(args: argparse.Namespace):
    if args.job_id:
        result = _get(f"/v1/jobs/{args.job_id}")
        print(json.dumps(result, indent=2))
    else:
        result = _get("/v1/jobs")
        if isinstance(result, dict) and "jobs" in result:
            for job in result["jobs"][:20]:
                print(f"  {job['job_id'][:8]}  {job['status']:12}  {job.get('created_at','')[:19]}  {job.get('request',{}).get('url','')}")
        else:
            print(json.dumps(result, indent=2))


def cmd_schedule(args: argparse.Namespace):
    if args.schedule_cmd == "list":
        result = _get("/v1/schedule")
        if isinstance(result, list):
            for s in result:
                print(f"  {s['id'][:8]}  {s.get('status','active'):8}  {s.get('job_type',''):8}  {s.get('cron', s.get('interval',''))}")
        else:
            print(json.dumps(result, indent=2))
    elif args.schedule_cmd == "delete":
        result = _delete(f"/v1/schedule/{args.schedule_id}")
        print(json.dumps(result, indent=2))
    elif args.schedule_cmd == "create":
        # Create from a JSON file or inline
        if args.payload_file:
            with open(args.payload_file) as f:
                payload = json.load(f)
        else:
            payload = json.loads(args.payload)
        # Determine job_type from endpoint
        job_type = args.job_type  # sweep, distill, flock
        schedule_data = {
            "job_type": job_type,
            "request": payload,
            "cron": args.cron if args.cron else None,
            "interval_seconds": args.interval or None,
            "webhook_url": args.webhook or None,
        }
        result = _post("/v1/schedule", {k: v for k, v in schedule_data.items() if v is not None})
        print(json.dumps(result, indent=2))


def _print_result(result: dict, formats: list):
    data = result.get("data", result)
    for fmt in formats:
        if fmt == "markdown" and data.get("markdown"):
            print(data["markdown"][:500])
        elif fmt == "html" and data.get("html"):
            print(data["html"][:300])
        elif fmt == "links" and data.get("links"):
            print(f"Links ({len(data['links'])}):")
            for link in data["links"][:20]:
                print(f"  {link}")
        elif fmt == "metadata" and data.get("metadata"):
            print(json.dumps(data["metadata"], indent=2))


def main():
    parser = argparse.ArgumentParser(description="Huginn CLI — autonomous web intelligence")
    parser.add_argument("--base-url", default=BASE_URL, help="API base URL")
    parser.add_argument("--api-key", default=API_KEY, help="API key")
    sub = parser.add_subparsers(dest="cmd")

    # probe
    p_probe = sub.add_parser("probe", help="Scrape a single URL")
    p_probe.add_argument("url", help="URL to scrape")
    p_probe.add_argument("--formats", nargs="+", choices=["markdown", "html", "raw_html", "screenshot", "links", "metadata", "pdf"])
    p_probe.add_argument("--no-filter", action="store_true", help="Don't filter to main content")
    p_probe.add_argument("--include-tags", help="Comma-separated tags to include")
    p_probe.add_argument("--exclude-tags", help="Comma-separated tags to exclude")

    # sweep
    p_sweep = sub.add_parser("sweep", help="Crawl a site")
    p_sweep.add_argument("url", help="Starting URL")
    p_sweep.add_argument("--depth", type=int, help="Crawl depth")
    p_sweep.add_argument("--max-pages", type=int, help="Max pages to crawl")
    p_sweep.add_argument("--concurrency", type=int, help="Concurrent page limit")
    p_sweep.add_argument("--start-urls", help="Comma-separated additional start URLs")
    p_sweep.add_argument("--wait", action="store_true", help="Wait for completion")
    p_sweep.add_argument("--timeout", type=int, default=300, help="Poll timeout")

    # chart
    p_chart = sub.add_parser("chart", help="Generate a site map")
    p_chart.add_argument("url", help="Root URL")
    p_chart.add_argument("--search", help="Limit to pages matching this search")
    p_chart.add_argument("--wait", action="store_true", help="Wait for completion")
    p_chart.add_argument("--timeout", type=int, default=300, help="Poll timeout")

    # distill
    p_distill = sub.add_parser("distill", help="Extract structured data")
    p_distill.add_argument("url", help="URL to extract from")
    p_distill.add_argument("prompt", help="Natural language extraction prompt")
    p_distill.add_argument("--format", choices=["text", "markdown", "json"], default="markdown")
    p_distill.add_argument("--schema", help="JSON schema for structured extraction")
    p_distill.add_argument("--wait", action="store_true", help="Wait for completion")
    p_distill.add_argument("--timeout", type=int, default=300, help="Poll timeout")

    # seek
    p_seek = sub.add_parser("seek", help="Web search")
    p_seek.add_argument("query", help="Search query")
    p_seek.add_argument("--recency-days", type=int, help="Limit to recent results")

    # flock
    p_flock = sub.add_parser("flock", help="Batch scrape multiple URLs")
    p_flock.add_argument("urls", nargs="+", help="URLs to scrape")
    p_flock.add_argument("--formats", nargs="+", choices=["markdown", "html", "raw_html", "screenshot", "links", "metadata"])
    p_flock.add_argument("--wait", action="store_true", help="Wait for completion")
    p_flock.add_argument("--timeout", type=int, default=300, help="Poll timeout")

    # jobs
    p_jobs = sub.add_parser("jobs", help="List or inspect jobs")
    p_jobs.add_argument("job_id", nargs="?", help="Job ID to inspect")

    # schedule
    p_sched = sub.add_parser("schedule", help="Manage scheduled jobs")
    sp = p_sched.add_subparsers(dest="schedule_cmd")
    sp_list = sp.add_parser("list", help="List schedules")
    sp_del = sp.add_parser("delete", help="Delete a schedule")
    sp_del.add_argument("schedule_id", help="Schedule ID")
    sp_create = sp.add_parser("create", help="Create a schedule")
    sp_create.add_argument("job_type", choices=["sweep", "distill", "flock"], help="Job type")
    sp_create.add_argument("payload", nargs="?", help="JSON payload (or use --file)")
    sp_create.add_argument("--file", dest="payload_file", help="JSON file with payload")
    sp_create.add_argument("--cron", help="Cron expression (e.g. '0 9 * * *')")
    sp_create.add_argument("--interval", type=int, help="Interval in seconds")
    sp_create.add_argument("--webhook", help="Webhook URL for notifications")

    args = parser.parse_args()

    # Override globals with CLI flags
    global BASE_URL, API_KEY
    if args.base_url:
        BASE_URL = args.base_url.rstrip("/")
    if args.api_key:
        API_KEY = args.api_key

    if not args.cmd:
        parser.print_help()
        sys.exit(1)

    # Check connectivity
    try:
        _get("/health")
    except Exception as e:
        print(f"Error: Cannot connect to {BASE_URL}/health — {e}", file=sys.stderr)
        sys.exit(1)

    try:
        if args.cmd == "probe":
            cmd_probe(args)
        elif args.cmd == "sweep":
            cmd_sweep(args)
        elif args.cmd == "chart":
            cmd_chart(args)
        elif args.cmd == "distill":
            cmd_distill(args)
        elif args.cmd == "seek":
            cmd_seek(args)
        elif args.cmd == "flock":
            cmd_flock(args)
        elif args.cmd == "jobs":
            cmd_jobs(args)
        elif args.cmd == "schedule":
            cmd_schedule(args)
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(130)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
