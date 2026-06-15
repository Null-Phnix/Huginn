"""
Huginn Scraper — Single page content extraction.

The /v1/scrape endpoint engine. Uses BrowserManager to load a page,
execute optional actions, then extract content in requested formats.
"""

import asyncio
import logging
import re
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlparse

import httpx
import markdownify
from bs4 import BeautifulSoup, Tag

from .browser import BrowserManager, WaitStrategy, parse_wait_for
from .circuit_breaker import CircuitBreaker, CircuitOpenError, extract_domain, get_circuit_breaker
from .domain_rate_limiter import DomainRateLimiter, get_domain_rate_limiter
from .models import OutputFormat, ScrapeData
from .pdf import extract_pdf_text, is_pdf_content

logger = logging.getLogger(__name__)


# ─── Mobile device emulation (Firecrawl parity) ────────────────────────────────
# iPhone 13 device descriptor for mobile=True. Matches the official
# playwright.devices["iPhone 13"] descriptor so behavior is identical whether
# we use Playwright's lookup or this hand-rolled fallback.
#
# We keep the dict at module level so it's looked up once at import time and
# re-used across all scrape() calls (no per-request device-table lookup).

_FALLBACK_IPHONE_DEVICE: Dict[str, Any] = {
    "is_mobile": True,
    "has_touch": True,
    "device_scale_factor": 3,
    "viewport": {"width": 390, "height": 844},
    "user_agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
    ),
    "is_ios": True,
    "locale": "en-US",
}

_MOBILE_DEVICE: Dict[str, Any] = _FALLBACK_IPHONE_DEVICE


# ─── Ad blocking (Firecrawl parity) ────────────────────────────────────────────
# Well-known ad network domains. When blockAds=True, the Scraper registers
# a Playwright route handler for each domain that aborts matching requests
# before they reach the page. Wildcard globs (**) match any path under the
# domain so ad creatives, beacons, and pixels are all caught.
#
# Sources: EasyPrivacy list, Firecrawl default, and the most common
# doubleclick / googlesyndication / amazon-aax / criteo endpoints.

_AD_DOMAINS: List[str] = [
    "**/doubleclick.net/**",
    "**/googlesyndication.com/**",
    "**/googleadservices.com/**",
    "**/googletagservices.com/**",
    "**/google-analytics.com/**",
    "**/googletagmanager.com/**",
    "**/adsystem.amazon.com/**",
    "**/amazon-adsystem.com/**",
    "**/criteo.com/**",
    "**/criteo.net/**",
    "**/outbrain.com/**",
    "**/taboola.com/**",
    "**/scorecardresearch.com/**",
    "**/adsrvr.org/**",
    "**/adnxs.com/**",
    "**/rubiconproject.com/**",
    "**/pubmatic.com/**",
    "**/openx.net/**",
    "**/moatads.com/**",
    "**/facebook.com/tr/**",
    "**/connect.facebook.net/**",
    "**/analytics.twitter.com/**",
    "**/ads.yahoo.com/**",
    "**/ads.linkedin.com/**",
    "**/adform.net/**",
]


# ─── Base64 image stripping (Firecrawl parity) ────────────────────────────────
# Matches `data:image/<type>;base64,<payload>` URIs. Captures the full URI
# so .sub("", md) strips it entirely. The payload is non-greedy and stops
# at any of: whitespace, ), ", ', <, >, end of string.
#
# Examples that match:
#   data:image/png;base64,iVBOR...
#   data:image/jpeg;base64,/9j/4AAQ...
#   data:image/svg+xml;base64,PHN2Zy...
#   data:image/webp;base64,UklGR...
#
# Examples that DO NOT match:
#   data:application/octet-stream;base64,XXXX (intentionally — only images)
#   data:text/plain,Hello (no ;base64 segment)

_BASE64_IMAGE_REGEX = re.compile(
    r"data:image/[a-zA-Z0-9+.\-]+;base64,[^\s\)\"'<>]+",
    re.IGNORECASE,
)


