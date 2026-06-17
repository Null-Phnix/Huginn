"""Research memory endpoints — ChromaDB research memory query and management."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..config import HuginnConfig

logger = logging.getLogger(__name__)


def create_memory_router(config: HuginnConfig, verify_api_key) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/memory/query", tags=["Memory"])
    async def memory_query(
        q: str = Query(..., description="Query text"),
        n: int = Query(5, ge=1, le=50),
        min_relevance: float = Query(0.0, ge=0.0, le=1.0),
        type: Optional[str] = Query(None, description="Filter by type: finding, citation, snippet, report_summary"),
        auth=Depends(verify_api_key),
    ):
        """Semantic search over accumulated research memory."""
        from ..memory import ResearchMemory

        memory = ResearchMemory(data_dir=config.data_dir)
        if not memory.available:
            raise HTTPException(status_code=503, detail="Research memory not available (chromadb not installed)")

        results = await memory.query(
            query_text=q,
            n_results=n,
            min_relevance=min_relevance,
            filter_type=type,
        )

        return {
            "success": True,
            "query": q,
            "count": len(results),
            "results": results,
        }

    @router.get("/v1/memory/reports", tags=["Memory"])
    async def memory_reports(
        n: int = Query(10, ge=1, le=100),
        auth=Depends(verify_api_key),
    ):
        """List stored research reports."""
        from ..memory import ResearchMemory

        memory = ResearchMemory(data_dir=config.data_dir)
        if not memory.available:
            raise HTTPException(status_code=503, detail="Research memory not available")

        reports = await memory.get_reports(n_results=n)
        return {"success": True, "reports": reports, "count": len(reports)}

    @router.get("/v1/memory/related", tags=["Memory"])
    async def memory_related(
        topic: str = Query(..., description="Topic to find related concepts for"),
        n: int = Query(5, ge=1, le=20),
        auth=Depends(verify_api_key),
    ):
        """Find topics related to the given topic in research memory."""
        from ..memory import ResearchMemory

        memory = ResearchMemory(data_dir=config.data_dir)
        if not memory.available:
            raise HTTPException(status_code=503, detail="Research memory not available")

        topics = await memory.get_related_topics(topic, n_results=n)
        return {"success": True, "topic": topic, "related": topics, "count": len(topics)}

    return router