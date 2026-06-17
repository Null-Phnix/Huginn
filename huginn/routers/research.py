"""Deep research endpoint — autonomous multi-source research with memory."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from ..config import HuginnConfig
from ..models import (
    ResearchCitation,
    ResearchFinding,
    ResearchRequest,
    ResearchResponse,
)
from ..state import get_state
from ..utils import _map_exception_to_error_code

logger = logging.getLogger(__name__)


def create_research_router(config: HuginnConfig, verify_api_key) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/research", response_model=ResearchResponse, tags=["Research"])
    async def deep_research(req: ResearchRequest, auth=Depends(verify_api_key)):
        """
        Conduct autonomous deep research on any topic.

        Iteratively explores multiple sources, tracks beliefs with confidence
        scores, detects contradictions, and synthesizes a structured report.

        This is Huginn's most powerful endpoint — far beyond what Firecrawl
        or any single-pass scraper can achieve.
        """
        state = get_state()
        if not state.browser:
            raise HTTPException(status_code=503, detail="Browser not initialized")

        from ..memory import ResearchMemory
        from ..researcher import DeepResearcher

        try:
            memory = ResearchMemory(data_dir=config.data_dir) if config.server.data_dir else None
            researcher = DeepResearcher(
                browser=state.browser,
                llm_provider=state.config.extract.llm_provider,
                llm_model=state.config.extract.llm_model,
                urls=req.urls,
                memory=memory,
            )

            result = await researcher.research(
                query=req.query,
                depth=req.depth,
                max_sources=req.max_sources,
                target_length=req.target_length,
                background_questions=req.background_questions,
            )

            return ResearchResponse(
                success=True,
                query=result.query,
                summary=result.summary,
                report=result.report,
                findings=[
                    ResearchFinding(
                        topic=f.topic,
                        claim=f.claim,
                        supporting_citations=[
                            ResearchCitation(**c.to_dict()) for c in f.supporting_citations
                        ],
                        confidence=f.confidence,
                        contradicts=f.contradicts,
                        needs_verification=f.needs_verification,
                        verified=f.verified,
                    )
                    for f in result.findings
                ],
                citations=[ResearchCitation(**c.to_dict()) for c in result.citations],
                confidence=result.confidence,
                sources_consulted=result.sources_consulted,
                research_duration_seconds=result.research_duration_seconds,
                depth_achieved=result.depth_achieved,
                warnings=result.warnings,
            )

        except Exception as e:
            logger.error(f"Deep research failed: {e}", exc_info=True)
            return ResearchResponse(
                success=False,
                error=str(e),
                error_code=_map_exception_to_error_code(e),
            )

    return router