def _is_valid_http_url(url: str) -> bool:
    """Return True if `url` is a syntactically valid http(s) URL.

    Used by FlockRequest / batch_scrape to decide whether to skip an
    invalid URL (when ignoreInvalidURLs=True) or 422 the request.
    """
    if not isinstance(url, str) or not url.strip():
        return False
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.netloc:
        return False
    return True


class RenderMode(str, Enum):
    """Rendering mode for page content extraction."""
    AUTO = "auto"    # Detect automatically
    FULL = "full"    # Always use full browser (Playwright)
    LIGHT = "light"  # Always use lightweight (httpx + markdownify)


# ── JS framework detection patterns ──────────────────────────────────────────

JS_INDICATOR_HEADERS = {
    "x-nextjs-cache", "x-nextjs-matched-path",
    "x-powered-by",  # Often reveals Next.js, Nuxt, etc.
    "cf-ray",         # Cloudflare
}

JS_INDICATOR_SERVER = {"cloudflare", "vercel", "netlify", "ampproject"}

JS_INDICATOR_PREFIXES = {"x-vercel", "x-next", "x-nuxt", "x-vite"}

# Very small HTML responses are likely SPA shells
MIN_CONTENT_FOR_STATIC = 2000  # bytes


def detect_render_mode(
    url: str,
    headers: Dict[str, str],
    force: Optional[RenderMode] = None,
) -> RenderMode:
    """Detect whether a page needs full browser rendering.

    Uses HTTP response headers and content-length to decide:
    - Static pages with substantial content → LIGHT
    - JS-heavy pages (SPA shells, Cloudflare, Next.js) → FULL
    - Force override if provided

    Args:
        url: The page URL (used to detect SPA patterns like hash routing).
        headers: HTTP response headers from the initial request.
        force: If set, overrides detection and returns this mode directly.

    Returns:
        RenderMode.LIGHT or RenderMode.FULL
    """
    if force is not None:
        return force

    # Check for JS framework indicators in headers
    header_keys_lower = {k.lower(): v for k, v in headers.items()}

    # Cloudflare-protected pages need full browser
    server = header_keys_lower.get("server", "").lower()
    if any(js_srv in server for js_srv in JS_INDICATOR_SERVER):
        return RenderMode.FULL

    # Check for JS framework header prefixes
    for h in headers:
        h_lower = h.lower()
        for prefix in JS_INDICATOR_PREFIXES:
            if h_lower.startswith(prefix):
                return RenderMode.FULL

    # Check for known JS framework headers
    for h in headers:
        if h.lower() in JS_INDICATOR_HEADERS:
            return RenderMode.FULL

    # Check content-length — very small HTML is likely a SPA shell
    content_length = header_keys_lower.get("content-length")
    if content_length:
        try:
            if int(content_length) < MIN_CONTENT_FOR_STATIC:
                return RenderMode.FULL
        except (ValueError, TypeError):
            pass

    # Check for non-HTML content types (PDF, etc.) that need full processing
    content_type = header_keys_lower.get("content-type", "").lower()
    if content_type and not content_type.startswith("text/html"):
        # Non-HTML content (PDF, images, etc.) requires full browser to handle
        return RenderMode.FULL

    # Check URL for SPA hash routing
    if "/#" in url:
        return RenderMode.FULL

    # Default: static content, use lightweight
    return RenderMode.LIGHT

# ── Retry configuration ──────────────────────────────────────────────────────

DEFAULT_MAX_RETRIES = 2  # Total attempts = max_retries + 1
RETRY_BACKOFFS = [1, 2, 4, 8, 16, 32]  # Seconds: exponential up to 32s


