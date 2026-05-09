"""
Huginn Benchmark Suite

Measures crawl engine throughput with deterministic site graphs.
Uses fake scrapers to isolate crawler overhead (URL discovery, dedup,
queue management, worker pool efficiency) from network/browser latency.

Run:  python benchmarks/bench.py
Output: benchmarks/results/latest.json + stdout table
"""

import asyncio
import json
import os
import sys
import time
import tracemalloc
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from huginn.crawler import Crawler
from huginn.models import ScrapeData


@dataclass
class BenchResult:
    name: str
    pages: int
    concurrency: int
    depth: int
    elapsed_sec: float
    pages_per_sec: float
    peak_mb: float
    errors: int


def make_scraper_for_graph(graph: dict) -> Callable:
    """Return a fake scraper callable for a page graph.

    graph: {url: {"links": [urls], "title": str}}
    """
    pages = {}
    for url, info in graph.items():
        pages[url] = ScrapeData(
            markdown=f"# {info['title']}\n\nContent for {url}",
            metadata={
                "url": url,
                "title": info["title"],
                "status_code": 200,
            },
            links=info.get("links", []),
        )

    class FakeScraper:
        async def scrape(self, url, **kwargs):
            return pages.get(url, ScrapeData(metadata={"url": url, "title": "unknown"}))

    return FakeScraper()


def build_chain(start: str, n: int) -> dict:
    """Linear chain: page1 -> page2 -> ... -> pageN"""
    graph = {start: {"title": "Home", "links": []}}
    for i in range(1, n):
        url = f"{start}/page{i}"
        next_url = f"{start}/page{i+1}" if i + 1 < n else None
        graph[start]["links"].append(url)
        graph[url] = {"title": f"Page {i}", "links": []}
        if next_url:
            graph[url]["links"].append(next_url)
    return graph


def build_tree(start: str, branching: int, depth: int) -> dict:
    """Tree: each page links to `branching` children."""
    graph = {start: {"title": "Root", "links": []}}
    total = 1
    queue = [(start, 0)]
    while queue:
        url, d = queue.pop(0)
        if d >= depth:
            continue
        for i in range(branching):
            child = f"{url}/child{i}"
            if child not in graph:
                graph[child] = {"title": f"Child {i} of {url}", "links": []}
                total += 1
            graph[url]["links"].append(child)
            queue.append((child, d + 1))
    return graph


def build_star(start: str, n: int) -> dict:
    """Star: home links to N leaf pages."""
    graph = {start: {"title": "Hub", "links": []}}
    for i in range(n):
        leaf = f"{start}/leaf{i}"
        graph[start]["links"].append(leaf)
        graph[leaf] = {"title": f"Leaf {i}", "links": []}
    return graph


async def run_benchmark(
    name: str,
    graph: dict,
    concurrency: int,
    max_depth: int,
    max_pages: int,
) -> BenchResult:
    """Run a single benchmark scenario."""
    from unittest.mock import AsyncMock, MagicMock

    browser = MagicMock()
    browser.stop = AsyncMock()
    browser.new_context = AsyncMock()
    browser.new_page = AsyncMock()
    browser.navigate = AsyncMock(return_value=True)
    browser.smart_wait = AsyncMock()
    browser.extract_content = AsyncMock(return_value={
        "title": "Test", "url": "http://example.com", "description": "", "language": "en"
    })
    browser.to_markdown = AsyncMock(return_value="# Test")
    browser.get_links = AsyncMock(return_value=[])
    browser.last_status_code = 200

    scraper = make_scraper_for_graph(graph)
    crawler = Crawler(
        browser=browser,
        max_depth=max_depth,
        max_pages=max_pages,
        concurrency=concurrency,
        delay=0,
    )
    crawler.scraper = scraper

    # Track memory
    tracemalloc.start()
    start_time = time.perf_counter()
    result = await crawler.crawl("http://example.com")
    elapsed = time.perf_counter() - start_time
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return BenchResult(
        name=name,
        pages=result.completed,
        concurrency=concurrency,
        depth=max_depth,
        elapsed_sec=round(elapsed, 3),
        pages_per_sec=round(result.completed / elapsed, 2) if elapsed > 0 else 0,
        peak_mb=round(peak / (1024 * 1024), 2),
        errors=len(result.errors),
    )


async def main():
    scenarios = [
        # (name, graph_builder, kwargs, concurrency, max_depth, max_pages)
        ("chain-10", build_chain, {"n": 10}, 1, 10, 10),
        ("chain-10", build_chain, {"n": 10}, 3, 10, 10),
        ("chain-50", build_chain, {"n": 50}, 3, 50, 50),
        ("chain-50", build_chain, {"n": 50}, 5, 50, 50),
        ("tree-2x3", build_tree, {"branching": 2, "depth": 3}, 1, 3, 20),
        ("tree-2x3", build_tree, {"branching": 2, "depth": 3}, 3, 3, 20),
        ("tree-3x3", build_tree, {"branching": 3, "depth": 3}, 3, 3, 50),
        ("tree-3x3", build_tree, {"branching": 3, "depth": 3}, 5, 3, 50),
        ("star-20", build_star, {"n": 20}, 1, 2, 25),
        ("star-20", build_star, {"n": 20}, 5, 2, 25),
        ("star-50", build_star, {"n": 50}, 5, 2, 60),
        ("star-100", build_star, {"n": 100}, 5, 2, 110),
    ]

    results: List[BenchResult] = []
    print("=" * 80)
    print("Huginn Crawl Benchmark Suite")
    print("=" * 80)
    print()

    for name, builder, kwargs, concurrency, depth, max_pages in scenarios:
        graph = builder("http://example.com", **kwargs)
        print(f"Running {name} (concurrency={concurrency}, depth={depth}, max_pages={max_pages}) ...", end=" ")
        sys.stdout.flush()
        result = await run_benchmark(name, graph, concurrency, depth, max_pages)
        results.append(result)
        print(f"{result.elapsed_sec}s — {result.pages_per_sec} pages/sec")

    print()
    print("-" * 80)
    print(f"{'Scenario':<20} {'Workers':<8} {'Pages':<8} {'Time(s)':<10} {'Pages/s':<10} {'Peak(MB)':<10}")
    print("-" * 80)
    for r in results:
        print(f"{r.name:<20} {r.concurrency:<8} {r.pages:<8} {r.elapsed_sec:<10} {r.pages_per_sec:<10} {r.peak_mb:<10}")
    print("-" * 80)

    # Save JSON
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)
    timestamp = datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = results_dir / f"{timestamp}.json"
    with open(out_path, "w") as f:
        json.dump({
            "timestamp": datetime.now(datetime.timezone.utc).isoformat(),
            "huginn_version": "1.2.0",
            "results": [asdict(r) for r in results],
        }, f, indent=2)

    # Also write "latest.json" symlink-ish (just overwrite)
    latest = results_dir / "latest.json"
    with open(latest, "w") as f:
        json.dump({
            "timestamp": datetime.now(datetime.timezone.utc).isoformat(),
            "huginn_version": "1.2.0",
            "results": [asdict(r) for r in results],
        }, f, indent=2)

    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
