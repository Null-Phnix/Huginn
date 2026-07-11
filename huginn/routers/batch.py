"""Batch scrape endpoint — scrape multiple URLs concurrently."""

import asyncio
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request

from ..config import HuginnConfig
from ..models import ErrorCode, FlockRequest, FlockResponse, FlockResultItem, OutputFormat
from ..scraper import Scraper
from ..proxy import ProxyUnavailable
from ..state import get_state, limiter
from ..utils import (
    _map_exception_to_error_code,
    get_proxy_provider,
    proxy_failure_likely,
    scrape_failure,
)

logger = logging.getLogger(__name__)


async def _do_flock_scrape(req: FlockRequest, config: HuginnConfig) -> FlockResponse:
    """Core flock_scrape logic — shared by /v1/flock and /v1/batch/scrape alias."""
    state = get_state()
    if not state.browser:
        raise HTTPException(status_code=503, detail="Browser not initialized")

    from ..circuit_breaker import get_circuit_breaker, extract_domain
    from ..cache import get_cached_scrape_result, cache_scrape_result

    proxy_provider = get_proxy_provider(config)
    cb = get_circuit_breaker()
    scraper = Scraper(state.browser, cb)
    sem = asyncio.Semaphore(5)
    results: List[FlockResultItem] = []
    warnings: List[str] = []
    cache_context = req.model_dump(mode="json", by_alias=True, exclude_none=True)
    cache_context.pop("urls", None)
    cache_context.pop("formats", None)
    cache_context["_egress"] = proxy_provider.cache_identity()

    async def scrape_one(url: str) -> FlockResultItem:
        from ..scraper import _is_valid_http_url
        if not _is_valid_http_url(url):
            if req.ignore_invalid_urls:
                warnings.append(f"Skipped invalid URL: {url}")
                return FlockResultItem(
                    url=url, success=False,
                    error=f"Invalid URL (bad scheme or missing host): {url}",
                    error_code=ErrorCode.INVALID_URL,
                )
            raise HTTPException(
                status_code=422,
                detail=f"Invalid URL (bad scheme or missing host): {url}",
            )

        async with sem:
            domain = extract_domain(url)
            if cb.is_open(domain):
                warnings.append(f"Skipped {url}: circuit breaker open")
                return FlockResultItem(
                    url=url, success=False,
                    error=f"Circuit breaker open for {domain}",
                    error_code=ErrorCode.CIRCUIT_OPEN,
                )

            cached = await get_cached_scrape_result(
                url,
                req.formats or [OutputFormat.MARKDOWN],
                extra=cache_context,
            )
            if cached:
                return FlockResultItem(url=url, success=True, data=cached, cached=True)

            proxy_lease = None
            try:
                proxy_lease = proxy_provider.acquire(session_key=domain)
                data = await scraper.scrape(
                    url=url,
                    formats=req.formats,
                    include_tags=req.include_tags,
                    exclude_tags=req.exclude_tags,
                    only_main_content=req.only_main_content,
                    timeout=req.timeout,
                    proxy=proxy_lease.as_browser_proxy(),
                )
                failure = scrape_failure(data)
                if failure:
                    status, message = failure
                    if proxy_failure_likely(status, message):
                        proxy_lease.report_failure(message)
                    await cb.record_failure(domain)
                    return FlockResultItem(
                        url=url,
                        success=False,
                        data=data,
                        error=message,
                        error_code=ErrorCode.from_http_status(status),
                    )
                proxy_lease.report_success()
                data.metadata = data.metadata or {}
                data.metadata["egress"] = {
                    "mode": proxy_provider.mode,
                    "proxied": proxy_lease.configured,
                    "endpoint": proxy_lease.endpoint.label if proxy_lease.endpoint else None,
                }
                if data.markdown or data.html or data.raw_html or data.links or data.screenshot:
                    await cache_scrape_result(
                        url,
                        req.formats or [OutputFormat.MARKDOWN],
                        data,
                        extra=cache_context,
                    )
                await cb.record_success(domain)
                return FlockResultItem(url=url, success=True, data=data)
            except asyncio.TimeoutError:
                if proxy_lease:
                    proxy_lease.report_failure(f"timed out after {req.timeout}ms")
                await cb.record_failure(domain)
                return FlockResultItem(
                    url=url, success=False,
                    error=f"Request timed out after {req.timeout}ms",
                    error_code=ErrorCode.TIMEOUT,
                )
            except ProxyUnavailable as e:
                return FlockResultItem(
                    url=url, success=False, error=str(e),
                    error_code=ErrorCode.SERVICE_UNAVAILABLE,
                )
            except Exception as e:
                if proxy_lease and proxy_failure_likely(message=str(e)):
                    proxy_lease.report_failure(str(e))
                await cb.record_failure(domain)
                return FlockResultItem(
                    url=url, success=False,
                    error=str(e),
                    error_code=_map_exception_to_error_code(e),
                )

    tasks = [scrape_one(url) for url in req.urls]
    items = await asyncio.gather(*tasks, return_exceptions=True)

    for i, item in enumerate(items):
        if isinstance(item, FlockResultItem):
            results.append(item)
        elif isinstance(item, Exception):
            results.append(FlockResultItem(
                url=req.urls[i] if i < len(req.urls) else "unknown",
                success=False, error=str(item),
                error_code=ErrorCode.NAVIGATION_FAILED,
            ))

    success_count = sum(1 for r in results if r.success)
    partial = success_count > 0 and success_count < len(results)
    await scraper.close()

    return FlockResponse(
        success=success_count > 0,
        partial=partial,
        data=results,
        warnings=warnings if warnings else None,
    )


def create_batch_router(config: HuginnConfig, verify_api_key) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/flock", response_model=FlockResponse, tags=["Batch"])
    @limiter.limit("10/minute")
    async def flock_scrape(request: Request, req: FlockRequest, auth=Depends(verify_api_key)):
        """Scrape multiple URLs concurrently with partial results support."""
        return await _do_flock_scrape(req, config)

    return router
