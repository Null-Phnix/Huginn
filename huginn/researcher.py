"""
Huginn Deep Research Agent

Autonomous multi-hop research agent that plans, explores, and synthesizes
information from the web. Goes beyond single-page extraction to conduct
genuine investigative research.

Firecrawl LIMITATION: Single-pass scraping only. No iterative research,
no knowledge synthesis, no source tracking, no confidence scoring.

Huginn SOLUTION: Agentic research loop with:
- Query decomposition and search planning
- Parallel exploration of multiple information sources
- Belief tracking with confidence scores
- Information synthesis with conflict detection
- Full source attribution with quotes
- Structured report generation

Usage:
    researcher = DeepResearcher(browser, llm_provider="openai")
    result = await researcher.research(
        query="What are the key differences between Kubernetes and Docker Swarm?",
        depth=3,
        max_sources=20,
    )
    print(result.report)  # Full synthesized report
    print(result.findings)  # Per-topic findings with confidence
    print(result.citations)  # All sources with quotes
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import httpx

from .browser import BrowserManager
from .circuit_breaker import get_circuit_breaker, extract_domain
from .extractor import Extractor
from .mapper import Mapper
from .models import OutputFormat
from .scraper import Scraper

logger = logging.getLogger(__name__)


# ─── Data Structures ───────────────────────────────────────────────────────────


@dataclass
class Citation:
    """A source citation with the specific information extracted."""
    url: str
    title: str
    domain: str
    quote: str
    relevance_score: float
    timestamp: str
    accessed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "domain": self.domain,
            "quote": self.quote,
            "relevance_score": self.relevance_score,
            "timestamp": self.timestamp,
            "accessed_at": self.accessed_at.isoformat(),
        }


@dataclass
class Finding:
    """A discrete piece of information discovered during research."""
    topic: str
    claim: str
    supporting_citations: List[Citation]
    confidence: float  # 0.0 - 1.0
    contradicts: Optional[str] = None  # Topic this contradicts, if any
    needs_verification: bool = False
    verified: bool = False

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "claim": self.claim,
            "supporting_citations": [c.to_dict() for c in self.supporting_citations],
            "confidence": self.confidence,
            "contradicts": self.contradicts,
            "needs_verification": self.needs_verification,
            "verified": self.verified,
        }


@dataclass
class ResearchPlan:
    """A research plan decomposed from the main query."""
    main_query: str
    sub_questions: List[str]
    search_queries: List[str]
    target_domains: List[str]  # Domains to prioritize
    depth: int
    max_sources: int

    def to_dict(self) -> dict:
        return {
            "main_query": self.main_query,
            "sub_questions": self.sub_questions,
            "search_queries": self.search_queries,
            "target_domains": self.target_domains,
            "depth": self.depth,
            "max_sources": self.max_sources,
        }


@dataclass
class ResearchReport:
    """Final synthesized research report."""
    query: str
    summary: str
    report: str  # Full structured report in markdown
    findings: List[Finding]
    citations: List[Citation]
    knowledge_graph: Dict[str, List[str]]  # topic -> related topics
    unanswered_questions: List[str]
    confidence: float  # Overall confidence in the report
    sources_consulted: int
    research_duration_seconds: float
    depth_achieved: int
    warnings: List[str]  # e.g. "conflicting sources", "low confidence areas"
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "summary": self.summary,
            "report": self.report,
            "findings": [f.to_dict() for f in self.findings],
            "citations": [c.to_dict() for c in self.citations],
            "knowledge_graph": self.knowledge_graph,
            "unanswered_questions": self.unanswered_questions,
            "confidence": self.confidence,
            "sources_consulted": self.sources_consulted,
            "research_duration_seconds": self.research_duration_seconds,
            "depth_achieved": self.depth_achieved,
            "warnings": self.warnings,
            "generated_at": self.generated_at.isoformat(),
        }


@dataclass
class BeliefState:
    """
    Tracks what the agent knows and how confident it is.
    Similar to the mental model in extractor.py but for research.
    """
    facts: Dict[str, float] = field(default_factory=dict)  # claim -> confidence
    sources: Dict[str, List[Citation]] = field(default_factory=dict)  # domain -> citations
    contradictions: List[Tuple[str, str]] = field(default_factory=list)  # (claim_a, claim_b)
    explored_queries: Set[str] = field(default_factory=set)
    explored_urls: Set[str] = field(default_factory=set)
    pending_questions: List[str] = field(default_factory=list)

    def add_fact(self, claim: str, confidence: float, citation: Citation):
        existing = self.facts.get(claim, 0.0)
        self.facts[claim] = max(existing, confidence)
        domain = citation.domain
        if domain not in self.sources:
            self.sources[domain] = []
        self.sources[domain].append(citation)

    def add_contradiction(self, claim_a: str, claim_b: str):
        self.contradictions.append((claim_a, claim_b))

    def get_average_confidence(self) -> float:
        if not self.facts:
            return 0.0
        return sum(self.facts.values()) / len(self.facts)

    def mark_explored_query(self, query: str):
        self.explored_queries.add(query.lower().strip())

    def mark_explored_url(self, url: str):
        self.explored_urls.add(url.lower().strip())

    def was_query_explored(self, query: str) -> bool:
        return query.lower().strip() in self.explored_queries

    def was_url_explored(self, url: str) -> bool:
        return url.lower().strip() in self.explored_urls


# ─── Deep Researcher ───────────────────────────────────────────────────────────


class DeepResearcher:
    """
    Autonomous deep research agent.

    Conducts iterative, multi-hop research by:
    1. Decomposing the query into sub-questions
    2. Searching and scraping in parallel
    3. Extracting claims and building a belief state
    4. Identifying knowledge gaps and planning follow-up searches
    5. Synthesizing findings into a structured report
    """

    def __init__(
        self,
        browser: BrowserManager,
        llm_provider: str = "openai",
        llm_model: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        urls: Optional[List[str]] = None,
        memory: Optional[Any] = None,
    ):
        self.browser = browser
        self.scraper = Scraper(browser)
        self.mapper = Mapper(browser)
        self.extractor = Extractor(
            browser,
            llm_provider=llm_provider,
            llm_model=llm_model,
            mental_model=True,
            http_client=http_client,
        )
        self._cb = get_circuit_breaker()
        self._preprovided_urls = urls or []
        self.memory = memory  # Optional ResearchMemory for persistence

    async def research(
        self,
        query: str,
        depth: int = 3,
        max_sources: int = 20,
        target_length: str = "standard",
        background_questions: Optional[List[str]] = None,
        urls: Optional[List[str]] = None,
    ) -> ResearchReport:
        """
        Conduct deep research on a query.

        Parameters
        ----------
        query : str
            The research question or topic.
        depth : int
            How many iterations of exploration (1-5). Default: 3.
            depth=1: Single search + scrape
            depth=2: Query decomposition + parallel exploration
            depth=3+: Iterative refinement with belief tracking
        max_sources : int
            Maximum number of unique sources to consult. Default: 20.
        target_length : str
            Target report length: "brief" (~500 words), "standard" (~1500 words),
            or "comprehensive" (~4000+ words).
        background_questions : list of str, optional
            Specific sub-questions to investigate alongside the main query.
        urls : list of str, optional
            Pre-specified URLs to scrape before doing any web search or query decomposition.
            These are scraped first using the extractor and their content is incorporated
            into the research before the normal iterative research loop begins.

        Returns
        -------
        ResearchReport
            Complete research report with findings, citations, and confidence scores.
        """
        start_time = time.monotonic()
        logger.info(f"Starting deep research: query={query!r}, depth={depth}, max_sources={max_sources}")

        beliefs = BeliefState()
        warnings: List[str] = []
        all_citations: Dict[str, Citation] = {}
        all_findings: List[Finding] = []
        pending_questions: List[str] = [query]
        if background_questions:
            pending_questions.extend(background_questions)

        # Merge provided URLs with any passed directly to research()
        all_urls = list(self._preprovided_urls)
        if urls:
            all_urls.extend(urls)

        # Phase 0: Scrape pre-provided URLs first, before query decomposition or search
        if all_urls:
            logger.info(f"Scraping {len(all_urls)} pre-provided URLs before research loop")
            for url in all_urls:
                if beliefs.was_url_explored(url):
                    continue
                result = await self._scrape_and_extract(query, url, beliefs)
                if result is None:
                    continue
                citations, new_questions, findings = result
                for citation in citations:
                    all_citations[citation.url] = citation
                all_findings.extend(findings)
                pending_questions.extend(new_questions)

        # Phase 1: Query decomposition and initial search
        plan = await self._decompose_query(query, depth, max_sources)
        pending_questions.extend(plan.sub_questions)
        logger.info(f"Research plan: {len(plan.search_queries)} search queries, depth={depth}")

        current_depth = 0
        sources_count = 0

        # Phase 2: Iterative research loop
        while pending_questions and sources_count < max_sources and current_depth <= depth:
            current_batch = pending_questions[:max_sources - sources_count]
            pending_questions = pending_questions[len(current_batch):]

            # Execute batch in parallel
            results = await asyncio.gather(
                *[self._explore_question(q, plan, beliefs) for q in current_batch],
                return_exceptions=True,
            )

            for result in results:
                if isinstance(result, Exception):
                    logger.warning(f"Question exploration failed: {result}")
                    continue

                if result is None:
                    continue

                citations, new_questions, findings = result
                sources_count += len(citations)

                # Track citations
                for citation in citations:
                    all_citations[citation.url] = citation

                # Update beliefs
                for finding in findings:
                    all_findings.append(finding)

                # Queue new questions for next iteration
                for q in new_questions:
                    if not beliefs.was_query_explored(q):
                        pending_questions.append(q)

                pending_questions.extend(new_questions)

            current_depth += 1

            # Early exit if we've saturated knowledge
            if current_depth >= 2 and not pending_questions:
                break

        # Phase 3: Synthesize report
        duration = time.monotonic() - start_time
        report = await self._synthesize_report(
            query=query,
            findings=all_findings,
            citations=list(all_citations.values()),
            beliefs=beliefs,
            target_length=target_length,
            duration=duration,
            depth_achieved=min(current_depth, depth),
            sources_count=sources_count,
            warnings=warnings,
        )

        logger.info(
            f"Research complete: {sources_count} sources, depth={report.depth_achieved}, "
            f"confidence={report.confidence:.2f}, duration={duration:.1f}s"
        )

        # Persist to vector memory if available
        if self.memory and self.memory.available:
            try:
                await self.memory.store_research(report)
                logger.info("Research report persisted to memory")
            except Exception as e:
                logger.warning("Failed to persist research to memory: %s", e)

        return report

    async def _decompose_query(
        self, query: str, depth: int, max_sources: int
    ) -> ResearchPlan:
        """
        Use LLM to decompose the main query into sub-questions and search queries.
        Falls back to rule-based decomposition if LLM is unavailable.
        """
        try:
            plan = await self._llm_decompose_query(query, depth, max_sources)
            return plan
        except Exception as e:
            logger.warning(f"LLM query decomposition failed, using fallback: {e}")
            return self._rule_based_decompose(query, depth)

    async def _llm_decompose_query(
        self, query: str, depth: int, max_sources: int
    ) -> ResearchPlan:
        """Use LLM to intelligently decompose the research query."""
        system_prompt = (
            "You are a research assistant. Decompose the following research query into:\n"
            "1. 3-7 specific sub-questions that need to be answered\n"
            "2. 5-10 search queries that would find relevant information\n"
            "3. Any specific domains known to have authoritative information on this topic\n\n"
            "Output a JSON object with: sub_questions[], search_queries[], target_domains[]\n"
            "Be specific and actionable. Focus on factual topics, not opinions."
        )

        result = await self.extractor.extract(
            urls=["https://en.wikipedia.org/wiki/Special:Search"],
            prompt=(
                f"Research query: {query}\n\n"
                "Decompose this into sub-questions, search queries, and identify authoritative domains. "
                "Return ONLY valid JSON."
            ),
            schema={
                "type": "object",
                "properties": {
                    "sub_questions": {"type": "array", "items": {"type": "string"}},
                    "search_queries": {"type": "array", "items": {"type": "string"}},
                    "target_domains": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["sub_questions", "search_queries"],
            },
            system_prompt=system_prompt,
            output_format="json",
        )

        if isinstance(result, dict) and not result.get("error"):
            return ResearchPlan(
                main_query=query,
                sub_questions=result.get("sub_questions", [])[:7],
                search_queries=result.get("search_queries", [])[:10],
                target_domains=result.get("target_domains", [])[:5],
                depth=depth,
                max_sources=max_sources,
            )

        return self._rule_based_decompose(query, depth)

    def _rule_based_decompose(self, query: str, depth: int) -> ResearchPlan:
        """Fallback decomposition without LLM."""
        sub_questions = [
            f"What is {query}?",
            f"History of {query}",
            f"Key facts about {query}",
            f"Pros and cons of {query}",
            f"How does {query} work?",
        ]
        return ResearchPlan(
            main_query=query,
            sub_questions=sub_questions[:5],
            search_queries=[query, f"{query} definition", f"{query} explained", f"{query} guide"],
            target_domains=[],
            depth=depth,
            max_sources=20,
        )

    async def _explore_question(
        self,
        question: str,
        plan: ResearchPlan,
        beliefs: BeliefState,
    ) -> Optional[Tuple[List[Citation], List[str], List[Finding]]]:
        """
        Explore a single research question:
        - Map relevant sites
        - Extract information
        - Identify new questions
        """
        if beliefs.was_query_explored(question):
            return None

        beliefs.mark_explored_query(question)

        citations: List[Citation] = []
        new_questions: List[str] = []
        findings: List[Finding] = []

        # Search for relevant URLs using the mapper
        try:
            search_urls = await self._find_relevant_urls(question, plan)
        except Exception as e:
            logger.warning(f"URL finding failed for '{question}': {e}")
            return None

        if not search_urls:
            return None

        # Scrape top URLs in parallel (limit concurrency)
        sem = asyncio.Semaphore(3)
        scrape_tasks = [
            self._scrape_and_extract(question, url, beliefs)
            for url in search_urls[:5]  # Top 5 per question
        ]

        results = await asyncio.gather(*scrape_tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                continue
            if result is None:
                continue

            c, nq, f = result
            citations.extend(c)
            new_questions.extend(nq)
            findings.extend(f)

        # Deduplicate new questions
        seen = beliefs.explored_queries
        new_questions = [q for q in new_questions if q.lower().strip() not in seen][:5]

        return citations, new_questions, findings

    async def _find_relevant_urls(
        self, question: str, plan: ResearchPlan
    ) -> List[str]:
        """Find relevant URLs for a question using sitemap + search."""
        urls = []

        # Try sitemap from common sources
        for domain_hint in plan.target_domains[:3]:
            try:
                if not domain_hint.startswith("http"):
                    domain_hint = f"https://{domain_hint}"
                sitemap_url = f"{domain_hint}/sitemap.xml"
                mapped = await self.mapper.map_site(sitemap_url, limit=5)
                urls.extend(mapped[:5])
            except Exception:
                pass

        # Also search directly
        try:
            search_url = f"https://www.google.com/search?q={question.replace(' ', '+')}&num=5"
            domain = extract_domain(search_url)
            if not self._cb.is_open(domain):
                scraped = await self.scraper.scrape(
                    url=search_url,
                    formats=[OutputFormat.MARKDOWN],
                    only_main_content=False,
                    timeout=15000,
                )
                if scraped and scraped.markdown:
                    # Extract URLs from search results
                    found = re.findall(r'https?://[^\s<>"\']+', scraped.markdown)
                    # Deduplicate and filter
                    seen = set()
                    for url in found:
                        parsed = urlparse(url)
                        if parsed.netloc and url not in seen:
                            seen.add(url)
                            urls.append(url)
                            if len(urls) >= 5:
                                break
        except Exception as e:
            logger.debug(f"Search URL extraction failed: {e}")

        # Also try Bing
        try:
            bing_url = f"https://www.bing.com/search?q={question.replace(' ', '+')}&count=5"
            domain = extract_domain(bing_url)
            if not self._cb.is_open(domain):
                scraped = await self.scraper.scrape(
                    url=bing_url,
                    formats=[OutputFormat.MARKDOWN],
                    only_main_content=False,
                    timeout=15000,
                )
                if scraped and scraped.markdown:
                    found = re.findall(r'https?://[^\s<>"\']+', scraped.markdown)
                    seen = set(urls)
                    for url in found:
                        parsed = urlparse(url)
                        if parsed.netloc and url not in seen:
                            seen.add(url)
                            urls.append(url)
                            if len(urls) >= 5:
                                break
        except Exception:
            pass

        # Deduplicate
        seen = set()
        unique = []
        for url in urls:
            if url not in seen:
                seen.add(url)
                unique.append(url)

        return unique[:10]

    async def _scrape_and_extract(
        self, question: str, url: str, beliefs: BeliefState
    ) -> Optional[Tuple[List[Citation], List[str], List[Finding]]]:
        """Scrape a URL and extract information relevant to the question."""
        if beliefs.was_url_explored(url):
            return None

        beliefs.mark_explored_url(url)

        domain = extract_domain(url)
        if self._cb.is_open(domain):
            return None

        try:
            scraped = await self.scraper.scrape(
                url=url,
                formats=[OutputFormat.MARKDOWN, OutputFormat.LINKS],
                only_main_content=True,
                timeout=20000,
            )
        except Exception as e:
            logger.debug(f"Failed to scrape {url}: {e}")
            await self._cb.record_failure(domain)
            return None

        if not scraped or not scraped.markdown:
            return None

        await self._cb.record_success(domain)

        content = scraped.markdown
        title = scraped.metadata.get("title", url) if scraped.metadata else url

        # Extract a relevant quote (first substantial paragraph)
        quote = self._extract_relevant_quote(content, question)

        citation = Citation(
            url=url,
            title=title,
            domain=domain,
            quote=quote,
            relevance_score=self._score_relevance(content, question),
            timestamp=scraped.metadata.get("publish_date", "") if scraped.metadata else "",
        )

        # Try to extract structured claims using LLM
        findings: List[Finding] = []
        new_questions: List[str] = []

        try:
            extracted = await self.extractor.extract(
                urls=[url],
                prompt=f"Based on this content about '{question}', list key facts and claims. "
                       f"Also identify what information might be missing or needs further research. "
                       f"Return a JSON array of objects with: claim, topic, needs_verification",
                schema={
                    "type": "object",
                    "properties": {
                        "claims": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "claim": {"type": "string"},
                                    "topic": {"type": "string"},
                                    "needs_verification": {"type": "boolean"},
                                },
                                "required": ["claim", "topic"],
                            },
                        },
                        "follow_up_questions": {"type": "array", "items": {"type": "string"}},
                    },
                },
                output_format="json",
            )

            if isinstance(extracted, dict) and not extracted.get("error"):
                for c in extracted.get("claims", []):
                    finding = Finding(
                        topic=c.get("topic", "general"),
                        claim=c.get("claim", ""),
                        supporting_citations=[citation],
                        confidence=0.7 if not c.get("needs_verification") else 0.4,
                        needs_verification=c.get("needs_verification", False),
                    )
                    findings.append(finding)
                    beliefs.add_fact(finding.claim, finding.confidence, citation)

                new_questions = extracted.get("follow_up_questions", [])[:3]

        except Exception as e:
            logger.debug(f"LLM extraction failed for {url}: {e}")

        return [citation], new_questions, findings

    async def _synthesize_report(
        self,
        query: str,
        findings: List[Finding],
        citations: List[Citation],
        beliefs: BeliefState,
        target_length: str,
        duration: float,
        depth_achieved: int,
        sources_count: int,
        warnings: List[str],
    ) -> ResearchReport:
        """Synthesize all findings into a structured research report."""

        # Sort findings by confidence
        sorted_findings = sorted(findings, key=lambda f: f.confidence, reverse=True)

        # Build knowledge graph
        knowledge_graph: Dict[str, List[str]] = {}
        for finding in sorted_findings:
            topic = finding.topic
            if topic not in knowledge_graph:
                knowledge_graph[topic] = []
            # Add related topics from claims
            words = finding.claim.split()[:5]
            for w in words:
                if len(w) > 4 and w.lower() != topic.lower():
                    knowledge_graph[topic].append(w.lower())

        # Identify contradictions
        contradiction_warnings = []
        for a, b in beliefs.contradictions:
            contradiction_warnings.append(f"Contradiction detected: '{a}' vs '{b}'")
        warnings.extend(contradiction_warnings[:3])

        # Check for low-confidence areas
        low_conf_findings = [f for f in sorted_findings if f.confidence < 0.5]
        if low_conf_findings:
            warnings.append(
                f"{len(low_conf_findings)} findings have low confidence and may need verification"
            )

        # Generate summary
        summary = self._generate_summary(query, sorted_findings, citations)

        # Generate full report text
        report_body = self._generate_report_body(
            query, sorted_findings, citations, target_length
        )

        # Identify unanswered questions
        unanswered = [
            q for q in beliefs.pending_questions
            if not any(f.claim.lower() in q.lower() or q.lower() in f.claim.lower()
                      for f in sorted_findings)
        ]

        overall_confidence = beliefs.get_average_confidence()

        return ResearchReport(
            query=query,
            summary=summary,
            report=report_body,
            findings=sorted_findings,
            citations=sorted(citations, key=lambda c: c.relevance_score, reverse=True),
            knowledge_graph=knowledge_graph,
            unanswered_questions=unanswered[:10],
            confidence=overall_confidence,
            sources_consulted=sources_count,
            research_duration_seconds=duration,
            depth_achieved=depth_achieved,
            warnings=warnings,
        )

    def _extract_relevant_quote(self, content: str, question: str) -> str:
        """Extract the most relevant quote from content to the question."""
        if not content:
            return ""

        # Split into sentences
        sentences = re.split(r'[.!?]+', content)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 30]

        if not sentences:
            return content[:500]

        # Score each sentence by keyword overlap with question
        question_words = set(question.lower().split())
        scored = []
        for s in sentences:
            words = set(s.lower().split())
            overlap = len(question_words & words)
            scored.append((overlap, s))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1][:500] if scored else sentences[0][:500]

    def _score_relevance(self, content: str, question: str) -> float:
        """Score how relevant content is to the question (0.0 - 1.0)."""
        content_lower = content.lower()
        question_lower = question.lower()
        question_words = set(question_lower.split())

        if not question_words:
            return 0.5

        # Check for question keywords in content
        matches = sum(1 for w in question_words if w in content_lower)
        base_score = matches / len(question_words)

        # Boost for question appearing as substring
        if question_lower in content_lower:
            base_score = min(1.0, base_score + 0.3)

        # Length bonus (substantial content is more useful)
        length_bonus = min(0.1, len(content) / 50000)
        return min(1.0, base_score + length_bonus)

    def _generate_summary(
        self, query: str, findings: List[Finding], citations: List[Citation]
    ) -> str:
        """Generate a brief summary of the research."""
        if not findings:
            return f"Research on '{query}' did not yield sufficient information to form conclusions."

        top_findings = findings[:5]
        claims = " ".join(f"- {f.claim}" for f in top_findings)
        return (
            f"Research on '{query}' consulted {len(citations)} sources and produced "
            f"{len(findings)} findings. Top conclusions: {claims[:300]}..."
        )

    def _generate_report_body(
        self,
        query: str,
        findings: List[Finding],
        citations: List[Citation],
        target_length: str,
    ) -> str:
        """Generate the full markdown report body."""
        length_map = {"brief": 500, "standard": 1500, "comprehensive": 4000}
        target_words = length_map.get(target_length, 1500)

        lines = [f"# Research Report: {query}\n"]
        lines.append(f"\n*Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*\n")

        # Executive summary
        lines.append("## Executive Summary\n")
        high_conf = [f for f in findings if f.confidence >= 0.7]
        lines.append(
            f"This research examined '{query}' across {len(citations)} sources and "
            f"identified {len(findings)} distinct findings. "
            f"{len(high_conf)} findings are supported by high-confidence evidence.\n"
        )

        # Group findings by topic
        topics: Dict[str, List[Finding]] = {}
        for f in findings:
            topic = f.topic
            if topic not in topics:
                topics[topic] = []
            topics[topic].append(f)

        lines.append("\n## Findings by Topic\n")
        for topic, topic_findings in topics.items():
            lines.append(f"### {topic.title()}\n")
            for f in topic_findings:
                conf_label = self._confidence_label(f.confidence)
                lines.append(f"**[{conf_label}]** {f.claim}\n")
                if f.supporting_citations:
                    lines.append(f"  *Sources: {', '.join(c.domain for c in f.supporting_citations[:2])}*\n")
            lines.append("\n")

        # Citations section
        lines.append("## Sources\n")
        for c in citations[:20]:
            lines.append(f"- [{c.title}]({c.url}) ({c.domain})\n")
            if c.quote:
                lines.append(f"  > \"{c.quote[:200]}...\"\n")

        # Warnings
        low_conf = [f for f in findings if f.confidence < 0.5]
        if low_conf:
            lines.append("\n## Areas Requiring Verification\n")
            for f in low_conf[:5]:
                lines.append(f"- **{f.claim}** (confidence: {f.confidence:.0%})\n")

        report = "".join(lines)

        # Truncate or pad to target length
        words = report.split()
        if len(words) > target_words * 1.5:
            # Truncate intelligently at section boundary
            truncated = " ".join(words[:target_words])
            last_section = truncated.rfind("\n##")
            if last_section > target_words * 0.7:
                report = truncated[:last_section]
            else:
                report = truncated

        return report

    def _confidence_label(self, confidence: float) -> str:
        if confidence >= 0.8:
            return "HIGH CONFIDENCE"
        elif confidence >= 0.6:
            return "MEDIUM CONFIDENCE"
        elif confidence >= 0.4:
            return "LOW CONFIDENCE"
        else:
            return "UNVERIFIED"
