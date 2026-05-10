"""
Huginn Mapper — Fast URL discovery without full content extraction.

The /v1/map endpoint engine. Uses DOM walker's link extraction and
optional sitemap parsing for rapid site mapping.
"""

import asyncio
import logging
import re
from typing import List, Optional, Set
from urllib.parse import urlparse, urljoin
logger = logging.getLogger(__name__)

try:
    from defusedxml import ElementTree as ElementTree
except ImportError:
    # Fallback: disable entity expansion as a basic defense
    from xml.etree import ElementTree
    import xml.sax
    xml.sax.make_parser = lambda *a, **kw: xml.sax.make_parser.__wrapped__(*a, **kw) if hasattr(xml.sax.make_parser, '__wrapped__') else xml.sax.make_parser()
    logger.warning("defusedxml not installed — XML parsing vulnerable to bombs. pip install defusedxml")

import httpx

from .browser import BrowserManager

logger = logging.getLogger(__name__)


class Mapper:
    """Fast URL mapper: discovers all URLs on a site without full content extraction."""

    def __init__(self, browser: BrowserManager):
        self.browser = browser

    async def chart_site(self, *args, **kwargs):
        """Alias for map_site — used by /v1/chart endpoint."""
        return await self.map_site(*args, **kwargs)

    async def map_site(
        self,
        url: str,
        search: Optional[str] = None,
        include_subdomains: bool = False,
        limit: int = 5000,
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
        """
        all_urls: Set[str] = set()
        parsed_start = urlparse(url)
        base_domain = parsed_start.netloc

        # Strategy 1: Try sitemap.xml first (fast, no browser needed)
        sitemap_urls = await self._fetch_sitemap(url)
        if sitemap_urls:
            logger.info(f"Sitemap: found {len(sitemap_urls)} URLs")
            all_urls.update(sitemap_urls)

        # Strategy 2: Load the page and extract all links via DOM walker
        page_links, _title, _status = await self._extract_page_links(url, include_subdomains, base_domain)
        if page_links:
            logger.info(f"Page extraction: found {len(page_links)} URLs")
            all_urls.update(page_links)

        # Strategy 3: If we found URLs on the page, check if they have their own sitemaps
        # (limited to avoid recursion)
        if len(all_urls) < limit:
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
        """Load page in browser and extract all links + metadata."""
        context = None
        try:
            context = await self.browser.new_context()
            page = await self.browser.new_page(context)

            success = await self.browser.navigate(page, url)
            if not success:
                return (set(), None, None)

            title = await page.title()
            status_code = self.browser.last_status_code or 200

            raw_links = await page.evaluate("""() => {
                const links = new Set();
                document.querySelectorAll('a[href]').forEach(a => {
                    const href = a.href;
                    if (href && !href.startsWith('javascript:') && !href.startsWith('#') && !href.startsWith('mailto:')) {
                        links.add(href);
                    }
                });
                return Array.from(links);
            }""")

            return (set(raw_links), title, status_code)

        except Exception as e:
            logger.error(f"Page link extraction failed for {url}: {e}")
            return (set(), None, None)
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass

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
        from .models import CrawlGraph, PageNode, PageEdge

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

        from .models import CrawlGraph
        return CrawlGraph(
            start_url=start_url,
            nodes=list(nodes.values()),
            edges=edges,
            total_discovered=len(nodes),
            total_crawled=sum(1 for n in nodes.values() if n.title is not None),
        )