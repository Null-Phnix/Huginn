"""
Huginn Scraper — Single page content extraction.

The /v1/scrape endpoint engine. Uses BrowserManager to load a page,
execute optional actions, then extract content in requested formats.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlparse

from .browser import BrowserManager, WaitStrategy, parse_wait_for
from .models import OutputFormat, ScrapeData

logger = logging.getLogger(__name__)

# ── Retry configuration ──────────────────────────────────────────────────────

DEFAULT_MAX_RETRIES = 2  # Total attempts = max_retries + 1
RETRY_BACKOFFS = [1, 2, 4]  # Seconds: 1s, 2s, 4s (exponential)


def classify_error(error: Exception) -> tuple:
    """Classify an error and return (error_type, http_status_code).

    Used to decide whether to retry and what status code to report.
    """
    if isinstance(error, asyncio.TimeoutError):
        return ("timeout", 408)
    if isinstance(error, (ConnectionRefusedError, ConnectionError, ConnectionResetError)):
        return ("connection", 502)
    if isinstance(error, OSError):
        return ("connection", 502)
    return ("unknown", 500)


class Scraper:
    """Scrapes a single URL and returns content in requested formats."""

    def __init__(self, browser: BrowserManager):
        self.browser = browser

    async def scrape(
        self,
        url: str,
        formats: Optional[List[OutputFormat]] = None,
        headers: Optional[Dict[str, str]] = None,
        wait_for: Optional[Union[int, str]] = None,
        actions: Optional[List[dict]] = None,
        include_tags: Optional[List[str]] = None,
        exclude_tags: Optional[List[str]] = None,
        only_main_content: bool = True,
        timeout: int = 30000,
        proxy: Optional[Dict[str, str]] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> ScrapeData:
        """Scrape a single page with automatic retry on transient errors.

        Retries up to max_retries times with exponential backoff on timeout,
        connection, and server errors. Client errors (4xx) are not retried.
        """
        if formats is None:
            formats = [OutputFormat.MARKDOWN]

        last_error = None
        for attempt in range(max_retries + 1):
            context = None
            try:
                context = await self.browser.new_context(proxy=proxy)
                page = await self.browser.new_page(context)

                # Set extra headers if provided
                if headers:
                    await context.set_extra_http_headers(headers)

                # Navigate to URL
                page.set_default_timeout(timeout)
                success = await self.browser.navigate(page, url)
                if not success:
                    return ScrapeData(metadata={"url": url, "status_code": 500, "error": "Navigation failed"})

                # Smart wait: selector, networkIdle, domContentLoaded, or timeout
                if wait_for:
                    strategy, value = parse_wait_for(wait_for)
                    await self.browser.smart_wait(page, strategy, value, timeout_ms=timeout)

                # Execute pre-extraction actions
                if actions:
                    await self.browser.execute_actions(page, actions)

                # Extract content
                content = await self.browser.extract_content(page)

                # Get actual HTTP status from the navigation response
                status_code = self.browser.last_status_code or 200
                current_url = content.get("url", url)

                result = ScrapeData(metadata={
                    "url": current_url,
                    "title": content.get("title", ""),
                    "description": content.get("description", ""),
                    "language": content.get("language", "en"),
                    "status_code": status_code,
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

            except asyncio.TimeoutError as e:
                error_type, status = classify_error(e)
                last_error = e
                logger.warning(f"Scrape attempt {attempt + 1}/{max_retries + 1} failed for {url}: {error_type} ({status})")
                if attempt < max_retries:
                    backoff = RETRY_BACKOFFS[min(attempt, len(RETRY_BACKOFFS) - 1)]
                    logger.info(f"Retrying in {backoff}s...")
                    await asyncio.sleep(backoff)
                    continue
                return ScrapeData(metadata={"url": url, "error": "Request timed out", "status_code": 408})

            except Exception as e:
                error_type, status = classify_error(e)
                last_error = e
                logger.warning(f"Scrape attempt {attempt + 1}/{max_retries + 1} failed for {url}: {error_type} ({status})")
                # Don't retry client errors or rate limits with no backoff benefit
                if error_type in ("client_error", "rate_limited"):
                    resolved_status = status
                    if self.browser.last_status_code and self.browser.last_status_code > status:
                        resolved_status = self.browser.last_status_code
                    return ScrapeData(metadata={"url": url, "error": str(e), "status_code": resolved_status})
                if attempt < max_retries:
                    backoff = RETRY_BACKOFFS[min(attempt, len(RETRY_BACKOFFS) - 1)]
                    logger.info(f"Retrying in {backoff}s...")
                    await asyncio.sleep(backoff)
                    continue
                # All retries exhausted
                resolved_status = self.browser.last_status_code if self.browser.last_status_code else status
                return ScrapeData(metadata={"url": url, "error": str(e), "status_code": resolved_status})

            finally:
                if context:
                    try:
                        await context.close()
                    except Exception:
                        pass

        # Should never reach here, but guard
        return ScrapeData(metadata={"url": url, "error": str(last_error), "status_code": 500})

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