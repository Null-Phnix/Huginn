"""Search endpoint — web search and search-driven scraping."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from ..config import HuginnConfig
from ..models import OutputFormat, SearchEngineHealthResponse, SearchRequest, SearchResponse
from ..proxy import ProxyConfigurationError, ProxyUnavailable
from ..searcher import SEARCH_ENGINE_HEALTH, Searcher
from ..state import get_state, limiter
from ..utils import get_proxy_provider, scrape_options_kwargs

logger = logging.getLogger(__name__)


async def _do_search(req: SearchRequest) -> SearchResponse:
    """Core search logic — shared by /v1/seek and /v1/search alias."""
    state = get_state()
    if not state.browser:
        raise HTTPException(status_code=503, detail="Browser not initialized")

    try:
        proxy_provider = get_proxy_provider(state.config or HuginnConfig())
        proxy_lease = proxy_provider.acquire(session_key=f"search:{uuid.uuid4()}")
    except (ProxyConfigurationError, ProxyUnavailable) as exc:
        return SearchResponse(
            success=False,
            error=str(exc),
            error_code=(
                "invalid_proxy"
                if isinstance(exc, ProxyConfigurationError)
                else "proxy_unavailable"
            ),
        )

    searcher = Searcher(
        state.browser,
        fallback_chain=req.fallback_chain,
        proxy_provider=proxy_provider,
        proxy_lease=proxy_lease,
    )
    formats = []
    if req.scrape_options:
        formats = req.scrape_options.formats

    try:
        results = await searcher.search(
            query=req.query,
            limit=req.limit or (req.search_options.limit if req.search_options else 5),
            scrape_formats=formats or [OutputFormat.MARKDOWN],
            tbs=req.search_options.tbs if req.search_options else None,
            country=req.search_options.country if req.search_options else None,
            language=req.search_options.language if req.search_options else None,
            engine=req.search_options.engine if req.search_options else "auto",
            scrape_results=req.scrape_results,
            scrape_kwargs=scrape_options_kwargs(req.scrape_options),
        )
        if results:
            return SearchResponse(success=True, data=results, metadata=searcher.last_metadata)
        attempt_codes = [
            attempt.error.code
            for attempt in searcher.last_metadata.attempts
            if attempt.error is not None
        ]
        error_code = (
            "circuit_open"
            if attempt_codes and all(code == "circuit_open" for code in attempt_codes)
            else "search_engines_unavailable"
        )
        return SearchResponse(
            success=False,
            error="No StarSearch-rendered search engine returned results",
            error_code=error_code,
            metadata=searcher.last_metadata,
        )
    except Exception as e:
        logger.error(f"Search failed: {e}", exc_info=True)
        return SearchResponse(success=False, error=str(e), error_code="search_failed")


def create_search_router(config: HuginnConfig, verify_api_key) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/seek", response_model=SearchResponse, tags=["Search"])
    @limiter.limit("30/minute")
    async def search(request: Request, req: SearchRequest, auth=Depends(verify_api_key)):
        """Search the web and scrape results."""
        return await _do_search(req)

    @router.get(
        "/v1/search/engines",
        response_model=SearchEngineHealthResponse,
        tags=["Search"],
    )
    @limiter.limit("60/minute")
    async def search_engine_health(request: Request, auth=Depends(verify_api_key)):
        """Return the live health scores and circuit state used by auto search."""
        return SearchEngineHealthResponse(engines=SEARCH_ENGINE_HEALTH.snapshot())

    return router
