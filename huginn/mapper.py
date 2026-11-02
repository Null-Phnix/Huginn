"""
Huginn Mapper — Fast URL discovery without full content extraction.

The /v1/map endpoint engine. Uses DOM walker's link extraction and
optional sitemap parsing for rapid site mapping.
"""

import logging
import re
from typing import List, Optional, Set
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

try:
    from defusedxml import ElementTree as ElementTree
except ImportError:
    # Fallback: disable entity expansion as a basic defense
    import xml.sax
    from xml.etree import ElementTree
    xml.sax.make_parser = lambda *a, **kw: xml.sax.make_parser.__wrapped__(*a, **kw) if hasattr(xml.sax.make_parser, '__wrapped__') else xml.sax.make_parser()
    logger.warning("defusedxml not installed — XML parsing vulnerable to bombs. pip install defusedxml")

import httpx

from .browser import BrowserManager
from .models import CrawlGraph, OutputFormat
from .scraper import Scraper
from .utils import scrape_failure

logger = logging.getLogger(__name__)


class Mapper:
    """Fast URL mapper: discovers all URLs on a site without full content extraction."""

    def __init__(self, browser: BrowserManager):
        # `browser` is kept for backward compatibility with existing call sites
        # (routers/map.py, researcher.py, cli.py all construct Mapper(browser)).
        # Link discovery itself no longer touches Playwright directly — it goes
        # through a Scraper, exactly like Crawler does, so StarSearch's
        # production enforcement / retry / circuit-breaker logic applies here too.
        self.browser = browser
        self.scraper = Scraper(browser)

    async def chart_site(self, *args, **kwargs):
        """Alias for map_site — used by /v1/chart endpoint."""
        return await self.map_site(*args, **kwargs)

    async def map_site(
        self,
        url: str,
        search: Optional[str] = None,
        include_subdomains: bool = False,
        limit: int = 5000,
        sitemap: str = "include",
    ) -> List[str]:
        """
        Map a site by extracting links from the page + sitemap.xml.

        Much faster than crawling because we don't extract content,
        just discover and return URLs.

        Args:
            url: Starting URL
            search: Filter URLs containing this string
            include_subdomains: Include links to subdomains
            limit: Maximum URLs to return
            sitemap: Sitemap mode (Firecrawl parity):
                "include" (default) — sitemap.xml + page links
                "skip"              — page links only, no sitemap fetch
                "only"              — sitemap.xml URLs only, no page crawl
        """
        if sitemap not in ("include", "skip", "only"):
            sitemap = "include"  # fallback to default

        all_urls: Set[str] = set()
        parsed_start = urlparse(url)
        base_domain = parsed_start.netloc

        # Strategy 1: Try sitemap.xml first (fast, no browser needed)
        # SKIP if sitemap="skip" (no sitemap fetch at all)
        if sitemap != "skip":
            sitemap_urls = await self._fetch_sitemap(url)
            if sitemap_urls:
                logger.info(f"Sitemap: found {len(sitemap_urls)} URLs")
                all_urls.update(sitemap_urls)

        # "only" mode — return just the sitemap, no page crawl
        if sitemap == "only":
            result = sorted(all_urls)[:limit]
            if search:
                search_lower = search.lower()
                result = [u for u in result if search_lower in u.lower()]
            return result

        # Strategy 2: Load the page and extract all links via DOM walker
        page_links, _title, _status = await self._extract_page_links(url, include_subdomains, base_domain)
        if page_links:
            logger.info(f"Page extraction: found {len(page_links)} URLs")
            all_urls.update(page_links)

        # Strategy 3: If we found URLs on the page, check if they have their own sitemaps
        # (limited to avoid recursion). Skip if sitemap="skip" to honour the flag.
        if len(all_urls) < limit and sitemap != "skip":
            sitemap_check_urls = [u for u in all_urls if u.endswith("/")]
            for sm_url in list(sitemap_check_urls)[:3]:  # Check up to 3 sub-urls for sitemaps
                sub_sitemap_urls = await self._fetch_sitemap(sm_url)
                all_urls.update(sub_sitemap_urls)

        # Filter by search query
        if search:
            search_lower = search.lower()
            all_urls = {u for u in all_urls if search_lower in u.lower()}

        # Filter by subdomain policy
        if not include_subdomains:
            all_urls = {u for u in all_urls if urlparse(u).netloc == base_domain or
                       urlparse(u).netloc.endswith(f".{base_domain}")}

        # Sort and limit
        result = sorted(all_urls)[:limit]
        return result

    async def _fetch_sitemap(self, base_url: str) -> Set[str]:
        """Fetch and parse sitemap.xml and robots.txt."""
        urls = set()
        parsed = urlparse(base_url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        # Try sitemap.xml
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            # Check robots.txt for sitemap location first
            sitemap_urls_to_check = [f"{base}/sitemap.xml"]

            try:
                robots_resp = await client.get(f"{base}/robots.txt")
                if robots_resp.status_code == 200:
                    for line in robots_resp.text.splitlines():
                        line = line.strip()
                        if line.lower().startswith("sitemap:"):
                            sitemap_url = line.split(":", 1)[1].strip()
                            sitemap_urls_to_check.append(sitemap_url)
            except Exception:
                pass

            # Parse each sitemap
            for sitemap_url in sitemap_urls_to_check:
                try:
                    resp = await client.get(sitemap_url)
                    if resp.status_code == 200:
                        urls.update(self._parse_sitemap_xml(resp.text, base))
                except Exception:
                    pass

        return urls

    def _parse_sitemap_xml(self, xml_text: str, base_url: str) -> Set[str]:
        """Parse sitemap XML and extract URLs."""
        urls = set()
        try:
            root = ElementTree.fromstring(xml_text)
            # Handle namespace
            ns = ""
            if root.tag.startswith("{"):
                ns = root.tag.split("}")[0] + "}"

            # Standard sitemap
            for url_elem in root.findall(f"{ns}url"):
                loc = url_elem.find(f"{ns}loc")
                if loc is not None and loc.text:
                    urls.add(loc.text.strip())

            # Sitemap index
            for sitemap_elem in root.findall(f"{ns}sitemap"):
                loc = sitemap_elem.find(f"{ns}loc")
                if loc is not None and loc.text:
                    urls.add(loc.text.strip())

            # Fallback: try without namespace
            if not urls:
                for url_elem in root.findall("url"):
                    loc = url_elem.find("loc")
                    if loc is not None and loc.text:
                        urls.add(loc.text.strip())
                for sitemap_elem in root.findall("sitemap"):
                    loc = sitemap_elem.find("loc")
                    if loc is not None and loc.text:
                        urls.add(loc.text.strip())

        except ElementTree.ParseError:
            # Try regex fallback for malformed XML
            url_pattern = re.compile(r'<loc>\s*(https?://[^<]+)\s*</loc>', re.IGNORECASE)
            for match in url_pattern.finditer(xml_text):
                urls.add(match.group(1).strip())

        return urls

    async def _extract_page_links(
        self,
        url: str,
        include_subdomains: bool,
        base_domain: str,
    ) -> tuple[set[str], str | None, int | None]:
        """Render the page via StarSearch (through Scraper.scrape) and extract
        all links + metadata.

        Mirrors huginn/crawler.py's link-discovery pattern: request
        ``formats=[OutputFormat.LINKS]`` from the Scraper (which picks
        StarSearch/Playwright per the configured backend and enforces
        production's StarSearch-only mode) and read links off
        ``page_data.links``. ``only_main_content=False`` so link discovery
        scans the whole page, matching the old DOM-wide
        ``document.querySelectorAll('a[href]')`` behavior instead of being
        limited to <main>/<article> content.

        include_subdomains/base_domain are accepted for signature
        compatibility with existing callers (map_site, map_site_graph), which
        already do their own subdomain/domain filtering on the returned set —
        this function does not filter by domain itself, preserving prior
        behavior.
        """
        try:
            page_data = await self.scraper.scrape(
                url=url,
                formats=[OutputFormat.LINKS],
                only_main_content=False,
            )
        except Exception as e:
            logger.warning(f"Page link extraction failed for {url}: {e}")
            return (set(), None, None)

        failure = scrape_failure(page_data)
        if failure:
            _, message = failure
            logger.warning(f"Page link extraction failed for {url}: {message}")
            return (set(), None, None)

        metadata = page_data.metadata or {}
        title = metadata.get("title")
        try:
            status_code = int(metadata.get("status_code", 200))
        except (TypeError, ValueError):
            status_code = 200

        links: set[str] = set()
        for raw_link in page_data.links or []:
            if not raw_link:
                continue
            href = raw_link.strip()
            if not href or href.startswith("javascript:") or href.startswith("#") or href.startswith("mailto:"):
                continue
            try:
                absolute = urljoin(url, href)
            except Exception:
                continue
            if urlparse(absolute).scheme not in ("http", "https"):
                continue
            links.add(absolute)

        return (links, title, status_code)

    async def map_site_graph(
        self,
        start_url: str,
        include_subdomains: bool = False,
        limit: int = 500,
        max_depth: int = 3,
    ) -> CrawlGraph:
        """
        Map a site as a directed graph of pages and links.

        Uses BFS up to max_depth, discovering pages and recording edges
        (source_page -> discovered_url). Each node stores title, status_code,
        and crawl depth.

        Returns a CrawlGraph with nodes and edges. Faster than full crawling
        because only links are extracted, not content.
        """
        from .models import CrawlGraph, PageEdge, PageNode

        parsed = urlparse(start_url)
        base_domain = parsed.netloc
        queue: list[tuple[str, int]] = [(start_url, 0)]  # (url, depth)
        seen: set[str] = {start_url}
        nodes: dict[str, PageNode] = {}
        edges: list[PageEdge] = []

        while queue and len(seen) < limit:
            url, depth = queue.pop(0)
            if depth > max_depth:
                continue

            # Extract links from this page
            links, title, status = await self._extract_page_links(
                url, include_subdomains, base_domain
            )

            # Create/update node for this page
            nodes[url] = PageNode(
                url=url,
                title=title or "",
                status_code=status,
                depth=depth,
            )

            for raw_link in links:
                # Normalize URL
                try:
                    link = urljoin(url, raw_link)
                    parsed_link = urlparse(link)
                    # Skip non-HTTP schemes, fragments, empty
                    if parsed_link.scheme not in ("http", "https"):
                        continue
                except Exception:
                    continue

                # Subdomain check
                link_domain = parsed_link.netloc
                if not include_subdomains:
                    if link_domain != base_domain and not link_domain.endswith(f".{base_domain}"):
                        continue

                # Check same-domain (mapping should stay within scope)
                is_same_domain = (link_domain == base_domain) or link_domain.endswith(f".{base_domain}")
                if not is_same_domain:
                    # Add edge but don't traverse
                    edges.append(PageEdge(source=url, target=link))
                    if link not in nodes:
                        nodes[link] = PageNode(url=link)
                    continue

                # Record edge
                edges.append(PageEdge(source=url, target=link))

                # Queue for BFS
                if link not in seen:
                    seen.add(link)
                    if depth < max_depth:
                        queue.append((link, depth + 1))

        return CrawlGraph(
            start_url=start_url,
            nodes=list(nodes.values()),
            edges=edges,
            total_discovered=len(nodes),
            total_crawled=sum(1 for n in nodes.values() if n.title is not None),
        )
