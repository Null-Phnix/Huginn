"""
BlackCrawl Crawler — Recursive site crawling with BFS.

The /v1/crawl endpoint engine. Manages concurrent page scraping,
URL deduplication, depth limits, and path filtering.
"""

import asyncio
import logging
import re
import time
from collections import deque
from typing import Any, Callable, Dict, List, Optional, Set
from urllib.parse import urlparse, urljoin
from pathlib import PurePosixPath

from .browser import BrowserManager
from .models import OutputFormat, ScrapeData
from .scraper import Scraper

logger = logging.getLogger(__name__)


class CrawlResult:
    """Container for crawl results and progress."""

    def __init__(self, job_id: str):
        self.job_id = job_id
        self.pages: List[ScrapeData] = []
        self.visited: Set[str] = set()
        self.queued: Set[str] = set()
        self.errors: List[str] = []
        self.completed = 0
        self.total_discovered = 0
        self.start_time = time.time()

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time


class Crawler:
    """BFS web crawler with concurrency, dedup, and path filtering."""

    def __init__(
        self,
        browser: BrowserManager,
        max_depth: int = 3,
        max_pages: int = 100,
        concurrency: int = 5,
        delay: float = 1.0,
        allow_external: bool = False,
        allow_backward: bool = False,
        include_paths: Optional[List[str]] = None,
        exclude_paths: Optional[List[str]] = None,
    ):
        self.browser = browser
        self.scraper = Scraper(browser)
        self.max_depth = max_depth
        self.max_pages = max_pages
        self.concurrency = concurrency
        self.delay = delay
        self.allow_external = allow_external
        self.allow_backward = allow_backward
        self.include_paths = include_paths or []
        self.exclude_paths = exclude_paths or []
        self._cancel = False

    def cancel(self):
        """Signal cancellation."""
        self._cancel = True

    async def crawl(
        self,
        start_url: str,
        scrape_formats: Optional[List[OutputFormat]] = None,
        only_main_content: bool = True,
        timeout: int = 30000,
        on_progress: Optional[Callable] = None,
    ) -> CrawlResult:
        """
        Crawl from start_url using BFS. Returns CrawlResult with all scraped pages.

        Args:
            start_url: URL to start crawling from
            scrape_formats: Output formats for each page
            only_main_content: Extract only main content per page
            timeout: Per-page timeout in ms
            on_progress: Optional callback(completed, total) for progress updates
        """
        if scrape_formats is None:
            scrape_formats = [OutputFormat.MARKDOWN]

        result = CrawlResult(job_id="")
        parsed_start = urlparse(start_url)
        base_domain = parsed_start.netloc
        base_path_prefix = parsed_start.path.rstrip("/") if not self.allow_backward else "/"

        # BFS queue: (url, depth)
        queue: deque = deque()
        queue.append((start_url, 0))
        result.queued.add(self._normalize_url(start_url))

        # Semaphore for concurrency control
        sem = asyncio.Semaphore(self.concurrency)

        async def process_page(url: str, depth: int):
            """Process a single page: scrape it and discover new links."""
            if self._cancel:
                return

            async with sem:
                # Rate limiting delay
                if self.delay > 0:
                    await asyncio.sleep(self.delay)

                try:
                    # Scrape the page
                    formats = list(scrape_formats) + [OutputFormat.LINKS]
                    page_data = await self.scraper.scrape(
                        url=url,
                        formats=formats,
                        only_main_content=only_main_content,
                        timeout=timeout,
                    )

                    # Add page to results
                    result.pages.append(page_data)
                    result.completed += 1

                    # Discover new links
                    links = page_data.links or []
                    for link in links:
                        norm_link = self._normalize_url(link)
                        if norm_link in result.visited or norm_link in result.queued:
                            continue
                        if not self._should_follow(norm_link, base_domain, depth + 1, base_path_prefix=base_path_prefix):
                            continue
                        if len(result.queued) >= self.max_pages * 2:  # discovery cap
                            continue

                        result.queued.add(norm_link)
                        queue.append((link, depth + 1))
                        result.total_discovered = len(result.queued)

                    if on_progress:
                        on_progress(result.completed, result.total_discovered)

                except Exception as e:
                    logger.error(f"Crawl error on {url}: {e}")
                    result.errors.append(f"{url}: {str(e)}")

        # Main BFS loop
        while queue and not self._cancel:
            if result.completed >= self.max_pages:
                logger.info(f"Reached max_pages limit ({self.max_pages})")
                break

            # Collect batch of pages at next depth level
            batch = []
            while queue and len(batch) < self.concurrency:
                url, depth = queue.popleft()
                if depth > self.max_depth:
                    continue
                norm_url = self._normalize_url(url)
                if norm_url in result.visited:
                    continue
                result.visited.add(norm_url)
                batch.append((url, depth))

            if not batch:
                break

            # Process batch concurrently
            tasks = [process_page(url, depth) for url, depth in batch]
            await asyncio.gather(*tasks, return_exceptions=True)

        result.total_discovered = len(result.queued)
        return result

    def _normalize_url(self, url: str) -> str:
        """Normalize URL for deduplication: strip fragment, trailing slash, query sort."""
        parsed = urlparse(url)
        # Remove fragment
        path = parsed.path.rstrip("/") or "/"
        # Sort query params for consistent hashing
        query_parts = sorted(parsed.query.split("&")) if parsed.query else []
        query = "&".join(query_parts)
        normalized = f"{parsed.scheme}://{parsed.netloc}{path}"
        if query:
            normalized += f"?{query}"
        return normalized

    def _should_follow(self, url: str, base_domain: str, depth: int, base_path_prefix: str = "") -> bool:
        """Determine if a discovered URL should be added to the crawl queue."""
        if depth > self.max_depth:
            return False

        parsed = urlparse(url)

        # Skip non-http(s) URLs
        if parsed.scheme not in ("http", "https"):
            return False

        # Skip common non-content URLs
        skip_extensions = {
            ".pdf", ".zip", ".tar", ".gz", ".png", ".jpg", ".jpeg",
            ".gif", ".svg", ".mp3", ".mp4", ".avi", ".mov", ".woff",
            ".woff2", ".ttf", ".eot", ".ico", ".css", ".js",
        }
        path_lower = parsed.path.lower()
        if any(path_lower.endswith(ext) for ext in skip_extensions):
            return False

        # External link check
        if not self.allow_external and parsed.netloc != base_domain:
            return False

        # Backward crawling check (subpaths only by default)
        if not self.allow_backward and base_path_prefix:
            if not parsed.path.startswith(base_path_prefix):
                return False

        # Include/exclude path filtering
        if self.include_paths:
            if not any(parsed.path.startswith(p) or self._path_match(p, parsed.path) for p in self.include_paths):
                return False

        if self.exclude_paths:
            if any(parsed.path.startswith(p) or self._path_match(p, parsed.path) for p in self.exclude_paths):
                return False

        return True

    @staticmethod
    def _path_match(pattern: str, path: str) -> bool:
        """Simple glob-style path matching."""
        if "*" not in pattern:
            return path.startswith(pattern)
        # Convert simple glob to regex
        regex = pattern.replace(".", r"\.").replace("*", ".*")
        return bool(re.match(f"^{regex}$", path))