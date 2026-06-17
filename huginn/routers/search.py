"""Search endpoint — web search and search-driven scraping."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from ..config import HuginnConfig
from ..models import OutputFormat, SearchRequest, SearchResponse
from ..searcher import Searcher
from ..state import get_state, limiter

logger = logging.getLogger(__name__)


async def _do_search(req: SearchRequest) -> SearchResponse:
    """Core search logic — shared by /v1/seek and /v1/search alias."""
    state = get_state()
    if not state.browser:
        raise HTTPException(status_code=503, detail="Browser not initialized")

    searcher = Searcher(state.browser, fallback_chain=req.fallback_chain)
    formats = []
    if req.scrape_options:
        formats = req.scrape_options.formats

    try:
        results = await searcher.search(
            query=req.query,
            limit=req.search_options.limit if req.search_options else 5,
            scrape_formats=formats or [OutputFormat.MARKDOWN],
            tbs=req.search_options.tbs if req.search_options else None,
            country=req.search_options.country if req.search_options else None,
            language=req.search_options.language if req.search_options else None,
            scrape_results=req.scrape_results,
        )
        return SearchResponse(success=True, data=results)
    except Exception as e:
        logger.error(f"Search failed: {e}", exc_info=True)
        return SearchResponse(success=False, error=str(e))


def create_search_router(config: HuginnConfig, verify_api_key) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/seek", response_model=SearchResponse, tags=["Search"])
    @limiter.limit("30/minute")
    async def search(request: Request, req: SearchRequest, auth=Depends(verify_api_key)):
        """Search the web and scrape results."""
        return await _do_search(req)

    return router