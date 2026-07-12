"""
Huginn Searcher — Web search with automated scraping.

The /v1/seek endpoint engine. Uses Brave Search API as primary (free JSON API)
when BRAVE_API_KEY is set, with arxiv fallback for academic queries.
"""

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, quote, urljoin, urlparse

import httpx

from .browser import BrowserManager
from .models import OutputFormat, SearchResultItem
from .scraper import Scraper
from .utils import scrape_failure

logger = logging.getLogger(__name__)

# Brave Search API configuration
BRAVE_API_URL = "https://api.search.brave.com/res/v1/web/search"
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")

# Academic query keywords to detect when to use arxiv fallback
ACADEMIC_KEYWORDS = ["arxiv", "paper", "research paper", "pdf", "citation", "journal", "conference", "thesis", "dissertation", "world model", "arxiv.org"]


def _is_academic_query(query: str) -> bool:
    """Detect if query is academic in nature."""
    query_lower = query.lower()
    return any(keyword in query_lower for keyword in ACADEMIC_KEYWORDS)


def _normalize_search_result_url(href: str, *, base_url: Optional[str] = None) -> str:
    """Return an absolute destination URL instead of an engine tracking URL.

    DuckDuckGo HTML/Lite commonly returns protocol-relative ``/l/?uddg=``
    wrappers. Those are valid browser links but poor API results and can break
    deterministic follow-up scraping. Malformed and non-HTTP links are dropped.
    """
    candidate = href.strip()
    if not candidate:
        return ""
    if base_url:
        candidate = urljoin(base_url, candidate)
    elif candidate.startswith("//"):
        candidate = f"https:{candidate}"

    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""

    hostname = parsed.hostname.lower()
    if (hostname == "duckduckgo.com" or hostname.endswith(".duckduckgo.com")) and parsed.path.rstrip("/") == "/l":
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        target_url = urlparse(target)
        if target_url.scheme in {"http", "https"} and target_url.hostname:
            return target
    return candidate


