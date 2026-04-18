"""
BlackCrawl Scraper — Single page content extraction.

The /v1/scrape endpoint engine. Uses BrowserManager to load a page,
execute optional actions, then extract content in requested formats.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .browser import BrowserManager
from .models import OutputFormat, ScrapeData

logger = logging.getLogger(__name__)


class Scraper:
    """Scrapes a single URL and returns content in requested formats."""

    def __init__(self, browser: BrowserManager):
        self.browser = browser

    async def scrape(
        self,
        url: str,
        formats: Optional[List[OutputFormat]] = None,
        headers: Optional[Dict[str, str]] = None,
        wait_for: Optional[int] = None,
        actions: Optional[List[dict]] = None,
        include_tags: Optional[List[str]] = None,
        exclude_tags: Optional[List[str]] = None,
        only_main_content: bool = True,
        timeout: int = 30000,
        proxy: Optional[Dict[str, str]] = None,
    ) -> ScrapeData:
        """Scrape a single page and return content in requested formats."""
        if formats is None:
            formats = [OutputFormat.MARKDOWN]

        context = await self.browser.new_context(proxy=proxy)
        page = await self.browser.new_page(context)

        try:
            # Set extra headers if provided
            if headers:
                await context.set_extra_http_headers(headers)

            # Navigate to URL
            page.set_default_timeout(timeout)
            success = await self.browser.navigate(page, url)
            if not success:
                return ScrapeData(metadata={"url": url, "statusCode": 500, "error": "Navigation failed"})

            # Wait for specific selector or time
            if wait_for:
                await asyncio.sleep(wait_for / 1000)

            # Execute pre-extraction actions
            if actions:
                await self.browser.execute_actions(page, actions)

            # Extract content
            content = await self.browser.extract_content(page)

            # Get response status
            status_code = 200
            current_url = content.get("url", url)

            result = ScrapeData(metadata={
                "url": current_url,
                "title": content.get("title", ""),
                "description": content.get("description", ""),
                "language": content.get("language", "en"),
                "statusCode": status_code,
            })

            # Extract in requested formats
            for fmt in formats:
                if fmt == OutputFormat.MARKDOWN:
                    result.markdown = await self.browser.to_markdown(page, content)
                elif fmt == OutputFormat.HTML:
                    result.html = await self.browser.to_html(page, only_main=only_main_content)
                elif fmt == OutputFormat.RAW_HTML:
                    result.raw_html = await page.content()
                elif fmt == OutputFormat.SCREENSHOT:
                    result.screenshot = await self.browser.take_screenshot(page)
                elif fmt == OutputFormat.LINKS:
                    result.links = await self.browser.get_links(page, base_url=url)
                elif fmt == OutputFormat.METADATA:
                    # Already collected in metadata
                    pass

            # Apply include/exclude tag filtering on HTML
            if include_tags or exclude_tags:
                result = await self._filter_tags(page, result, include_tags, exclude_tags)

            # Truncate very long content to prevent memory issues
            if result.markdown and len(result.markdown) > 500_000:
                logger.warning(f"Truncating large markdown content ({len(result.markdown)} chars)")
                result.markdown = result.markdown[:500_000]

            return result

        except Exception as e:
            logger.error(f"Scrape failed for {url}: {e}")
            return ScrapeData(metadata={"url": url, "error": str(e), "statusCode": 500})
        finally:
            try:
                await context.close()
            except Exception:
                pass

    async def _filter_tags(
        self,
        page,
        result: ScrapeData,
        include_tags: Optional[List[str]],
        exclude_tags: Optional[List[str]],
    ) -> ScrapeData:
        """Filter HTML content based on include/exclude tag selectors."""
        if include_tags:
            # Keep only elements matching include selectors
            filtered_html = await page.evaluate("""(selectors) => {
                const results = [];
                for (const sel of selectors) {
                    document.querySelectorAll(sel).forEach(el => {
                        results.push(el.outerHTML);
                    });
                }
                return results.join('\\n');
            }""", include_tags)
            if result.html:
                result.html = filtered_html
            if result.markdown:
                # Re-convert the filtered HTML
                result.markdown = await page.evaluate("""(selectors) => {
                    const results = [];
                    for (const sel of selectors) {
                        document.querySelectorAll(sel).forEach(el => {
                            results.push(el.textContent);
                        });
                    }
                    return results.join('\\n\\n');
                }""", include_tags)

        if exclude_tags:
            # Remove elements matching exclude selectors
            if result.html:
                filtered_html = await page.evaluate("""(selectors) => {
                    const clone = document.querySelector('main') ||
                                 document.querySelector('article') ||
                                 document.body;
                    const el = clone.cloneNode(true);
                    for (const sel of selectors) {
                        el.querySelectorAll(sel).forEach(e => e.remove());
                    }
                    return el.innerHTML;
                }""", exclude_tags)
                result.html = filtered_html

        return result