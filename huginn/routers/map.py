"""Map endpoints — URL discovery and site mapping."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from ..config import HuginnConfig
from ..mapper import Mapper
from ..models import ErrorCode, GraphRequest, GraphResponse, MapRequest, MapResponse
from ..state import get_state, limiter

logger = logging.getLogger(__name__)


async def _do_chart_site(req: MapRequest) -> MapResponse:
    """Core chart_site logic — shared by /v1/chart and /v1/map alias."""
    state = get_state()
    if not state.browser:
        raise HTTPException(status_code=503, detail="Browser not initialized")

    mapper = Mapper(state.browser)
    try:
        links = await mapper.chart_site(
            url=req.url,
            search=req.search,
            include_subdomains=req.include_subdomains,
            limit=req.limit,
            sitemap=req.sitemap or "include",
        )
        return MapResponse(success=True, links=links)
    except Exception as e:
        logger.error(f"Map failed: {e}", exc_info=True)
        return MapResponse(success=False, error=str(e))


def create_map_router(config: HuginnConfig, verify_api_key) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/chart", response_model=MapResponse, tags=["Map"])
    @limiter.limit("60/minute")
    async def chart_site(request: Request, req: MapRequest, auth=Depends(verify_api_key)):
        """Fast URL discovery — returns all links without full content extraction."""
        return await _do_chart_site(req)

    @router.post("/v1/graph", response_model=GraphResponse, tags=["Map"])
    @limiter.limit("30/minute")
    async def graph_site(request: Request, req: GraphRequest, auth=Depends(verify_api_key)):
        """
        Map a site as a directed graph of pages and links.

        BFS crawl up to max_depth, returning nodes (pages with metadata)
        and edges (source -> target links). Useful for site architecture
        analysis, link visualization, and audit.
        """
        state = get_state()
        if not state.browser:
            raise HTTPException(status_code=503, detail="Browser not initialized")

        mapper = Mapper(state.browser)
        try:
            graph = await mapper.map_site_graph(
                start_url=req.url,
                include_subdomains=req.include_subdomains,
                limit=req.limit,
                max_depth=req.max_depth,
            )
            return GraphResponse(success=True, data=graph)
        except Exception as e:
            logger.error(f"Graph failed: {e}", exc_info=True)
            return GraphResponse(success=False, error=str(e), error_code=ErrorCode.INTERNAL_ERROR)

    return router