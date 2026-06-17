"""Scrape endpoint — single-page scraping in multiple formats."""

import asyncio
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from ..config import HuginnConfig
from ..models import ErrorCode, OutputFormat, ScrapeRequest, ScrapeResponse
from ..scraper import Scraper
from ..state import get_state, limiter
from ..utils import _map_exception_to_error_code, build_proxy_dict

logger = logging.getLogger(__name__)


async def _do_scrape(request: Request, req: ScrapeRequest, config: HuginnConfig) -> ScrapeResponse:
    """Core scrape logic — shared by /v1/probe and /v1/scrape alias."""
    state = get_state()
    if not state.browser:
        raise HTTPException(status_code=503, detail="Browser not initialized")

    from ..circuit_breaker import get_circuit_breaker, CircuitOpenError as CBCircuitOpen, extract_domain
    from ..cache import get_cached_scrape_result, cache_scrape_result

    cb = get_circuit_breaker()
    domain = extract_domain(req.url)

    # Check circuit breaker first
    if cb.is_open(domain):
        if state.replay_log:
            await state.replay_log.log_scrape(
                url=req.url, method="scrape", status="blocked",
                error="circuit_open", request=req.model_dump(exclude_none=True),
            )
        return ScrapeResponse(
            success=False,
            error=f"Circuit breaker open for {domain}. Domain is temporarily blocked due to repeated failures.",
            error_code=ErrorCode.CIRCUIT_OPEN,
        )

    # Check response cache
    formats = req.formats or [OutputFormat.MARKDOWN]
    cached = await get_cached_scrape_result(req.url, formats)
    if cached:
        if state.replay_log:
            await state.replay_log.log_scrape(
                url=req.url, method="scrape", status="cache_hit",
                request=req.model_dump(exclude_none=True),
            )
        return ScrapeResponse(success=True, data=cached, cached=True)

    scraper = Scraper(state.browser, cb)
    proxy_dict = build_proxy_dict(config)

    _t0 = time.perf_counter()
    _replay_status = "success"
    _replay_error: Optional[str] = None
    try:
        data = await scraper.scrape(
            url=req.url,
            formats=formats,
            headers=req.headers,
            wait_for=req.wait_for,
            actions=[a.model_dump() for a in req.actions] if req.actions else None,
            include_tags=req.include_tags,
            exclude_tags=req.exclude_tags,
            only_main_content=req.only_main_content,
            timeout=req.timeout,
            proxy=proxy_dict,
            max_retries=req.max_retries,
            scroll=req.scroll,
            render_mode=req.render_mode,
        )
        await cache_scrape_result(req.url, formats, data)
        await cb.record_success(domain)
        summary = None
        if req.summary:
            from ..llm import _summarize_text
            text = (data.markdown or "").strip() or (data.html or "").strip()
            summary = await _summarize_text(text)
        return ScrapeResponse(success=True, data=data, summary=summary)
    except CBCircuitOpen:
        _replay_status = "blocked"
        _replay_error = "circuit_open"
        return ScrapeResponse(
            success=False,
            error=f"Circuit breaker opened for {domain} during request.",
            error_code=ErrorCode.CIRCUIT_OPEN,
        )
    except asyncio.TimeoutError:
        await cb.record_failure(domain)
        _replay_status = "timeout"
        _replay_error = f"timed out after {req.timeout}ms"
        return ScrapeResponse(
            success=False,
            error=f"Request timed out after {req.timeout}ms",
            error_code=ErrorCode.TIMEOUT,
        )
    except Exception as e:
        await cb.record_failure(domain)
        error_code = _map_exception_to_error_code(e)
        _replay_status = "error"
        _replay_error = str(e)
        return ScrapeResponse(success=False, error=str(e), error_code=error_code)
    finally:
        if state.replay_log:
            duration_ms = int((time.perf_counter() - _t0) * 1000)
            try:
                await state.replay_log.log_scrape(
                    url=req.url,
                    method="scrape",
                    status=_replay_status,
                    error=_replay_error,
                    duration_ms=duration_ms,
                    request=req.model_dump(exclude_none=True),
                )
            except Exception:
                logger.debug("Replay log write failed", exc_info=True)


def create_scrape_router(config: HuginnConfig, verify_api_key) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/probe", response_model=ScrapeResponse, tags=["Scrape"])
    @limiter.limit("100/minute")
    async def scrape(request: Request, req: ScrapeRequest, auth=Depends(verify_api_key)):
        """Scrape a single URL and return content in requested formats."""
        return await _do_scrape(request, req, config)

    return router