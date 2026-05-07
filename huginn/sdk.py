"""
Huginn Python SDK — Clean, typed client for the Huginn API.

Usage:
    client = Huginn("http://localhost:8000", api_key="...")
    result = await client.scrape("https://example.com")
    async for page in client.crawl("https://example.com"):
        print(page.markdown)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Union

import httpx

from .models import (
    ActionType,
    CrawlRequest,
    CrawlStartResponse,
    CrawlStatusResponse,
    DistillRequest,
    DistillStartResponse,
    DistillStatusResponse,
    ErrorCode,
    FlockRequest,
    FlockResponse,
    JobStatus,
    MapRequest,
    MapResponse,
    OutputFormat,
    ScrapeData,
    ScrapeRequest,
    ScrapeResponse,
)

logger = logging.getLogger(__name__)


class HuginnError(Exception):
    """Base exception for Huginn API errors."""

    def __init__(
        self,
        message: str,
        error_code: Optional[ErrorCode] = None,
        status_code: int = 0,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code


class CircuitOpenError(HuginnError):
    """Raised when a domain's circuit breaker is open."""
    pass


class RateLimitError(HuginnError):
    """Raised when rate limit is exceeded."""
    pass


class HuginnClient:
    """
    Async Python client for the Huginn API.

    Parameters
    ----------
    base_url : str
        Base URL of the Huginn API server. Default: ``http://localhost:8000``.
    api_key : str, optional
        API key for authentication. Can also be set via ``HUGINN_API_KEY``
        environment variable.
    timeout : float
        Default timeout for requests in seconds. Default: 60.
    max_connections : int
        Maximum concurrent connections. Default: 100.
    max_keepalive_connections : int
        Maximum idle connections to keep alive. Default: 20.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: Optional[str] = None,
        timeout: float = 60.0,
        max_connections: int = 100,
        max_keepalive_connections: int = 20,
    ):
        import os

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get("HUGINN_API_KEY", "")
        self.timeout = timeout

        limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
        )
        self._client: httpx.AsyncClient = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=10.0),
            limits=limits,
            headers=self._headers(),
        )

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def close(self):
        """Close the underlying HTTP client. Use as async context manager instead."""
        await self._client.aclose()

    async def __aenter__(self) -> "HuginnClient":
        return self

    async def __aexit__(self, *exc_info):
        await self.close()

    # ── internal ─────────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs,
    ) -> httpx.Response:
        """Make an HTTP request with error handling."""
        url = f"{self.base_url}{path}"
        try:
            response = await self._client.request(method, url, **kwargs)
        except httpx.TimeoutException as e:
            raise HuginnError(f"Request to {url} timed out: {e}", status_code=408) from e
        except httpx.ConnectError as e:
            raise HuginnError(f"Failed to connect to {url}: {e}", status_code=503) from e

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "60")
            raise RateLimitError(
                f"Rate limit exceeded. Retry after {retry_after}s",
                error_code=ErrorCode.RATE_LIMITED,
                status_code=429,
            )

        if response.status_code == 503 and "circuit" in response.text.lower():
            raise CircuitOpenError(
                "Circuit breaker is open for this domain",
                error_code=ErrorCode.CIRCUIT_OPEN,
                status_code=503,
            )

        if response.status_code >= 400:
            try:
                body = response.json()
                error_msg = body.get("error", response.text)
                error_code_str = body.get("error_code")
                error_code = ErrorCode(error_code_str) if error_code_str else None
            except Exception:
                error_msg = response.text
                error_code = None

            raise HuginnError(error_msg, error_code=error_code, status_code=response.status_code)

        return response

    # ── scrape ───────────────────────────────────────────────────────────────

    async def scrape(
        self,
        url: str,
        formats: Optional[List[str]] = None,
        headers: Optional[Dict[str, str]] = None,
        wait_for: Optional[Union[int, str]] = None,
        actions: Optional[List[Dict[str, Any]]] = None,
        only_main_content: bool = True,
        timeout: int = 30000,
        scroll: bool = False,
        render_mode: str = "auto",
        cache: bool = True,
        **kwargs,
    ) -> ScrapeData:
        """
        Scrape a single URL.

        Parameters
        ----------
        url : str
            URL to scrape.
        formats : list of str, optional
            Output formats: ``markdown``, ``html``, ``raw_html``, ``screenshot``,
            ``links``, ``metadata``, ``pdf``. Default: ``["markdown"]``.
        headers : dict, optional
            Custom HTTP headers to send with the request.
        wait_for : int or str, optional
            Wait strategy: integer for timeout ms, ``"networkidle"``,
            ``"domcontentloaded"``, or a CSS selector.
        actions : list of dict, optional
            Browser actions to execute before extraction, e.g.
            ``[{"type": "click", "selector": ".load-more"}]``.
        only_main_content : bool
            Extract only main content (skip nav, footer, etc.). Default: True.
        timeout : int
            Request timeout in milliseconds. Default: 30000.
        scroll : bool
            Auto-scroll the page to load dynamic content. Default: False.
        render_mode : str
            ``auto`` (default), ``full`` (browser), or ``light`` (httpx).
        cache : bool
            Use cached response if available. Default: True.

        Returns
        -------
        ScrapeData
            The scraped content and metadata.

        Raises
        ------
        HuginnError
            On API errors.

        Examples
        --------
        >>> async with HuginnClient() as client:
        ...     result = await client.scrape("https://example.com", formats=["markdown", "links"])
        ...     print(result.markdown[:100])
        ...     print(result.links[:5])
        """
        if formats is None:
            formats = ["markdown"]

        # Normalize format strings to OutputFormat enums
        fmt_enums = []
        for f in formats:
            try:
                fmt_enums.append(OutputFormat(f))
            except ValueError:
                raise HuginnError(
                    f"Unsupported format: {f}. Supported: {[e.value for e in OutputFormat]}"
                )

        # Normalize actions dicts
        normalized_actions = None
        if actions:
            normalized_actions = []
            for a in actions:
                try:
                    at = ActionType(a.get("type", ""))
                    normalized_actions.append({**a, "type": at})
                except ValueError:
                    raise HuginnError(
                        f"Unsupported action type: {a.get('type')}. "
                        f"Supported: {[e.value for e in ActionType]}"
                    )

        req = ScrapeRequest(
            url=url,
            formats=fmt_enums,
            headers=headers,
            wait_for=wait_for,
            actions=normalized_actions,
            only_main_content=only_main_content,
            timeout=timeout,
            scroll=scroll,
            render_mode=render_mode,
            max_retries=kwargs.get("max_retries", 2),
        )

        response = await self._request("POST", "/v1/probe", json=req.model_dump())
        data = response.json()

        if not data.get("success", False):
            error_code_str = data.get("error_code")
            raise HuginnError(
                data.get("error", "Scrape failed"),
                error_code=ErrorCode(error_code_str) if error_code_str else None,
            )

        return ScrapeData.model_validate(data.get("data", {}))

    # ── crawl ────────────────────────────────────────────────────────────────

    async def crawl(
        self,
        url: str,
        max_depth: Optional[int] = None,
        limit: Optional[int] = None,
        allow_external_links: bool = False,
        scrape_options: Optional[Dict[str, Any]] = None,
        webhook_url: Optional[str] = None,
        stream: bool = False,
        **kwargs,
    ) -> Union[CrawlStartResponse, AsyncIterator[ScrapeData]]:
        """
        Start a crawl job for a URL.

        Parameters
        ----------
        url : str
            Starting URL for the crawl.
        max_depth : int, optional
            Maximum link-following depth. None = unlimited.
        limit : int, optional
            Maximum number of pages to crawl. None = unlimited.
        allow_external_links : bool
            Follow links to external domains. Default: False.
        scrape_options : dict, optional
            Options passed to each page scrape.
        webhook_url : str, optional
            URL to receive job completion notifications.
        stream : bool
            If True, yields results as they are discovered (async generator).
            If False, waits for completion and returns job info.

        Returns
        -------
        CrawlStartResponse or AsyncIterator[ScrapeData]
            Job info when stream=False, or an async generator of ScrapeData
            when stream=True.

        Examples
        --------
        Non-streaming:
        >>> async with HuginnClient() as client:
        ...     job = await client.crawl("https://example.com", limit=10)
        ...     print(f"Job ID: {job.id}")

        Streaming:
        >>> async with HuginnClient() as client:
        ...     async for page in client.crawl("https://example.com", limit=10, stream=True):
        ...         print(page.markdown[:100])
        """
        req = CrawlRequest(
            url=url,
            max_depth=max_depth,
            limit=limit,
            allow_external_links=allow_external_links,
            scrape_options=scrape_options,
            webhook_url=webhook_url,
            stream=stream,
            ignore_robots=kwargs.get("ignore_robots", False),
        )

        response = await self._request("POST", "/v1/sweep", json=req.model_dump())
        data = response.json()

        if not data.get("success", False):
            raise HuginnError(data.get("error", "Crawl start failed"))

        job = CrawlStartResponse.model_validate(data)

        if stream:
            return self._crawl_stream(job.id)

        return job

    async def _crawl_stream(self, job_id: str) -> AsyncIterator[ScrapeData]:
        """Async generator that yields crawl results via SSE."""
        url = f"{self.base_url}/v1/sweep/{job_id}/stream"
        headers = self._headers()
        headers["Accept"] = "text/event-stream"

        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            async with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if not line:
                        continue
                    if line.startswith("data:"):
                        import json
                        payload = json.loads(line[5:])
                        if payload.get("type") == "document":
                            yield ScrapeData.model_validate(payload.get("data", {}))
                        elif payload.get("type") == "done":
                            break

    async def crawl_status(self, job_id: str) -> CrawlStatusResponse:
        """Poll crawl job status and return full result."""
        response = await self._request("GET", f"/v1/sweep/{job_id}")
        data = response.json()

        if not data.get("success", False):
            raise HuginnError(data.get("error", "Crawl status failed"))

        return CrawlStatusResponse.model_validate(data)

    # ── batch scrape (flock) ─────────────────────────────────────────────────

    async def flock(
        self,
        urls: List[str],
        formats: Optional[List[str]] = None,
        only_main_content: bool = True,
        timeout: int = 30000,
        webhook_url: Optional[str] = None,
        **kwargs,
    ) -> FlockResponse:
        """
        Scrape multiple URLs concurrently.

        Parameters
        ----------
        urls : list of str
            URLs to scrape (max 50).
        formats : list of str, optional
            Output formats. Default: ``["markdown"]``.
        only_main_content : bool
            Extract only main content. Default: True.
        timeout : int
            Per-URL timeout in milliseconds. Default: 30000.
        webhook_url : str, optional
            URL to receive completion notification.

        Returns
        -------
        FlockResponse
            Contains ``data`` list of FlockResultItem, ``partial`` bool
            indicating if some URLs failed, and ``warnings``.

        Raises
        ------
        HuginnError
            If all URLs fail or request is invalid.
        RateLimitError
            If rate limit is exceeded.

        Examples
        --------
        >>> async with HuginnClient() as client:
        ...     result = await client.flock(["https://a.com", "https://b.com"], formats=["markdown"])
        ...     for item in result.data:
        ...         if item.success:
        ...             print(item.data.markdown[:50])
        ...     if result.partial:
        ...         print(f"Some URLs failed: {result.warnings}")
        """
        if len(urls) > 50:
            raise HuginnError("Maximum 50 URLs per flock request")

        if formats is None:
            formats = ["markdown"]

        fmt_enums = [OutputFormat(f) for f in formats]

        req = FlockRequest(
            urls=urls,
            formats=fmt_enums,
            only_main_content=only_main_content,
            timeout=timeout,
            webhook_url=webhook_url,
        )

        response = await self._request("POST", "/v1/flock", json=req.model_dump())
        data = response.json()

        if not data.get("success", False) and not data.get("partial", False):
            raise HuginnError(
                data.get("error", "Batch scrape failed"),
                error_code=ErrorCode(data.get("error_code")) if data.get("error_code") else None,
            )

        return FlockResponse.model_validate(data)

    # ── map ──────────────────────────────────────────────────────────────────

    async def map_site(
        self,
        url: str,
        search: Optional[str] = None,
        include_subdomains: bool = False,
        limit: int = 5000,
    ) -> List[str]:
        """
        Discover URLs on a site without extracting content.

        Parameters
        ----------
        url : str
            Starting URL.
        search : str, optional
            Filter URLs containing this string.
        include_subdomains : bool
            Include links to subdomains. Default: False.
        limit : int
            Maximum URLs to return. Default: 5000.

        Returns
        -------
        list of str
            Discovered URLs.
        """
        req = MapRequest(
            url=url,
            search=search,
            include_subdomains=include_subdomains,
            limit=limit,
        )

        response = await self._request("POST", "/v1/chart", json=req.model_dump())
        data = response.json()

        if not data.get("success", False):
            raise HuginnError(data.get("error", "Site map failed"))

        return data.get("links", [])

    # ── distill ─────────────────────────────────────────────────────────────

    async def distill(
        self,
        urls: List[str],
        prompt: Optional[str] = None,
        schema: Optional[Dict[str, Any]] = None,
        system_prompt: Optional[str] = None,
        format: str = "json",
        mental_model: bool = True,
        max_retries: int = 3,
        webhook_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Extract structured data from URLs using an LLM.

        Parameters
        ----------
        urls : list of str
            URLs to extract from (max 50).
        prompt : str, optional
            What to extract from the pages.
        schema : dict, optional
            JSON schema the output must conform to.
        system_prompt : str, optional
            Custom system prompt for the LLM.
        format : str
            ``json`` (default), ``markdown``, or ``text``.
        mental_model : bool
            Use DOM mental model for better extraction. Default: True.
        max_retries : int
            Max LLM retry attempts. Default: 3.
        webhook_url : str, optional
            URL to receive completion notification.

        Returns
        -------
        dict
            Extracted structured data with ``data``, ``confidence``,
            ``attempts``, and ``sources`` fields.
        """
        req = DistillRequest(
            urls=urls,
            prompt=prompt,
            schema_=schema,
            system_prompt=system_prompt,
            format=format,
            mental_model=mental_model,
            max_retries=max_retries,
            webhook_url=webhook_url,
        )

        response = await self._request("POST", "/v1/distill", json=req.model_dump())
        data = response.json()

        if not data.get("success", False):
            raise HuginnError(data.get("error", "Distillation failed"))

        return data

    async def distill_status(self, job_id: str) -> DistillStatusResponse:
        """Poll a distillation job status."""
        response = await self._request("GET", f"/v1/distill/{job_id}")
        return DistillStatusResponse.model_validate(response.json())

    # ── health ───────────────────────────────────────────────────────────────

    async def health(self) -> Dict[str, Any]:
        """Check basic health."""
        response = await self._request("GET", "/health")
        return response.json()

    async def health_detailed(self) -> Dict[str, Any]:
        """Check detailed health including circuit breaker and cache stats."""
        response = await self._request("GET", "/health/detailed")
        return response.json()
