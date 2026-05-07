"""
Huginn Crawler — Recursive site crawling with BFS.

The /v1/crawl endpoint engine. Manages concurrent page scraping,
URL deduplication, depth limits, and path filtering.
"""

import asyncio
import hashlib
import heapq
import logging
import re
import signal
import time
from collections import deque
from typing import Any, Callable, Dict, List, Optional, Set
from urllib.parse import urlparse, urljoin
from pathlib import PurePosixPath

import httpx

from .browser import BrowserManager
from .models import OutputFormat, ScrapeData
from .scraper import Scraper

logger = logging.getLogger(__name__)


def content_hash(text: str) -> str:
    """Hash cleaned text for duplicate detection.

    Normalizes whitespace before hashing so superficially different
    content (extra spaces, newlines) hashes the same.
    """
    cleaned = re.sub(r'\s+', ' ', text).strip().lower()
    return hashlib.sha256(cleaned.encode('utf-8')).hexdigest()[:16]


class RobotsChecker:
    """Check robots.txt for allowed/disallowed paths.

    Parses robots.txt for User-agent: * rules. Supports Disallow
    and Allow directives with longest-match priority.
    """

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        parsed = urlparse(self.base_url)
        self.robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        self.rules: Optional[Dict[str, List[str]]] = None  # None = not fetched yet

    async def fetch_rules(self) -> None:
        """Fetch and parse robots.txt from the site."""
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                resp = await client.get(self.robots_url)
                if resp.status_code == 200:
                    self.rules = self._parse(resp.text)
                else:
                    # No robots.txt or error = allow all
                    self.rules = {"disallow": [], "allow": []}
        except Exception:
            # On any error, allow all
            self.rules = {"disallow": [], "allow": []}

    def is_allowed(self, path: str) -> bool:
        """Check if a path is allowed by robots.txt rules.

        If rules haven't been fetched yet, allows all paths.
        Uses longest-match: more specific Allow rules override Disallow.
        """
        if self.rules is None:
            return True  # Not fetched yet, allow

        # Find the most specific matching rule
        best_disallow = ""
        best_allow = ""

        for pattern in self.rules.get("disallow", []):
            if path.startswith(pattern) or pattern == "/":
                if len(pattern) > len(best_disallow):
                    best_disallow = pattern

        for pattern in self.rules.get("allow", []):
            if path.startswith(pattern) and len(pattern) > len(best_allow):
                best_allow = pattern

        # More specific allow wins over less specific disallow
        if best_allow and len(best_allow) >= len(best_disallow):
            return True
        if best_disallow:
            return False
        return True

    def _parse(self, robots_text: str) -> Dict[str, List[str]]:
        """Parse robots.txt for User-agent: * rules."""
        disallow = []
        allow = []
        in_wildcard = False

        for line in robots_text.splitlines():
            line = line.strip()
            if line.startswith("#") or not line:
                continue
            if line.lower().startswith("user-agent:"):
                agent = line.split(":", 1)[1].strip()
                in_wildcard = agent == "*"
            elif in_wildcard:
                if line.lower().startswith("disallow:"):
                    path = line.split(":", 1)[1].strip()
                    if path:
                        disallow.append(path)
                elif line.lower().startswith("allow:"):
                    path = line.split(":", 1)[1].strip()
                    if path:
                        allow.append(path)

        return {"disallow": disallow, "allow": allow}


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
    """Priority web crawler with concurrency, dedup, pagination detection, and SIGINT handling."""

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
        skip_duplicates: bool = True,
        ignore_robots: bool = False,
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
        self.skip_duplicates = skip_duplicates
        self.ignore_robots = ignore_robots
        self._robots_checker: Optional[RobotsChecker] = None
        self._cancel = False
        self._seen_hashes: Set[str] = set()

    def cancel(self):
        """Signal cancellation."""
        self._cancel = True

    def is_duplicate(self, text: str, seen_hashes: Set[str]) -> bool:
        """Check if content is a duplicate of a previously seen page."""
        h = content_hash(text)
        if h in seen_hashes:
            return True
        seen_hashes.add(h)
        return False

    # ─── Pagination ──────────────────────────────────────────────────────────
    _PAGINATION_PATTERNS = [
        r"/page/(\d+)",
        r"/p/(\d+)",
        r"/\?(?:.*[&;]|)page=(\d+)",
        r"/\?(?:.*[&;]|)p=(\d+)",
        r"-page-(\d+)",
        r"_(\d+)\.html?",
    ]

    def _detect_pagination(self, links: list[str]) -> Optional[dict]:
        """Detect pagination patterns in discovered links. Returns {base_url, page_numbers}."""
        import re
        seen_bases = {}
        for link in links:
            parsed = urlparse(link)
            for pattern in self._PAGINATION_PATTERNS:
                m = re.search(pattern, parsed.path + "?" + parsed.query)
                if m:
                    page_num = int(m.group(1))
                    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rsplit('/', 1)[0]}"
                    if base not in seen_bases:
                        seen_bases[base] = []
                    seen_bases[base].append(page_num)
                    break
        if not seen_bases:
            return None
        best = max(seen_bases.items(), key=lambda x: len(set(x[1])))
        return {"base": best[0], "pages": sorted(set(best[1]))}

    async def crawl(
        self,
        start_url: str,
        scrape_formats: Optional[List[OutputFormat]] = None,
        only_main_content: bool = True,
        timeout: int = 30000,
        on_progress: Optional[Callable] = None,
    ) -> CrawlResult:
        """
        Priority crawl from start_url. Returns CrawlResult with all scraped pages.

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

        # Priority heap: (priority_score, counter, url, depth)
        # Lower score = higher priority. same-domain, shallow pages first.
        counter = 0
        def url_priority(url: str, depth: int) -> tuple:
            parsed = urlparse(url)
            same_domain_penalty = 0 if parsed.netloc == base_domain else 100
            return (same_domain_penalty, depth, len(url))

        heap: list = []
        heapq.heappush(heap, (url_priority(start_url, 0), counter, start_url, 0))
        counter += 1
        result.queued.add(self._normalize_url(start_url))

        # Semaphore for concurrency control
        sem = asyncio.Semaphore(self.concurrency)

        async def process_page(url: str, depth: int):
            """Process a single page: scrape it and discover new links."""
            if self._cancel:
                return

            async with sem:
                if self.delay > 0:
                    await asyncio.sleep(self.delay)

                try:
                    formats = list(scrape_formats) + [OutputFormat.LINKS]
                    page_data = await self.scraper.scrape(
                        url=url,
                        formats=formats,
                        only_main_content=only_main_content,
                        timeout=timeout,
                    )

                    # Skip duplicate content
                    if self.skip_duplicates and page_data.markdown:
                        if self.is_duplicate(page_data.markdown, self._seen_hashes):
                            logger.info(f"Skipping duplicate content: {url}")
                            result.visited.add(self._normalize_url(url))
                            return

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
                        if len(result.queued) >= self.max_pages * 2:
                            continue
                        result.queued.add(norm_link)
                        heapq.heappush(heap, (url_priority(link, depth + 1), counter, link, depth + 1))
                        counter += 1
                        result.total_discovered = len(result.queued)

                    if on_progress:
                        on_progress(result.completed, result.total_discovered)

                except Exception as e:
                    logger.error(f"Crawl error on {url}: {e}")
                    result.errors.append(f"{url}: {str(e)}")

        # SIGINT handler for graceful shutdown
        old_handler = signal.signal(signal.SIGINT, lambda s, f: setattr(self, "_cancel", True))

        try:
            while heap and not self._cancel:
                if result.completed >= self.max_pages:
                    logger.info(f"Reached max_pages limit ({self.max_pages})")
                    break

                # Collect batch
                batch = []
                while heap and len(batch) < self.concurrency:
                    _, _, url, depth = heapq.heappop(heap)
                    if depth > self.max_depth:
                        continue
                    norm_url = self._normalize_url(url)
                    if norm_url in result.visited:
                        continue
                    result.visited.add(norm_url)
                    batch.append((url, depth))

                if not batch:
                    break

                tasks = [process_page(url, depth) for url, depth in batch]
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            signal.signal(signal.SIGINT, old_handler)

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