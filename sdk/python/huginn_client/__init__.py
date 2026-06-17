"""Huginn Python SDK - async client for autonomous web scraping."""
from __future__ import annotations
import json, os
from typing import Any, AsyncIterator, TypeVar
import httpx
from pydantic import BaseModel
from huginn.models import (
    Action, CrawlStartResponse, CrawlStatusResponse, DistillStartResponse,
    DistillStatusResponse, FlockResponse, JobStatus, MapResponse, OutputFormat,
    ScrapeOptions, ScrapeResponse, SearchOptions, SearchResponse,
)

__version__ = "1.2.0"
__all__ = [
    "HuginnClient",
    "HuginnError",
    "HuginnSync",
    "CircuitOpenError",
    "RateLimitError",
    "JobNotFoundError",
    "JobCancelledError",
]


class HuginnError(Exception):
    def __init__(self, message: str, status_code: int | None = None,
                 detail: str | None = None, response: httpx.Response | None = None,
                 error_code: str | None = None) -> None:
        self.status_code = status_code
        self.detail = detail or message
        self.response = response
        self.error_code = error_code
        super().__init__(self.detail)


class JobNotFoundError(HuginnError): pass
class JobCancelledError(HuginnError): pass


class CircuitOpenError(HuginnError):
    """Raised when a domain's circuit breaker is open."""
    pass


class RateLimitError(HuginnError):
    """Raised when the configured rate limit is exceeded."""
    pass


class _SSEEvent:
    def __init__(self, event: str, data: dict) -> None:
        self.event = event
        self.data = data