def classify_error(error: Exception) -> tuple:
    """Classify an error and return (error_type, http_status_code).

    Used to decide whether to retry and what status code to report.
    """
    msg = str(error).lower()

    # CAPTCHA / bot detection indicators (check message text first)
    captcha_indicators = [
        "captcha", "recaptcha", "hcaptcha", "please verify", "are you human",
        "verification required", "challenge", "blocked by", "automated access",
        "bot detected", "unusual traffic",
    ]
    for indicator in captcha_indicators:
        if indicator in msg:
            return ("captcha", 403)

    # Paywall / auth wall indicators
    paywall_indicators = [
        "subscribe", "subscription required", "login required", "sign in to",
        "premium content", "members only", "paywall",
    ]
    for indicator in paywall_indicators:
        if indicator in msg:
            return ("paywall", 402)

    if isinstance(error, asyncio.TimeoutError):
        return ("timeout", 408)
    if isinstance(error, (ConnectionRefusedError, ConnectionError, ConnectionResetError)):
        return ("connection", 502)
    if isinstance(error, OSError):
        return ("connection", 502)
    # 429 and 503 are retriable; 403/401/402 generally are not
    if "429" in msg or "too many requests" in msg:
        return ("rate_limited", 429)
    if "503" in msg or "service unavailable" in msg:
        return ("server_error", 503)
    if "502" in msg or "bad gateway" in msg:
        return ("server_error", 502)
    if "504" in msg or "gateway timeout" in msg:
        return ("server_error", 504)
    return ("unknown", 500)


