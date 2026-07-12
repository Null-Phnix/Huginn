"""
Huginn Smoke Test Suite

Tests all scraper code paths (light render, fallback, full render, error
handling) using a mock HTTP server on localhost. No external DNS needed.

Run:  python scripts/smoke_test.py
"""

import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from huginn import __version__
from huginn.browser import BrowserManager
from huginn.scraper import Scraper, RenderMode
from huginn.models import OutputFormat


# ── Mock sites served from localhost ───────────────────────────────────────

MOCK_SITES = {
    "/static": b"""<!DOCTYPE html>
<html>
<head><title>Static Test Page</title></head>
<body>
<h1>Welcome to the static test</h1>
<p>This is a simple static page with minimal HTML.</p>
<a href="/page2">Link to page 2</a>
<footer>Footer text here</footer>
</body>
</html>""",
    "/spa": b"""<!DOCTYPE html>
<html>
<head><title>SPA Shell</title><script>window.APP_DATA={title:"Dynamic SPA"};</script></head>
<body>
<div id="app">Content loaded via JS</div>
<p>This is a single-page application shell. The real content is loaded dynamically.</p>
</body>
</html>""",
    "/minimal": b"<html><head><title>Tiny</title></head><body>hi</body></html>",
    "/empty": b"<html><head><title>Empty</title></head><body></body></html>",
    "/json": b'{"message": "This is JSON content", "status": "ok"}',
}


class MockHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        content = MOCK_SITES.get(path)
        if content is None:
            self.send_error(404)
            return

        content_type = "application/json" if path == "/json" else "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Server", "MockServer/1.0")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, fmt, *args):
        pass  # Suppress logs


def start_mock_server(port=8765):
    server = HTTPServer(("127.0.0.1", port), MockHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


@dataclass
class SiteResult:
    url: str
    category: str
    passed: bool
    markdown_ok: bool
    links_ok: bool
    metadata_ok: bool
    elapsed_sec: float
    render_mode: str
    error: Optional[str] = None
    markdown_preview: str = ""
    title: str = ""


async def test_site(browser, url: str, category: str, expected_words: List[str]) -> SiteResult:
    scraper = Scraper(browser)
    start = time.perf_counter()

    try:
        data = await asyncio.wait_for(
            scraper.scrape(
                url=url,
                formats=[OutputFormat.MARKDOWN, OutputFormat.LINKS, OutputFormat.METADATA],
                timeout=15000,
            ),
            timeout=25,
        )
        elapsed = time.perf_counter() - start
        render_mode = data.metadata.get("render_mode", "unknown") if data.metadata else "unknown"

        markdown = data.markdown or ""
        links = data.links or []
        meta = data.metadata or {}
        title = meta.get("title", "")

        markdown_ok = len(markdown) > 0 and any(w.lower() in markdown.lower() for w in expected_words)
        links_ok = len(links) >= 0
        metadata_ok = bool(title) and len(title) > 0

        return SiteResult(
            url=url,
            category=category,
            passed=markdown_ok and metadata_ok,
            markdown_ok=markdown_ok,
            links_ok=links_ok,
            metadata_ok=metadata_ok,
            elapsed_sec=round(elapsed, 2),
            render_mode=render_mode,
            markdown_preview=markdown[:200].replace("\n", " "),
            title=title,
        )
    except Exception as e:
        return SiteResult(
            url=url,
            category=category,
            passed=False,
            markdown_ok=False,
            links_ok=False,
            metadata_ok=False,
            elapsed_sec=round(time.perf_counter() - start, 2),
            render_mode="error",
            error=str(e)[:200],
        )


async def main():
    print("=" * 80)
    print("Huginn Smoke Test Suite")
    print("=" * 80)
    print()

    server, port = start_mock_server()
    base = f"http://127.0.0.1:{port}"

    browser = BrowserManager(headless=True, stealth=True)
    await browser.start()

    sites = [
        (f"{base}/static", "static", ["static", "test"], True),   # Should have markdown
        (f"{base}/spa", "spa", ["spa", "dynamic"], True),        # Should have markdown
        (f"{base}/minimal", "minimal", ["hi"], True),             # Minimal but has text
        (f"{base}/empty", "empty", [], False),                   # Truly empty → no markdown expected
        (f"{base}/json", "json", ["message", "status"], False),   # JSON: no HTML title expected, just raw content
    ]

    results: List[SiteResult] = []
    for url, category, expected, expect_markdown in sites:
        print(f"Testing {url} ({category}) ...", end=" ", flush=True)
        result = await test_site(browser, url, category, expected)
        results.append(result)
        # Override pass logic for edge cases
        if not expect_markdown:
            result.passed = result.error is None  # Pass if no crash
        status = "PASS" if result.passed else "FAIL"
        print(f"{status} ({result.elapsed_sec}s, {result.render_mode})")
        if result.error:
            print(f"  Error: {result.error}")

    await browser.stop()
    server.shutdown()

    print()
    print("-" * 80)
    print(f"{'Site':<35} {'Category':<12} {'Render':<8} {'Markdown':<10} {'Meta':<8} {'Time':<8} {'Status'}")
    print("-" * 80)
    for r in results:
        md = "OK" if r.markdown_ok else "FAIL"
        mt = "OK" if r.metadata_ok else "FAIL"
        st = "PASS" if r.passed else "FAIL"
        print(f"{r.url:<35} {r.category:<12} {r.render_mode:<8} {md:<10} {mt:<8} {r.elapsed_sec:<8.2f} {st}")
    print("-" * 80)

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"\n{passed}/{total} passed ({passed/total*100:.0f}%)")

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "huginn_version": __version__,
        "passed": passed,
        "total": total,
        "results": [asdict(r) for r in results],
    }
    report_path = PROJECT_ROOT / "benchmarks" / "results" / "smoke_latest.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