class HuginnClient:
    def __init__(self, base_url: str = "http://localhost:7432",
                 api_key: str | None = None, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key or os.getenv("HUGINN_API_KEY", "")
        self.timeout = timeout
        self._timeout = timeout  # back-compat alias
        self._client: httpx.AsyncClient | None = None

    async def close(self) -> None:
        """Close the underlying HTTP client. Safe to call multiple times."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "HuginnClient":
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        self._client = httpx.AsyncClient(
            base_url=self.base_url, headers=headers, timeout=httpx.Timeout(self._timeout))
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    @property
    def _httpx(self) -> httpx.AsyncClient:
        if self._client is None:
            raise HuginnError("Client not initialized. Use 'async with HuginnClient()'.")
        return self._client

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = self._auth_headers()
        headers.setdefault("Content-Type", "application/json")
        try:
            response = await self._httpx.request(method, path, headers=headers, **kwargs)
        except httpx.ConnectError as e:
            raise HuginnError(f"Connection failed - is Huginn running at {self.base_url}?", detail=str(e)) from e
        except httpx.TimeoutException as e:
            raise HuginnError("Request timed out.", detail=str(e)) from e
        if response.status_code >= 400:
            try:
                error_body = response.json()
                detail = error_body.get("detail", response.text)
            except Exception:
                detail = response.text
            if response.status_code == 404:
                raise JobNotFoundError(detail, status_code=404, response=response)
            raise HuginnError(detail, status_code=response.status_code, response=response)
        return response

    async def _get(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return (await self._request("GET", path, **kwargs)).json()

    async def _post(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return (await self._request("POST", path, **kwargs)).json()

    async def _stream_sse(self, path: str, **kwargs: Any) -> AsyncIterator[_SSEEvent]:
        headers = self._auth_headers()
        headers["Accept"] = "text/event-stream"
        headers["Cache-Control"] = "no-cache"
        try:
            async with self._httpx.stream("POST", path, headers=headers, **kwargs) as response:
                if response.status_code >= 400:
                    try: detail = response.json().get("detail", response.text)
                    except Exception: detail = response.text
                    raise HuginnError(detail, status_code=response.status_code, response=response)
                event_type = "message"
                async for line in response.aiter_lines():
                    if not line.strip(): continue
                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data_str = line[5:].strip()
                        data = json.loads(data_str) if data_str else {}
                        yield _SSEEvent(event=event_type, data=data)
                        event_type = "message"
        except httpx.ConnectError as e:
            raise HuginnError(f"Connection failed - is Huginn running at {self.base_url}?", detail=str(e)) from e

    async def health(self) -> dict[str, Any]:
        """Check API server health. Returns {"status": "ok", "version": "1.0.0", "browser": "running"}"""
        return await self._get("/health")

    async def probe(self, url: str, formats: list[OutputFormat | str] | None = None,
                    only_main_content: bool = True, include_tags: list[str] | None = None,
                    exclude_tags: list[str] | None = None, headers: dict[str, str] | None = None,
                    wait_for: int | str | None = None, actions: list[dict | Action] | None = None,
                    timeout: int = 30000, max_retries: int = 2, scroll: bool = False,
                    render_mode: str = "auto") -> ScrapeResponse:
        """Scrape a single URL and return structured content."""
        if formats is None: formats = [OutputFormat.MARKDOWN]
        payload: dict[str, Any] = {
            "url": url,
            "formats": [f.value if isinstance(f, OutputFormat) else f for f in formats],
            "onlyMainContent": only_main_content, "timeout": timeout,
            "maxRetries": max_retries, "scroll": scroll, "renderMode": render_mode,
        }
        if include_tags: payload["includeTags"] = include_tags
        if exclude_tags: payload["excludeTags"] = exclude_tags
        if headers: payload["headers"] = headers
        if wait_for: payload["waitFor"] = wait_for
        if actions:
            payload["actions"] = [a.model_dump(exclude_none=True) if isinstance(a, Action) else a for a in actions]
        return ScrapeResponse(**(await self._post("/v1/probe", json=payload)))

    async def sweep_start(self, url: str, max_depth: int | None = None, limit: int | None = None,
                          allow_backward_crawling: bool = False, allow_external_links: bool = False,
                          include_paths: list[str] | None = None, exclude_paths: list[str] | None = None,
                          scrape_options: ScrapeOptions | None = None, stream: bool = False) -> CrawlStartResponse:
        """Start an async crawl job. Returns CrawlStartResponse with .id for polling or SSE streaming."""
        payload: dict[str, Any] = {"url": url, "stream": stream}
        if max_depth is not None: payload["maxDepth"] = max_depth
        if limit is not None: payload["limit"] = limit
        payload["allowBackwardCrawling"] = allow_backward_crawling
        payload["allowExternalLinks"] = allow_external_links
        if include_paths: payload["includePaths"] = include_paths
        if exclude_paths: payload["excludePaths"] = exclude_paths
        if scrape_options: payload["scrapeOptions"] = scrape_options.model_dump(exclude_none=True)
        return CrawlStartResponse(**(await self._post("/v1/sweep", json=payload)))

    async def sweep_status(self, job_id: str) -> CrawlStatusResponse:
        """Get crawl job status and results."""
        return CrawlStatusResponse(**(await self._get(f"/v1/sweep/{job_id}")))

    async def sweep_cancel(self, job_id: str) -> dict[str, Any]:
        """Cancel a running crawl job."""
        return (await self._request("DELETE", f"/v1/sweep/{job_id}")).json()

    async def sweep_stream(self, url: str, max_depth: int | None = None, limit: int | None = None,
                           allow_backward_crawling: bool = False, allow_external_links: bool = False,
                           include_paths: list[str] | None = None, exclude_paths: list[str] | None = None,
                           scrape_options: ScrapeOptions | None = None) -> AsyncIterator[dict[str, Any]]:
        """Crawl a site with SSE streaming - yields documents as they are scraped."""
        payload: dict[str, Any] = {"url": url, "stream": True}
        if max_depth is not None: payload["maxDepth"] = max_depth
        if limit is not None: payload["limit"] = limit
        payload["allowBackwardCrawling"] = allow_backward_crawling
        payload["allowExternalLinks"] = allow_external_links
        if include_paths: payload["includePaths"] = include_paths
        if exclude_paths: payload["excludePaths"] = exclude_paths
        if scrape_options: payload["scrapeOptions"] = scrape_options.model_dump(exclude_none=True)
        async for e in self._stream_sse("/v1/sweep", json=payload):
            if e.event == "document": yield {"type": "document", "data": e.data.get("data", {})}
            elif e.event == "done": yield {"type": "done", "data": e.data.get("data", {})}
            elif e.event == "error": yield {"type": "error", "data": e.data}

    async def chart(self, url: str, search: str | None = None, include_subdomains: bool = False,
                    limit: int = 5000) -> MapResponse:
        """Fast URL discovery - returns all links without full content extraction."""
        return MapResponse(**(await self._post("/v1/chart", json={
            "url": url, "search": search, "includeSubdomains": include_subdomains, "limit": limit
        })))

    async def seek(self, query: str, search_options: SearchOptions | None = None,
                   scrape_options: ScrapeOptions | None = None, fallback_chain: bool = True) -> SearchResponse:
        """Search the web and scrape results. Falls back Bing->DDG->Brave when fallback_chain=True."""
        payload: dict[str, Any] = {"query": query, "fallbackChain": fallback_chain}
        if search_options: payload["searchOptions"] = search_options.model_dump(exclude_none=True)
        if scrape_options: payload["scrapeOptions"] = scrape_options.model_dump(exclude_none=True)
        return SearchResponse(**(await self._post("/v1/seek", json=payload)))

    async def distill_start(self, urls: list[str], prompt: str | None = None,
                            schema: type[BaseModel] | dict[str, Any] | None = None,
                            system_prompt: str | None = None, format: str = "markdown",
                            mental_model: bool = True, max_retries: int = 3,
                            stream: bool = False) -> DistillStartResponse:
        """Start an async structured extraction job."""
        schema_dict: dict[str, Any] | None = None
        if schema is not None:
            if isinstance(schema, type) and issubclass(schema, BaseModel):
                schema_dict = schema.model_json_schema()
            elif isinstance(schema, dict): schema_dict = schema
        return DistillStartResponse(**(await self._post("/v1/distill", json={
            "urls": urls, "prompt": prompt, "schema": schema_dict,
            "systemPrompt": system_prompt, "format": format,
            "mentalModel": mental_model, "maxRetries": max_retries, "stream": stream,
        })))

    async def distill_status(self, job_id: str) -> DistillStatusResponse:
        """Get extraction job status and result."""
        return DistillStatusResponse(**(await self._get(f"/v1/distill/{job_id}")))

    async def distill(self, urls: list[str], prompt: str | None = None,
                      schema: type[BaseModel] | dict[str, Any] | None = None,
                      system_prompt: str | None = None, format: str = "markdown",
                      mental_model: bool = True, max_retries: int = 3,
                      poll_interval: float = 1.0, poll_timeout: float = 300.0) -> DistillStatusResponse:
        """Extract structured data from URLs - polls until completion. Pass a Pydantic model as schema for typed results."""
        import asyncio
        start_resp = await self.distill_start(urls=urls, prompt=prompt, schema=schema,
            system_prompt=system_prompt, format=format, mental_model=mental_model,
            max_retries=max_retries, stream=False)
        elapsed = 0.0
        while elapsed < poll_timeout:
            await asyncio.sleep(poll_interval); elapsed += poll_interval
            status_resp = await self.distill_status(start_resp.id)
            if status_resp.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                if schema is not None and isinstance(schema, type) and issubclass(schema, BaseModel):
                    if status_resp.data is not None:
                        try: status_resp.data = schema.model_validate(status_resp.data)
                        except Exception: pass
                return status_resp
        raise HuginnError(f"distill job timed out after {poll_timeout}s", detail="Polling timeout")

    async def distill_stream(self, urls: list[str], prompt: str | None = None,
                              schema: type[BaseModel] | dict[str, Any] | None = None,
                              system_prompt: str | None = None, format: str = "markdown",
                              mental_model: bool = True, max_retries: int = 3) -> AsyncIterator[dict[str, Any]]:
        """Extract structured data with SSE streaming - yields progress events and a final done event."""
        schema_dict: dict[str, Any] | None = None
        if schema is not None:
            if isinstance(schema, type) and issubclass(schema, BaseModel):
                schema_dict = schema.model_json_schema()
            elif isinstance(schema, dict): schema_dict = schema
        async for e in self._stream_sse("/v1/distill", json={
            "urls": urls, "prompt": prompt, "schema": schema_dict,
            "systemPrompt": system_prompt, "format": format,
            "mentalModel": mental_model, "maxRetries": max_retries, "stream": True,
        }):
            yield {"type": e.event, "data": e.data}

    async def flock(self, urls: list[str], formats: list[OutputFormat | str] | None = None,
                     only_main_content: bool = True, include_tags: list[str] | None = None,
                     exclude_tags: list[str] | None = None, timeout: int = 30000) -> FlockResponse:
        """Scrape multiple URLs concurrently (max 50)."""
        if formats is None: formats = [OutputFormat.MARKDOWN]
        payload: dict[str, Any] = {
            "urls": urls,
            "formats": [f.value if isinstance(f, OutputFormat) else f for f in formats],
            "onlyMainContent": only_main_content, "timeout": timeout,
        }
        if include_tags: payload["includeTags"] = include_tags
        if exclude_tags: payload["excludeTags"] = exclude_tags
        return FlockResponse(**(await self._post("/v1/flock", json=payload)))

    async def jobs_list(self, status: str | None = None, limit: int = 50) -> dict[str, Any]:
        """List all jobs, optionally filtered by status."""
        params: dict[str, Any] = {"limit": limit}
        if status: params["status"] = status
        return await self._get("/v1/jobs", params=params)

    async def job_delete(self, job_id: str) -> dict[str, Any]:
        """Delete a job."""
        return (await self._request("DELETE", f"/v1/jobs/{job_id}")).json()


# ─── Sync wrapper ────────────────────────────────────────────────────────────

import asyncio as _asyncio  # noqa: E402  (deferred to end to keep async import optional)


class HuginnSync:
    """Synchronous wrapper for the async :class:`HuginnClient`.

    Every method is a thin sync shim that runs ``asyncio.run()`` internally.
    Use this if you don't want to write ``async/await`` code.

    Usage:
        from huginn_client import HuginnSync

        client = HuginnSync("http://localhost:7432", api_key="your-key")
        result = client.scrape("https://example.com")
        print(result.markdown)

        with client:
            result = client.scrape("https://example.com")

    Parameters match :class:`HuginnClient` exactly.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:7432",
        api_key: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._async = HuginnClient(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
        )

    def close(self) -> None:
        """Close the underlying HTTP client."""
        _asyncio.run(self._async.close())

    def __enter__(self) -> "HuginnSync":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    async def __aenter__(self) -> "HuginnClient":
        # Defer to the async client for use in async contexts too
        return await self._async.__aenter__()

    async def __aexit__(self, *exc_info) -> None:
        await self._async.__aexit__(*exc_info)

    # ─── internal ──────────────────────────────────────────────────────────

    def _run(self, coro):
        """Run an async coroutine in a fresh event loop, inside the async context.

        Without this, the underlying HuginnClient's _client is None
        (it's only created on __aenter__). HuginnSync's promise is
        "you don't have to write async/await" — that includes the
        `async with` boilerplate.
        """
        async def _wrapper():
            await self._async.__aenter__()
            try:
                return await coro
            finally:
                await self._async.__aexit__(None, None, None)
        return _asyncio.run(_wrapper())

    # ─── scrape ──────────────────────────────────────────────────────────────

    def scrape(self, url: str, formats=None, **kwargs):
        """Scrape a single URL (sync).

        Alias for probe() — kept for back-compat with code that expects
        a 'scrape' method. The public SDK calls this 'probe' internally
        (matching Huginn's /v1/probe endpoint), but downstream users
        are more familiar with 'scrape' as the Firecrawl-equivalent
        method name.
        """
        return self._run(self._async.probe(url, formats, **kwargs))

    def health(self) -> dict[str, Any]:
        """Check basic health (sync)."""
        return self._run(self._async.health())

    def probe(self, url: str, formats=None, **kwargs):
        """Lightweight probe of a URL (sync)."""
        return self._run(self._async.probe(url, formats, **kwargs))

    # ─── crawl ───────────────────────────────────────────────────────────────

    def sweep_start(self, url: str, max_depth=None, limit=None, **kwargs):
        """Start an async crawl (sync)."""
        return self._run(self._async.sweep_start(url, max_depth, limit, **kwargs))

    def sweep_status(self, job_id: str):
        """Poll a crawl job (sync)."""
        return self._run(self._async.sweep_status(job_id))

    def sweep_cancel(self, job_id: str):
        """Cancel a crawl (sync)."""
        return self._run(self._async.sweep_cancel(job_id))

    def sweep_stream(self, url: str, max_depth=None, limit=None, **kwargs):
        """Stream crawl events (sync, blocks until done)."""
        return list(self._run(self._async.sweep_stream(url, max_depth, limit, **kwargs)))

    # ─── map / batch ────────────────────────────────────────────────────────

    def chart(self, url: str, search=None, include_subdomains: bool = False, limit: int = 5000, **kwargs):
        """Map a site (sync)."""
        return self._run(self._async.chart(url, search, include_subdomains, limit, **kwargs))

    def flock(self, urls, formats=None, ignore_invalid_urls: bool = False, **kwargs):
        """Scrape multiple URLs (sync)."""
        return self._run(self._async.flock(urls, formats, ignore_invalid_urls, **kwargs))

    # ─── extract ────────────────────────────────────────────────────────────

    def distill_start(self, urls, prompt=None, schema=None, **kwargs):
        """Start an extraction job (sync)."""
        return self._run(self._async.distill_start(urls, prompt, schema, **kwargs))

    def distill_status(self, job_id: str):
        """Poll an extraction job (sync)."""
        return self._run(self._async.distill_status(job_id))