class Scraper:
    """Scrapes a single URL and returns content in requested formats.

    Uses a persistent httpx client for connection pooling on lightweight
    fetches. Call ``await scraper.close()`` when done to release resources.
    """

    def __init__(
        self,
        browser: BrowserManager,
        circuit_breaker: Optional[CircuitBreaker] = None,
        rate_limiter: Optional[DomainRateLimiter] = None,
        proxy_pool: Optional[List[Dict[str, str]]] = None,
    ):
        self.browser = browser
        self._cb = circuit_breaker
        self._rl = rate_limiter
        self._proxy_pool = proxy_pool or []
        self._cb_domain_failures: dict[str, int] = {}  # domain -> consecutive failures
        # Persistent HTTP client for connection pooling
        self._http_client: Optional[httpx.AsyncClient] = None

    def _get_http_client(self) -> httpx.AsyncClient:
        """Lazy-init shared httpx client with keep-alive / connection pooling."""
        if self._http_client is None:
            limits = httpx.Limits(
                max_keepalive_connections=20,
                max_connections=50,
                keepalive_expiry=30.0,
            )
            self._http_client = httpx.AsyncClient(
                limits=limits,
                follow_redirects=True,
                timeout=httpx.Timeout(30.0, connect=5.0),
            )
        return self._http_client

    async def close(self) -> None:
        """Close persistent HTTP client. Safe to call multiple times."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

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
        scroll: bool = False,
        render_mode: str = "auto",
        skip_tls_verification: bool = True,
        summary: bool = False,
        mobile: bool = False,
        block_ads: bool = False,
        remove_base64_images: bool = False,
    ) -> ScrapeData:
        """Scrape a single page with automatic retry on transient errors.

        Retries up to max_retries times with exponential backoff on timeout,
        connection, and server errors. Client errors (4xx) are not retried.

        Per-domain rate limiting waits politely before requesting.
        The circuit breaker (if configured) prevents hammering domains that are
        returning persistent errors, protecting both the target and our resources.

        ``skip_tls_verification`` defaults to True for Firecrawl parity
        + backward compat with Huginn's historical hardcoded
        ``ignore_https_errors=True``. Pass False for stricter TLS
        verification (e.g. when scraping sites you don't control and
        don't want to be MITM'd).
        """
        if formats is None:
            formats = [OutputFormat.MARKDOWN]

        domain = extract_domain(url)
        cb = self._cb or get_circuit_breaker()
        rl = self._rl or get_domain_rate_limiter()

        # 1. Rate limit: acquire token (waits if bucket empty)
        wait_time = await rl.acquire_or_wait(domain)
        if wait_time > 1.0:
            logger.info(f"Rate-limited on {domain}, waited {wait_time:.1f}s before scraping {url}")

        # 2. Circuit breaker check
        if cb.is_open(domain):
            logger.debug(f"Circuit open for {domain}, returning fast-fail for {url}")
            return ScrapeData(
                metadata={
                    "url": url,
                    "error": f"Circuit breaker open for {domain}. Site may be temporarily unavailable.",
                    "status_code": 503,
                    "circuit_open": True,
                }
            )

        # 3. Wrap the actual scrape in the circuit breaker
        try:
            return await cb.call(
                domain,
                self._scrape_impl,
                url, formats, headers,
                wait_for, actions, include_tags, exclude_tags,
                only_main_content, timeout, proxy, max_retries, scroll,
                render_mode, skip_tls_verification, summary, mobile,
                block_ads, remove_base64_images,
            )
        except CircuitOpenError:
            return ScrapeData(
                metadata={
                    "url": url,
                    "error": f"Circuit breaker open for {domain}. Site may be temporarily unavailable.",
                    "status_code": 503,
                    "circuit_open": True,
                }
            )
        except Exception as e:
            # Record the failure so circuit breaker tracks it
            await cb.record_failure(domain)
            raise

    async def _scrape_impl(
        self,
        url: str,
        formats: Optional[List[OutputFormat]],
        headers: Optional[Dict[str, str]],
        wait_for: Optional[Union[int, str]],
        actions: Optional[List[dict]],
        include_tags: Optional[List[str]],
        exclude_tags: Optional[List[str]],
        only_main_content: bool,
        timeout: int,
        proxy: Optional[Dict[str, str]],
        max_retries: int,
        scroll: bool,
        render_mode: str,
        skip_tls_verification: bool,
        summary: bool = False,
        mobile: bool = False,
        block_ads: bool = False,
        remove_base64_images: bool = False,
    ) -> ScrapeData:
        """Internal implementation — wrapped by circuit breaker."""

        # ── Lightweight rendering path ────────────────────────────────────
        # If render_mode is "light" or "auto", try lightweight HTTP fetch first.
        # Fall back to full browser if content is too thin or if mode is "auto"
        # and detection suggests JS is needed.
        mode = RenderMode(render_mode)
        if mode != RenderMode.FULL:
            try:
                # Quick HEAD request to detect JS requirements
                client = self._get_http_client()
                head_resp = await client.head(
                    url,
                    headers={"User-Agent": "Huginn/Bot (+https://huginn.dev/bot)"},
                )
                head_headers = dict(head_resp.headers)

                detected = detect_render_mode(url, head_headers, force=mode)

                if detected == RenderMode.LIGHT:
                    result = await self.lightweight_scrape(
                        url=url,
                        formats=formats,
                        only_main_content=only_main_content,
                        timeout=timeout,
                        headers=headers,
                    )
                    # Check if content is thin — might need JS after all
                    if result.markdown and len(result.markdown) > 200:
                        return result
                    elif not result.markdown:
                        # Empty result — fall through to full browser
                        logger.info(f"Lightweight scrape returned empty for {url}, falling back to full browser")
                    else:
                        logger.info(f"Lightweight scrape returned thin content ({len(result.markdown)} chars), falling back to full browser")
            except Exception as e:
                logger.warning(f"Lightweight scrape failed for {url}: {e}, falling back to full browser")
        # Fall through to full browser rendering for FULL mode or failed light mode
        # ── Full browser rendering path ─────────────────────────────────

        last_error = None
        pdf_text: Optional[str] = None  # extracted if page is PDF
        # Firecrawl parity: skip TLS certificate verification.
        # Stored on BrowserManager so new_context() can read it via getattr()
        # without needing the param threaded through every call site.
        self.browser.ignore_https_errors = skip_tls_verification
        # Firecrawl parity: mobile device emulation. When mobile=True,
        # pass the iPhone 13 device descriptor to Playwright's
        # new_context() so the browser uses mobile viewport / UA / touch.
        device = _MOBILE_DEVICE if mobile else None
        for attempt in range(max_retries + 1):
            context = None
            try:
                context = await self.browser.new_context(proxy=proxy, device=device)
                page = await self.browser.new_page(context)

                # Firecrawl parity: block ad network requests at the network
                # layer. Each ad domain gets a route handler that aborts the
                # request before it reaches the page — saves bandwidth + speeds
                # up the scrape. We register synchronously here so the routes
                # are active before any subresource loads.
                if block_ads:
                    async def _abort_route(route):
                        await route.abort()
                    for pattern in _AD_DOMAINS:
                        # MagicMock-based tests don't await page.route(); in real
                        # Playwright the call returns a coroutine, so we await it.
                        route_call = page.route(pattern, _abort_route)
                        if asyncio.iscoroutine(route_call):
                            await route_call

                # Set extra headers if provided
                if headers:
                    await context.set_extra_http_headers(headers)

                # Navigate to URL
                # set_default_timeout is sync in Playwright; suppress mock coroutine warning
                result = page.set_default_timeout(timeout)
                if asyncio.iscoroutine(result):
                    await result
                success = await self.browser.navigate(page, url)
                if not success:
                    return ScrapeData(metadata={"url": url, "status_code": 500, "error": "Navigation failed"})

                # Check if response is a PDF -- use PDF extractor instead
                try:
                    resp = page.url  # Current URL after navigation
                    content_type = ""
                    try:
                        resp_obj = await page.evaluate("() => document.contentType || ''")
                        content_type = resp_obj or ""
                    except Exception:
                        pass

                    if url.lower().endswith(".pdf") or "application/pdf" in content_type:
                        logger.info(f"PDF detected for {url}, using PDF extractor")
                        # Download PDF bytes via persistent client
                        pdf_client = self._get_http_client()
                        pdf_resp = await pdf_client.get(url)
                        if pdf_resp.status_code == 200:
                            pdf_text = await extract_pdf_text(pdf_resp.content)
                            return ScrapeData(
                                markdown=pdf_text,
                                metadata={
                                    "url": url,
                                    "title": url.split("/")[-1],
                                    "status_code": pdf_resp.status_code,
                                    "content_type": "pdf",
                                },
                            )
                except Exception as e:
                    logger.warning(f"PDF extraction attempt failed for {url}: {e}")
                    # Fall through to normal HTML extraction

                # Smart wait: selector, networkIdle, domContentLoaded, or timeout
                if wait_for:
                    strategy, value = parse_wait_for(wait_for)
                    await self.browser.smart_wait(page, strategy, value, timeout_ms=timeout)

                # Execute pre-extraction actions
                if actions:
                    await self.browser.execute_actions(page, actions)

                # Auto-scroll for dynamic content
                if scroll:
                    await self.browser.auto_scroll(page)

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
                        # Firecrawl parity: strip inline base64 image data URIs
                        # from the extracted markdown. Pages with inlined SVG or
                        # canvas screenshots can blow up token counts and payload
                        # size; this is a one-line post-process to clean them up.
                        if remove_base64_images and result.markdown:
                            result.markdown = _BASE64_IMAGE_REGEX.sub("", result.markdown)
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
                    elif fmt == OutputFormat.PDF:
                        result.pdf_text = pdf_text

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
                    # Rotate proxy if pool available
                    if self._proxy_pool:
                        proxy = self._proxy_pool[attempt % len(self._proxy_pool)]
                        logger.info(f"Rotating to proxy {proxy.get('server', 'unknown')} for retry")
                    logger.info(f"Retrying in {backoff}s...")
                    await asyncio.sleep(backoff)
                    continue
                return ScrapeData(metadata={"url": url, "error": "Request timed out", "status_code": 408, "error_type": error_type})

            except Exception as e:
                error_type, status = classify_error(e)
                last_error = e
                logger.warning(f"Scrape attempt {attempt + 1}/{max_retries + 1} failed for {url}: {error_type} ({status})")
                # Don't retry captcha, paywall, or client errors
                if error_type in ("captcha", "paywall", "client_error"):
                    resolved_status = self.browser.last_status_code if self.browser.last_status_code else status
                    return ScrapeData(
                        metadata={
                            "url": url,
                            "error": str(e),
                            "status_code": resolved_status,
                            "error_type": error_type,
                        }
                    )
                if error_type == "rate_limited":
                    resolved_status = self.browser.last_status_code if self.browser.last_status_code else status
                    # Rate limited: still retry, but with longer backoff and proxy rotation
                    if attempt < max_retries:
                        backoff = RETRY_BACKOFFS[min(attempt + 1, len(RETRY_BACKOFFS) - 1)]
                        if self._proxy_pool:
                            proxy = self._proxy_pool[attempt % len(self._proxy_pool)]
                            logger.info(f"Rate-limited; rotating to proxy {proxy.get('server', 'unknown')} and waiting {backoff}s")
                        logger.info(f"Rate-limited; retrying in {backoff}s...")
                        await asyncio.sleep(backoff)
                        continue
                    return ScrapeData(
                        metadata={
                            "url": url,
                            "error": str(e),
                            "status_code": resolved_status,
                            "error_type": error_type,
                        }
                    )
                if attempt < max_retries:
                    backoff = RETRY_BACKOFFS[min(attempt, len(RETRY_BACKOFFS) - 1)]
                    if self._proxy_pool:
                        proxy = self._proxy_pool[attempt % len(self._proxy_pool)]
                        logger.info(f"Rotating to proxy {proxy.get('server', 'unknown')} for retry")
                    logger.info(f"Retrying in {backoff}s...")
                    await asyncio.sleep(backoff)
                    continue
                # All retries exhausted
                resolved_status = self.browser.last_status_code if self.browser.last_status_code else status
                return ScrapeData(
                    metadata={
                        "url": url,
                        "error": str(e),
                        "status_code": resolved_status,
                        "error_type": error_type,
                    }
                )

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
    async def lightweight_scrape(
        self,
        url: str,
        formats: Optional[List[OutputFormat]] = None,
        only_main_content: bool = True,
        timeout: int = 15000,
        headers: Optional[Dict[str, str]] = None,
    ) -> ScrapeData:
        """Lightweight page extraction using httpx + markdownify.

        Skips full browser rendering. Much faster for static pages.
        Falls back gracefully if content is thin.
        """
        if formats is None:
            formats = [OutputFormat.MARKDOWN]

        request_headers = {
            "User-Agent": "Huginn/Bot (+https://huginn.dev/bot)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if headers:
            request_headers.update(headers)

        client = self._get_http_client()
        resp = await client.get(url, headers=request_headers)
        resp.raise_for_status()
        html_content = resp.text

        # Parse with BeautifulSoup
        soup = BeautifulSoup(html_content, "html.parser")

        # Extract metadata
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        description_tag = soup.find("meta", attrs={"name": "description"})
        description = description_tag.get("content", "") if description_tag else ""
        lang_tag = soup.find("html")
        language = lang_tag.get("lang", "en") if lang_tag else "en"

        result = ScrapeData(metadata={
            "url": str(resp.url),
            "title": title,
            "description": description,
            "language": language,
            "status_code": resp.status_code,
            "render_mode": "light",
        })

        # Extract main content
        main = soup.find("main") or soup.find("article") or soup.find(attrs={"role": "main"})
        if main and only_main_content:
            content_el = main
        else:
            content_el = soup.body or soup

        # Remove noise elements
        for tag in content_el.find_all(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        for tag in content_el.find_all(class_=lambda c: c and any(x in c.lower() for x in ["sidebar", "nav", "ad", "cookie", "popup", "modal"])):
            tag.decompose()

        # Format outputs
        for fmt in formats:
            if fmt == OutputFormat.MARKDOWN:
                html_str = str(content_el)
                md = markdownify.markdownify(html_str, heading_style="ATX", bullets="-")
                # Clean up excessive blank lines
                import re
                md = re.sub(r"\n{3,}", "\n\n", md).strip()
                if len(md) > 500_000:
                    logger.warning(f"Truncating large markdown content ({len(md)} chars)")
                    md = md[:500_000]
                result.markdown = md
            elif fmt == OutputFormat.HTML:
                result.html = str(content_el)
            elif fmt == OutputFormat.RAW_HTML:
                result.raw_html = html_content
            elif fmt == OutputFormat.LINKS:
                result.links = [a.get("href") for a in content_el.find_all("a", href=True)]

        return result