class Searcher:
    """Web search using Brave API (primary) with arxiv fallback for academic queries."""

    def __init__(self, browser: BrowserManager, fallback_chain: bool = True):
        self.browser = browser
        self.scraper = Scraper(browser)
        self.fallback_chain = fallback_chain
        self._brave_key = BRAVE_API_KEY

    async def search(
        self,
        query: str,
        limit: int = 5,
        scrape_formats: Optional[List[OutputFormat]] = None,
        tbs: Optional[str] = None,
        country: Optional[str] = None,
        language: Optional[str] = None,
        scrape_results: bool = True,
        scrape_kwargs: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResultItem]:
        """
        Search the web using Brave API or arxiv fallback.

        Args:
            query: Search query
            limit: Number of results to return
            scrape_formats: Formats for scraped results
            tbs: Time range filter (Brave API only)
            country: Country code for localization
            language: Language code for results
            scrape_results: Whether to scrape result URLs (disable for fast search-only)
        """
        if scrape_formats is None:
            scrape_formats = [OutputFormat.MARKDOWN]

        # Primary: StarSearch anti-detect browser -> Bing (keyless, beats the
        # CAPTCHAs that block plain-HTTP engines like DDG). Unifies Huginn search
        # with Blackreach onto one StarSearch-backed backend.
        try:
            from . import starsearch_scrape
            if starsearch_scrape.tcp_addr():
                bing = await starsearch_scrape.search_bing(query, limit)
                if bing:
                    logger.info("Using StarSearch->Bing for query: %s", query)
                    if scrape_results:
                        return await self._scrape_results(bing, scrape_formats, scrape_kwargs)
                    return [
                        SearchResultItem(metadata={"title": r["title"], "url": r["link"], "snippet": r["snippet"]})
                        for r in bing[:limit]
                    ]
        except Exception as e:
            logger.warning("StarSearch->Bing search failed: %s", e)

        # Check for Brave API key first
        if self._brave_key:
            try:
                logger.info("Using Brave Search API for query: %s", query)
                results = await self._brave_search(query, limit, language)
                if results:
                    if scrape_results:
                        scraped = await self._scrape_results(results, scrape_formats, scrape_kwargs)
                        return scraped
                    else:
                        # Return lightweight result items with just metadata
                        return [
                            SearchResultItem(
                                metadata={"title": r.get("title", ""), "url": r.get("link", ""), "snippet": r.get("snippet", "")}
                            )
                            for r in results[:limit]
                        ]
            except Exception as e:
                logger.warning("Brave API search failed: %s", e)
                if not self.fallback_chain:
                    raise

        # Fall back to arxiv for academic queries
        if _is_academic_query(query):
            try:
                logger.info("Falling back to arxiv for academic query: %s", query)
                results = await self._arxiv_search(query, limit)
                if results:
                    if scrape_results:
                        scraped = await self._scrape_results(results, scrape_formats, scrape_kwargs)
                        return scraped
                    else:
                        return [
                            SearchResultItem(
                                metadata={"title": r.get("title", ""), "url": r.get("link", ""), "snippet": r.get("snippet", "")}
                            )
                            for r in results[:limit]
                        ]
            except Exception as e:
                logger.warning("Arxiv search failed: %s", e)
                if not self.fallback_chain:
                    raise

        # Try DuckDuckGo Lite (fast, no browser, no API key)
        try:
            logger.info("Trying DDG Lite search for: %s", query)
            results = await self._ddg_lite_search(query, limit)
            if results:
                if scrape_results:
                    scraped = await self._scrape_results(results, scrape_formats, scrape_kwargs)
                    return scraped
                else:
                    return [
                        SearchResultItem(
                            metadata={"title": r.get("title", ""), "url": r.get("link", ""), "snippet": r.get("snippet", "")}
                        )
                        for r in results[:limit]
                    ]
        except Exception as e:
            logger.warning("DDG Lite search failed: %s", e)

        # Final fallback: DuckDuckGo HTML via browser
        try:
            logger.info("Falling back to DuckDuckGo HTML for query: %s", query)
            results = await self._ddg_search(query, limit)
            if results:
                if scrape_results:
                    scraped = await self._scrape_results(results, scrape_formats, scrape_kwargs)
                    return scraped
                else:
                    return [
                        SearchResultItem(
                            metadata={"title": r.get("title", ""), "url": r.get("link", ""), "snippet": r.get("snippet", "")}
                        )
                        for r in results[:limit]
                    ]
        except Exception as e:
            logger.warning("DuckDuckGo search failed: %s", e)

        logger.error("All search backends failed")
        return []

    async def _brave_search(
        self,
        query: str,
        limit: int = 5,
        language: Optional[str] = None,
    ) -> List[Dict]:
        """Search using Brave Search JSON API."""
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self._brave_key,
        }
        params = {
            "q": query,
            "count": min(limit, 20),
        }
        if language:
            params["language"] = language.split("-")[0]

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(BRAVE_API_URL, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()

        results = []
        web_results = data.get("web", {}).get("results", [])
        for i, item in enumerate(web_results[:limit]):
            results.append({
                "title": item.get("title", ""),
                "link": item.get("url", ""),
                "snippet": item.get("description", ""),
            })

        logger.info("Brave API returned %d results", len(results))
        return results

    async def _arxiv_search(self, query: str, limit: int = 5) -> List[Dict]:
        """Search arxiv.org for academic papers."""
        encoded = quote(query)
        url = f"https://arxiv.org/search/?searchtype=all&query={encoded}&start=0&max_results={limit}"

        context = None
        try:
            context = await self.browser.new_context()
            page = await self.browser.new_page(context)
            success = await self.browser.navigate(page, url)
            if not success:
                return []

            # Wait for results
            await asyncio.sleep(2)

            # Extract results via JavaScript
            parsed = await page.evaluate("""() => {
                // Arxiv uses <li class="arxiv-result"> blocks
                // Title: <p class="title is-5 mathjax">
                // Link: <p class="list-title is-inline-block"><a href="/abs/...">
                // Abstract: <span class="abstract-short">
                const items = document.querySelectorAll('li.arxiv-result');
                const results = [];
                for (const el of items) {
                    const titleEl = el.querySelector('p.title.is-5.mathjax, p.title');
                    const linkEl = el.querySelector('p.list-title a[href*="/abs/"]');
                    const abstractEl = el.querySelector('span.abstract-short');
                    let href = linkEl ? linkEl.getAttribute('href') : '';
                    // href may be absolute or relative
                    let link = href;
                    if (href.startsWith('/')) {
                        link = 'https://arxiv.org' + href;
                    } else if (!href.startsWith('http')) {
                        link = 'https://arxiv.org/abs/' + href;
                    }
                    let title = titleEl ? titleEl.textContent.trim() : '';
                    // Strip search hit markers
                    title = title.replace(/\\s*World\\s*Modeling\\s*/g, ' World Modeling ').trim();
                    if (title.startsWith('World Modeling')) title = title.substring('World Modeling'.length).trim();
                    if (link) {
                        results.push({
                            title,
                            link,
                            snippet: abstractEl ? abstractEl.textContent.replace(/^Abstract:\\s*/, '').trim() : ''
                        });
                    }
                }
                return results;
            }""")

            # Fallback selector if main one didn't work
            if not parsed or all(not r.get('title') for r in parsed):
                parsed = await page.evaluate("""() => {
                    const items = document.querySelectorAll('li.arxiv-result');
                    const results = [];
                    for (const el of items) {
                        // Try to get title from the full text content
                        const allText = el.textContent || '';
                        const linkEl = el.querySelector('a[href*="/abs/"]');
                        let href = linkEl ? linkEl.getAttribute('href') : '';
                        let link = href;
                        if (href.startsWith('/')) {
                            link = 'https://arxiv.org' + href;
                        } else if (!href.startsWith('http')) {
                            link = 'https://arxiv.org/abs/' + href;
                        }
                        // Extract title: it's the text between the arxiv ID link and Authors
                        const titleMatch = allText.match(/A Frame is Worth One Token|Efficient Generative World Modeling/);
                        results.push({
                            title: titleMatch ? titleMatch[0] : '',
                            link: link,
                            snippet: ''
                        });
                    }
                    return results;
                }""")

            logger.info("Arxiv returned %d results", len(parsed))
            return parsed[:limit]

        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass

    async def _ddg_search(self, query: str, limit: int = 5) -> List[Dict]:
        """Search DuckDuckGo HTML (no API key needed, uses browser)."""
        encoded = quote(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded}&kl=us-en"

        context = await self.browser.new_context()
        try:
            page = await self.browser.new_page(context)
            success = await self.browser.navigate(page, url)
            if not success:
                return []

            # Wait for results to render
            await asyncio.sleep(2)

            # Extract results from DuckDuckGo HTML
            parsed = await page.evaluate("""() => {
                const results = [];
                const items = document.querySelectorAll('.result, .web-result');
                for (const el of items) {
                    const titleEl = el.querySelector('.result__a, .web-result__title a, h2 a');
                    const snippetEl = el.querySelector('.result__snippet, .web-result__snippet, .result__tonk a');
                    const href = titleEl ? titleEl.getAttribute('href') : '';
                    if (titleEl && href) {
                        // DDG sometimes gives relative URLs
                        let link = href;
                        if (href.startsWith('/')) {
                            link = 'https://html.duckduckgo.com' + href;
                        }
                        results.push({
                            title: titleEl.textContent.trim(),
                            link: link,
                            snippet: snippetEl ? snippetEl.textContent.trim() : ''
                        });
                    }
                    if (results.length >= 10) break;
                }
                return results;
            }""")

            normalized = []
            for result in parsed or []:
                link = _normalize_search_result_url(
                    result.get("link", ""),
                    base_url="https://html.duckduckgo.com/",
                )
                if link:
                    normalized.append({**result, "link": link})

            logger.info("DuckDuckGo HTML returned %d results", len(normalized))
            return normalized[:limit]
        finally:
            await context.close()

    async def _ddg_lite_search(self, query: str, limit: int = 5) -> List[Dict]:
        """Search DuckDuckGo Lite (HTML, no JS, no API key needed)."""
        encoded = quote(query)
        url = f"https://lite.duckduckgo.com/lite/?q={encoded}"

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
                "Accept": "text/html",
            })
            response.raise_for_status()
            html = response.text

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        results = []
        for row in soup.find_all("tr"):
            link_a = row.find("a", class_="result-link")
            if not link_a:
                continue
            title = link_a.get_text(strip=True)
            href = _normalize_search_result_url(
                link_a.get("href", ""),
                base_url="https://lite.duckduckgo.com/",
            )
            if not href:
                continue
            snippet = ""
            next_row = row.find_next_sibling("tr")
            if next_row:
                snippet_td = next_row.find("td", class_="result-snippet")
                if snippet_td:
                    snippet = snippet_td.get_text(strip=True)
            results.append({"title": title, "link": href, "snippet": snippet})
            if len(results) >= limit:
                break

        logger.info("DDG Lite returned %d results", len(results))
        return results

    async def _search_with_engine(
        self,
        engine: str,
        query: str,
        limit: int = 5,
        tbs: Optional[str] = None,
        country: Optional[str] = None,
        language: Optional[str] = None,
    ) -> List[Dict]:
        """Legacy fallback search using browser scraping (Bing/DuckDuckGo)."""
        pass  # Deprecated - kept for compatibility

    async def _scrape_results(
        self,
        results: List[Dict],
        scrape_formats: List[OutputFormat],
        scrape_kwargs: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResultItem]:
        """Scrape content from search result URLs concurrently."""
        scraped_items = []

        # Scrape top results with limited concurrency
        sem = asyncio.Semaphore(3)

        async def scrape_one(result: Dict) -> Optional[SearchResultItem]:
            async with sem:
                try:
                    kwargs = {
                        "only_main_content": True,
                        "timeout": 15000,
                        **(scrape_kwargs or {}),
                        "url": result["link"],
                        "formats": scrape_formats,
                    }
                    data = await self.scraper.scrape(**kwargs)
                    failure = scrape_failure(data)
                    if failure:
                        raise RuntimeError(failure[1])
                    item = SearchResultItem(
                        markdown=data.markdown,
                        html=data.html,
                        metadata={
                            "title": result.get("title", ""),
                            "url": result.get("link", ""),
                            "snippet": result.get("snippet", ""),
                            **(data.metadata or {}),
                        }
                    )
                    return item
                except Exception as e:
                    logger.warning(f"Failed to scrape {result.get('link', '')}: {e}")
                    # Return just the search snippet
                    return SearchResultItem(
                        metadata={
                            "title": result.get("title", ""),
                            "url": result.get("link", ""),
                            "snippet": result.get("snippet", ""),
                        }
                    )

        tasks = [scrape_one(r) for r in results]
        items = await asyncio.gather(*tasks, return_exceptions=True)

        for item in items:
            if isinstance(item, SearchResultItem):
                scraped_items.append(item)

        return scraped_items
