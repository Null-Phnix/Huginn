"""
Huginn Research Agent — Deep multi-source research with autonomous sub-agent dispatch.

Given a research query, the agent:
1. Breaks it into independent sub-queries
2. Scrapes multiple sources concurrently
3. Synthesizes findings with an LLM
4. Returns a comprehensive, source-cited report

Firecrawl has no equivalent. Perplexity is the closest competitor but:
- No API control over source discovery
- No persistent memory across queries
- No structured data extraction
- No change detection on sources

Huginn Research Agent: you own the infrastructure, the models, and the data.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from .browser import BrowserManager
from .circuit_breaker import get_circuit_breaker, extract_domain
from .extractor import Extractor
from .mapper import Mapper
from .models import OutputFormat
from .scraper import Scraper

logger = logging.getLogger(__name__)


class ResearchSource:
    """A single source in a research report."""

    def __init__(
        self,
        url: str,
        title: str = "",
        relevance: float = 0.0,
        summary: str = "",
        raw_content: str = "",
        citations: Optional[List[str]] = None,
    ):
        self.url = url
        self.title = title
        self.relevance = relevance
        self.summary = summary
        self.raw_content = raw_content
        self.citations = citations or []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "relevance": self.relevance,
            "summary": self.summary,
            "raw_content": self.raw_content[:500],
            "citations": self.citations,
        }


class ResearchReport:
    """Complete research report."""

    def __init__(self, query: str, id: Optional[str] = None):
        self.id = id or str(uuid.uuid4())[:8]
        self.query = query
        self.sources: List[ResearchSource] = []
        self.findings: List[str] = []
        self.contradictions: List[Dict[str, str]] = []
        self.gaps: List[str] = []
        self.final_report: str = ""
        self.confidence: float = 0.0
        self.duration_seconds: float = 0.0
        self.created_at: str = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    def add_source(self, source: ResearchSource):
        self.sources.append(source)

    def add_finding(self, finding: str):
        self.findings.append(finding)

    def add_contradiction(
        self, claim_a: str, claim_b: str, source_a: str, source_b: str
    ):
        self.contradictions.append({
            "claim_a": claim_a,
            "claim_b": claim_b,
            "source_a": source_a,
            "source_b": source_b,
        })

    def add_gap(self, gap: str):
        self.gaps.append(gap)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "query": self.query,
            "sources": [s.to_dict() for s in self.sources],
            "findings": self.findings,
            "contradictions": self.contradictions,
            "gaps": self.gaps,
            "final_report": self.final_report,
            "confidence": self.confidence,
            "duration_seconds": self.duration_seconds,
            "created_at": self.created_at,
            "num_sources": len(self.sources),
        }


class SubQuery:
    """A single sub-query for parallel research."""

    def __init__(
        self,
        text: str,
        parent_query: str,
        depth: int = 0,
        source_hint: Optional[str] = None,
    ):
        self.text = text
        self.parent_query = parent_query
        self.depth = depth
        self.source_hint = source_hint
        self.sources: List[ResearchSource] = []
        self.findings: List[str] = []
        self.status: str = "pending"
        self.error: Optional[str] = None


class ResearchAgent:
    """
    Deep research agent. Breaks complex queries into sub-queries, scrapes
    multiple sources in parallel, and synthesizes a final report.

    Parameters
    ----------
    browser : BrowserManager
        Browser instance for JS-rendered pages.
    llm_provider : str
        LLM provider for synthesis: "openai" (default), "ollama", "google", "anthropic".
    llm_model : str, optional
        Specific model to use.
    max_sources_per_query : int
        Maximum sources to scrape per sub-query. Default: 5.
    max_total_sources : int
        Maximum sources across entire research session. Default: 20.
    synthesis_depth : str
        "quick" (1 pass), "standard" (2 passes), "deep" (3 passes). Default: "standard".
    parallel_subqueries : int
        Max concurrent sub-query workers. Default: 5.
    """

    def __init__(
        self,
        browser: BrowserManager,
        llm_provider: str = "openai",
        llm_model: Optional[str] = None,
        max_sources_per_query: int = 5,
        max_total_sources: int = 20,
        synthesis_depth: str = "standard",
        parallel_subqueries: int = 5,
    ):
        self.browser = browser
        self.scraper = Scraper(browser)
        self.mapper = Mapper(browser)
        self.extractor = Extractor(browser, llm_provider=llm_provider, llm_model=llm_model)
        self.circuit_breaker = get_circuit_breaker()
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.max_sources_per_query = max_sources_per_query
        self.max_total_sources = max_total_sources
        self.synthesis_depth = synthesis_depth
        self.parallel_subqueries = parallel_subqueries

    async def research(
        self,
        query: str,
        context: Optional[str] = None,
    ) -> ResearchReport:
        """
        Conduct deep research on a query and return a comprehensive report.

        Parameters
        ----------
        query : str
            The research question or topic.
        context : str, optional
            Additional context to guide research direction.

        Returns
        -------
        ResearchReport
            Complete report with sources, findings, contradictions, and final synthesis.
        """
        start = time.time()
        report = ResearchReport(query=query)

        logger.info(f"[{report.id}] Starting research: {query}")

        # Step 1: Decompose query into sub-queries
        sub_queries = await self._decompose_query(query, context)
        logger.info(f"[{report.id}] Decomposed into {len(sub_queries)} sub-queries")

        # Step 2: Run sub-queries in parallel
        await self._run_subqueries(sub_queries, report)
        logger.info(f"[{report.id}] Sub-queries complete: {len(report.sources)} sources")

        # Step 3: Detect contradictions
        await self._detect_contradictions(report)
        if report.contradictions:
            logger.info(f"[{report.id}] Found {len(report.contradictions)} contradictions")

        # Step 4: Identify gaps
        await self._identify_gaps(report)
        if report.gaps:
            logger.info(f"[{report.id}] Identified {len(report.gaps)} gaps")

        # Step 5: Synthesize final report
        synthesis_passes = {"quick": 1, "standard": 2, "deep": 3}.get(
            self.synthesis_depth, 2
        )
        report.final_report = await self._synthesize(query, report, passes=synthesis_passes)
        report.confidence = self._compute_confidence(report)

        report.duration_seconds = time.time() - start
        logger.info(
            f"[{report.id}] Research complete in {report.duration_seconds:.1f}s, "
            f"confidence={report.confidence:.2f}"
        )

        return report

    async def _decompose_query(
        self, query: str, context: Optional[str] = None
    ) -> List[SubQuery]:
        """Use LLM to break a complex query into independent sub-queries."""
        system_prompt = (
            "You are a research strategist. Break down complex research questions "
            "into 3-8 independent sub-queries that can be answered by scraping web sources. "
            "Each sub-query should:\n"
            "1. Be self-contained (answerable without knowing the others)\n"
            "2. Cover a distinct aspect of the main question\n"
            "3. Be specific enough to guide source discovery\n"
            "Return a JSON object with a 'sub_queries' key containing an array of objects "
            "with 'text' and optional 'source_hint' fields. "
            "source_hint can be: 'academic', 'news', 'technical', 'government', 'community'."
        )

        user_prompt = f"Main question: {query}"
        if context:
            user_prompt += f"\n\nAdditional context: {context}"

        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            raw = await self.extractor._call_llm(messages, schema=None)
            data = json.loads(raw) if isinstance(raw, str) else raw
            sub_qs = data.get("sub_queries", data.get("sub-queries", []))
            return [
                SubQuery(
                    text=sq["text"],
                    parent_query=query,
                    source_hint=sq.get("source_hint"),
                )
                for sq in sub_qs
            ]
        except Exception as e:
            logger.warning(f"Query decomposition failed: {e}, using fallback")
            keywords = [k.strip() for k in query.split() if len(k) > 4][:5]
            return [SubQuery(text=q, parent_query=query) for q in keywords[:3]]

    async def _run_subqueries(
        self, sub_queries: List[SubQuery], report: ResearchReport
    ) -> None:
        """Run sub-queries in parallel with a semaphore to limit concurrency."""
        semaphore = asyncio.Semaphore(self.parallel_subqueries)

        async def worker(sq: SubQuery) -> None:
            async with semaphore:
                sq.status = "running"
                try:
                    sources = await self._research_subquery(sq)
                    sq.sources = sources
                    sq.status = "done"
                    for src in sources:
                        report.add_source(src)
                    for finding in sq.findings:
                        report.add_finding(finding)
                except Exception as e:
                    sq.status = "failed"
                    sq.error = str(e)[:200]
                    logger.error(f"Sub-query failed: {sq.text[:50]} -- {e}")

        await asyncio.gather(*[worker(sq) for sq in sub_queries])

    async def _research_subquery(self, sub_query: SubQuery) -> List[ResearchSource]:
        """Research a single sub-query: map sources, scrape them, extract content."""
        sources: List[ResearchSource] = []

        candidate_urls = await self._discover_sources(sub_query)
        if not candidate_urls:
            return sources

        scrape_tasks = [
            self._scrape_source(url, sub_query.text)
            for url in candidate_urls[: self.max_sources_per_query]
        ]

        results = await asyncio.gather(*scrape_tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, ResearchSource):
                sources.append(result)
            elif isinstance(result, Exception):
                logger.debug(f"Source scrape failed: {result}")

        return sources

    async def _discover_sources(self, sub_query: SubQuery) -> List[str]:
        """Discover URLs relevant to a sub-query using the mapper."""
        query = sub_query.text

        # Try direct search URL
        try:
            encoded = query.replace(" ", "+")
            urls = await self.mapper.map_site(
                url=f"https://www.google.com/search?q={encoded}",
                search=query,
                limit=10,
            )
            if urls:
                return urls
        except Exception as e:
            logger.debug(f"Source discovery failed for '{query}': {e}")

        # Try known source hints
        if sub_query.source_hint == "academic":
            search_urls = [
                f"https://scholar.google.com/scholar?q={query.replace(' ', '+')}",
                f"https://arxiv.org/search/?searchby=All&query={query.replace(' ', '+')}",
            ]
        elif sub_query.source_hint == "news":
            search_urls = [
                f"https://news.google.com/search?q={query.replace(' ', '+')}",
            ]
        else:
            search_urls = []

        for search_url in search_urls:
            try:
                urls = await self.mapper.map_site(url=search_url, search=query, limit=10)
                if urls:
                    return urls
            except Exception:
                continue

        return []

    async def _scrape_source(
        self, url: str, relevance_context: str
    ) -> Optional[ResearchSource]:
        """Scrape a single URL and create a ResearchSource."""
        domain = extract_domain(url)
        if not await self.circuit_breaker.can_call(domain):
            logger.debug(f"Circuit open, skipping: {url}")
            return None

        try:
            data = await self.scraper.scrape(
                url=url,
                formats=[OutputFormat.MARKDOWN, OutputFormat.LINKS],
                only_main_content=True,
            )

            await self.circuit_breaker.record_success(domain)

            source = ResearchSource(
                url=url,
                title=data.metadata.get("title", "") if data.metadata else "",
                relevance=self._score_relevance(data.markdown or "", relevance_context),
                summary=self._extract_summary(data.markdown or ""),
                raw_content=data.markdown or "",
            )
            if data.links:
                source.citations = data.links[:10]

            return source

        except Exception as e:
            await self.circuit_breaker.record_failure(domain)
            logger.debug(f"Failed to scrape {url}: {e}")
            return None

    def _score_relevance(self, content: str, query: str) -> float:
        """Simple keyword overlap scoring for relevance."""
        if not content or not query:
            return 0.0
        query_words = set(query.lower().split())
        content_lower = content.lower()
        matches = sum(1 for w in query_words if w in content_lower)
        return min(matches / len(query_words), 1.0)

    def _extract_summary(self, markdown: str, max_length: int = 300) -> str:
        """Extract first meaningful paragraph as summary."""
        if not markdown:
            return ""
        lines = [l.strip() for l in markdown.split("\n") if l.strip()]
        for line in lines:
            if len(line) > 50:
                return line[:max_length]
        return markdown[:max_length]

    async def _detect_contradictions(self, report: ResearchReport) -> None:
        """Detect conflicting claims across sources using the LLM."""
        if len(report.sources) < 2:
            return

        claims = []
        for src in report.sources:
            if src.summary:
                claims.append(f"- [{src.title}]({src.url}): {src.summary[:150]}")

        if len(claims) < 2:
            return

        comparison_prompt = (
            "You are a fact-checker. Given multiple source summaries, identify any "
            "contradictions or conflicting claims. Return a JSON object with a "
            "'contradictions' key containing an array of conflict objects, each with "
            "'claim_a', 'claim_b', 'source_a', 'source_b' fields. "
            "If no contradictions, return {'contradictions': []}.\n\n"
            f"Sources:\n" + "\n".join(claims[:10])
        )

        try:
            messages = [
                {"role": "system", "content": comparison_prompt},
                {"role": "user", "content": "Identify contradictions in the sources above."},
            ]
            raw = await self.extractor._call_llm(messages, schema=None)
            data = json.loads(raw) if isinstance(raw, str) else raw
            for c in data.get("contradictions", []):
                report.add_contradiction(
                    claim_a=c["claim_a"],
                    claim_b=c["claim_b"],
                    source_a=c["source_a"],
                    source_b=c["source_b"],
                )
        except Exception as e:
            logger.debug(f"Contradiction detection failed: {e}")

    async def _identify_gaps(self, report: ResearchReport) -> None:
        """Identify missing aspects of the research topic."""
        if not report.findings:
            return

        gaps_prompt = (
            "You are a research analyst. Given a research query and its findings, "
            "identify 2-4 specific aspects that were NOT covered by the sources. "
            "Return a JSON object with a 'gaps' key containing an array of gap descriptions. "
            "Be specific, not vague.\n\n"
            f"Query: {report.query}\n\n"
            f"Findings:\n" + "\n".join(f"- {f}" for f in report.findings[:10])
        )

        try:
            messages = [
                {"role": "system", "content": gaps_prompt},
                {"role": "user", "content": "What aspects of the research query remain unaddressed?"},
            ]
            raw = await self.extractor._call_llm(messages, schema=None)
            data = json.loads(raw) if isinstance(raw, str) else raw
            for gap in data.get("gaps", []):
                report.add_gap(gap)
        except Exception as e:
            logger.debug(f"Gap identification failed: {e}")

    async def _synthesize(
        self, query: str, report: ResearchReport, passes: int = 2
    ) -> str:
        """Synthesize findings into a final report using multiple LLM passes."""
        if not report.sources:
            return "Insufficient sources to generate a report."

        source_context = []
        for src in sorted(report.sources, key=lambda s: s.relevance, reverse=True)[:10]:
            source_context.append(
                f"## Source: {src.title}\nURL: {src.url}\n"
                f"Summary: {src.summary or src.raw_content[:200]}\n"
            )

        ctx = "\n\n".join(source_context)

        draft_prompt = (
            f"You are a research analyst. Write a comprehensive, balanced report answering: "
            f"**{query}**\n\n"
            f"Use ONLY the sources provided below. Cite sources inline using [Source] notation. "
            f"If a claim appears in only one source, note that it is single-source. "
            f"If sources contradict each other, note the disagreement.\n\n"
            f"Sources:\n{ctx}\n\n"
            f"Structure: Introduction, Key Findings, Analysis, Contradictions (if any), Conclusion."
        )

        messages = [
            {"role": "system", "content": draft_prompt},
            {"role": "user", "content": "Write the comprehensive research report."},
        ]

        try:
            draft = await self.extractor._call_llm(messages, schema=None)
        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            return f"Synthesis failed: {e}"

        # Additional refinement passes
        for i in range(passes - 1):
            refine_prompt = (
                f"Revise this research report to be more thorough, accurate, and well-structured. "
                f"Add missing details, correct any errors, and improve clarity.\n\n"
                f"Current report:\n{draft}"
            )
            try:
                messages = [
                    {"role": "system", "content": refine_prompt},
                    {"role": "user", "content": "Revise and improve the report."},
                ]
                draft = await self.extractor._call_llm(messages, schema=None)
            except Exception as e:
                logger.debug(f"Refinement pass {i+1} failed: {e}")
                break

        return draft

    def _compute_confidence(self, report: ResearchReport) -> float:
        """Compute overall confidence score for the report."""
        if not report.sources:
            return 0.0

        source_score = min(len(report.sources) / 10, 1.0) * 0.4

        if report.sources:
            top_sources = sorted(
                report.sources, key=lambda s: s.relevance, reverse=True
            )[:5]
            relevance_score = (
                sum(s.relevance for s in top_sources) / len(top_sources) * 0.3
            )
        else:
            relevance_score = 0.0

        contradiction_penalty = min(len(report.contradictions) * 0.1, 0.3)

        return max(0.0, min(1.0, source_score + relevance_score + 0.3 - contradiction_penalty))
