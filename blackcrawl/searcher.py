"""
BlackCrawl Searcher — Web search with automated scraping.

The /v1/search endpoint engine. Uses Blackreach's fallback search chain
(Bing -> DuckDuckGo -> Brave) with automatic result scraping.
"""

import asyncio
import logging
import re
from typing import Dict, List, Optional
from urllib.parse import quote, urlparse

import httpx

from .browser import BrowserManager
from .models import OutputFormat, SearchResultItem, ScrapeData
from .scraper import Scraper

logger = logging.getLogger(__name__)

# Search engine configurations
SEARCH_ENGINES = {
    "bing": {
        "url": "https://www.bing.com/search?q={query}&count={limit}",
        "result_selector": "li.b_algo",
        "title_selector": "h2 a",
        "link_selector": "h2 a",
        "snippet_selector": ".b_caption p, p",
    },
    "duckduckgo": {
        "url": "https://html.duckduckgo.com/html/?q={query}",
        "result_selector": ".result",
        "title_selector": ".result__a",
        "link_selector": ".result__a",
        "snippet_selector": ".result__snippet",
    },
    "brave": {
        "url": "https://search.brave.com/search?q={query}&count={limit}",
        "result_selector": ".snippet",
        "title_selector": ".snippet-title",
        "link_selector": "a.result-header",
        "snippet_selector": ".snippet-description",
    },
}


class Searcher:
    """Web search with automated result scraping and fallback chain."""

    def __init__(self, browser: BrowserManager, fallback_chain: bool = True):
        self.browser = browser
        self.scraper = Scraper(browser)
        self.fallback_chain = fallback_chain

    async def search(
        self,
        query: str,
        limit: int = 5,
        scrape_formats: Optional[List[OutputFormat]] = None,
        tbs: Optional[str] = None,
        country: Optional[str] = None,
        language: Optional[str] = None,
    ) -> List[SearchResultItem]:
        """
        Search the web and scrape results.

        Args:
            query: Search query
            limit: Number of results to return
            scrape_formats: Formats for scraped results
            tbs: Time range filter
            country: Country code for localization
            language: Language code for results
            fallback_chain: Use fallback search engines if primary fails
        """
        if scrape_formats is None:
            scrape_formats = [OutputFormat.MARKDOWN]

        # Try search engines in order with fallback
        engines = list(SEARCH_ENGINES.keys())

        for engine_name in engines:
            try:
                logger.info(f"Trying search engine: {engine_name}")
                results = await self._search_with_engine(
                    engine_name, query, limit, tbs, country, language
                )

                if results:
                    # Scrape each result page
                    scraped = await self._scrape_results(results, scrape_formats)
                    return scraped

            except Exception as e:
                logger.warning(f"Search engine {engine_name} failed: {e}")
                if not self.fallback_chain:
                    raise
                continue

        logger.error("All search engines failed")
        return []

    async def _search_with_engine(
        self,
        engine: str,
        query: str,
        limit: int = 5,
        tbs: Optional[str] = None,
        country: Optional[str] = None,
        language: Optional[str] = None,
    ) -> List[Dict]:
        """Execute search using a specific engine and parse results."""
        config = SEARCH_ENGINES[engine]
        encoded_query = quote(query)
        url = config["url"].format(query=encoded_query, limit=limit)

        # Add time range
        if tbs:
            url += f"&tbs={tbs}"

        context = None
        try:
            context = await self.browser.new_context()
            page = await self.browser.new_page(context)

            # Set language preference
            if language:
                await context.set_extra_http_headers({
                    "Accept-Language": f"{language},{language.split('-')[0]};q=0.9"
                })

            success = await self.browser.navigate(page, url)
            if not success:
                return []

            # Wait for results to load
            await asyncio.sleep(2)  # Let dynamic content render

            # Parse search results
            results = await page.evaluate("""(config) => {
                const resultElements = document.querySelectorAll(config.result_selector);
                const results = [];

                for (const el of resultElements) {
                    let title = '';
                    let link = '';
                    let snippet = '';

                    const titleEl = el.querySelector(config.title_selector);
                    if (titleEl) title = titleEl.textContent.trim();

                    const linkEl = el.querySelector(config.link_selector);
                    if (linkEl) {
                        link = linkEl.href || linkEl.getAttribute('href') || '';
                        // DuckDuckGo uses redirect URLs
                        if (link.includes('uddg=')) {
                            try {
                                const urlParams = new URLSearchParams(link);
                                link = decodeURIComponent(urlParams.get('uddg') || link);
                            } catch {}
                        }
                    }

                    const snippetEl = el.querySelector(config.snippet_selector);
                    if (snippetEl) snippet = snippetEl.textContent.trim();

                    if (title && link && !link.includes('javascript:')) {
                        results.push({ title, link, snippet });
                    }
                }

                return results;
            }""", {
                "result_selector": config["result_selector"],
                "title_selector": config["title_selector"],
                "link_selector": config["link_selector"],
                "snippet_selector": config["snippet_selector"],
            })

            # Filter valid URLs
            valid_results = []
            for r in results[:limit]:
                if r.get("link") and r["link"].startswith("http"):
                    valid_results.append(r)

            logger.info(f"{engine}: found {len(valid_results)} results")
            return valid_results

        except Exception as e:
            logger.error(f"Search with {engine} failed: {e}")
            return []
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass

    async def _scrape_results(
        self,
        results: List[Dict],
        scrape_formats: List[OutputFormat],
    ) -> List[SearchResultItem]:
        """Scrape content from search result URLs concurrently."""
        scraped_items = []

        # Scrape top results with limited concurrency
        sem = asyncio.Semaphore(3)

        async def scrape_one(result: Dict) -> Optional[SearchResultItem]:
            async with sem:
                try:
                    data = await self.scraper.scrape(
                        url=result["link"],
                        formats=scrape_formats,
                        only_main_content=True,
                        timeout=15000,
                    )
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

        tasks = [scrape_one(r) for r in results[:len(results)]]
        items = await asyncio.gather(*tasks, return_exceptions=True)

        for item in items:
            if isinstance(item, SearchResultItem):
                scraped_items.append(item)

        return scraped_items