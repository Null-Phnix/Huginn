"""Huginn Searcher — resilient keyless search through StarSearch.

Production search renders independent Bing and Brave Search pages in the
shared StarSearch browser runtime. A process-local health registry scores
latency and success, opens bounded circuits after repeated failures, and makes
every engine selection/failure visible in the API response.
"""

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .browser import BrowserManager
from .models import (
    OutputFormat,
    SearchEngineAttempt,
    SearchEngineError,
    SearchEngineHealth,
    SearchMetadata,
    SearchResultItem,
)
from .scraper import Scraper
from .utils import build_egress_metadata, proxy_failure_likely, scrape_failure

logger = logging.getLogger(__name__)

STARSEARCH_ENGINE_NAMES = ("bing", "brave")


@dataclass
class _EngineState:
    """Mutable health state for one search engine."""

    success_ema: float
    latency_ema_ms: float
    attempts: int = 0
    successes: int = 0
    consecutive_failures: int = 0
    circuit_open_until: float = 0.0
    last_error_code: Optional[str] = None


class SearchEngineHealthRegistry:
    """Thread-safe, process-local engine scoring and circuit breaker.

    Health is intentionally runtime state rather than durable user state: a
    daemon/container restart provides a clean probe window, while every search
    response exposes the current evidence. Two consecutive failures open an
    engine for 30 seconds; the other independent rendered engine remains
    eligible.
    """

    def __init__(self, *, failure_threshold: int = 2, cooldown_seconds: float = 30.0):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        # A small Bing prior preserves the historical primary until measured
        # health provides a reason to choose Brave first.
        self._states = {
            "bing": _EngineState(success_ema=0.90, latency_ema_ms=1800.0),
            "brave": _EngineState(success_ema=0.88, latency_ema_ms=1900.0),
        }
        self._lock = threading.Lock()

    @staticmethod
    def _score(state: _EngineState) -> float:
        latency_penalty = min(state.latency_ema_ms, 15_000.0) / 300.0
        return round(max(0.0, state.success_ema * 100.0 - latency_penalty), 2)

    def select(
        self,
        *,
        requested: str = "auto",
        fallback_chain: bool = True,
    ) -> Tuple[List[str], List[str]]:
        """Return eligible engines in score order plus currently open engines."""
        now = time.monotonic()
        with self._lock:
            names = [requested] if requested != "auto" else list(STARSEARCH_ENGINE_NAMES)
            eligible = [name for name in names if self._states[name].circuit_open_until <= now]
            opened = [name for name in names if self._states[name].circuit_open_until > now]
            eligible.sort(key=lambda name: self._score(self._states[name]), reverse=True)
            if requested == "auto" and not fallback_chain:
                eligible = eligible[:1]
            return eligible, opened

    def record_success(self, engine: str, latency_ms: int) -> None:
        with self._lock:
            state = self._states[engine]
            state.attempts += 1
            state.successes += 1
            state.consecutive_failures = 0
            state.circuit_open_until = 0.0
            state.last_error_code = None
            state.success_ema = state.success_ema * 0.7 + 0.3
            state.latency_ema_ms = state.latency_ema_ms * 0.7 + max(0, latency_ms) * 0.3

    def record_failure(self, engine: str, latency_ms: int, error_code: str) -> None:
        now = time.monotonic()
        with self._lock:
            state = self._states[engine]
            state.attempts += 1
            state.consecutive_failures += 1
            state.last_error_code = error_code
            state.success_ema *= 0.7
            state.latency_ema_ms = state.latency_ema_ms * 0.7 + max(0, latency_ms) * 0.3
            if state.consecutive_failures >= self.failure_threshold:
                state.circuit_open_until = now + self.cooldown_seconds

    def snapshot(self) -> Dict[str, SearchEngineHealth]:
        now = time.monotonic()
        with self._lock:
            result: Dict[str, SearchEngineHealth] = {}
            for name, state in self._states.items():
                open_for = max(0.0, state.circuit_open_until - now)
                if open_for > 0:
                    status = "open"
                elif state.attempts == 0:
                    status = "untried"
                elif state.consecutive_failures:
                    status = "degraded"
                else:
                    status = "healthy"
                result[name] = SearchEngineHealth(
                    status=status,
                    score=self._score(state),
                    attempts=state.attempts,
                    successes=state.successes,
                    consecutive_failures=state.consecutive_failures,
                    latency_ema_ms=round(state.latency_ema_ms, 2),
                    circuit_open_for_seconds=round(open_for, 2),
                    last_error_code=state.last_error_code,
                )
            return result


SEARCH_ENGINE_HEALTH = SearchEngineHealthRegistry()

class Searcher:
    """Health-scored Bing/Brave web search rendered only by StarSearch."""

    def __init__(
        self,
        browser: BrowserManager,
        fallback_chain: bool = True,
        *,
        health_registry: Optional[SearchEngineHealthRegistry] = None,
        proxy_provider: Any = None,
        proxy_lease: Any = None,
    ):
        self.browser = browser
        self.scraper = Scraper(browser)
        self.fallback_chain = fallback_chain
        self.health_registry = health_registry or SEARCH_ENGINE_HEALTH
        self.proxy_provider = proxy_provider
        self.proxy_lease = proxy_lease
        self.proxy = proxy_lease.as_browser_proxy() if proxy_lease else None
        self.last_metadata = SearchMetadata(engines=self.health_registry.snapshot())

    def _report_proxy_success(self) -> None:
        if self.proxy_lease:
            self.proxy_lease.report_success()

    def _report_proxy_failure(self, exc: Exception) -> None:
        if self.proxy_lease and proxy_failure_likely(message=str(exc)):
            self.proxy_lease.report_failure(str(exc))

    def _egress_metadata(self, existing: Any) -> Any:
        if self.proxy_provider is None or self.proxy_lease is None:
            return existing
        return build_egress_metadata(existing, self.proxy_provider, self.proxy_lease)

    @staticmethod
    def _classify_engine_error(exc: Exception) -> SearchEngineError:
        message = str(exc) or type(exc).__name__
        lowered = message.lower()
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "timeout" in lowered:
            code = "timeout"
        elif isinstance(exc, (ConnectionError, OSError)) or "connection" in lowered:
            code = "connection_error"
        elif "capacityexceeded" in lowered or "capacity" in lowered:
            code = "service_unavailable"
        elif "captcha" in lowered or "challenge" in lowered:
            code = "captcha_detected"
        elif "navigate" in lowered:
            code = "navigation_failed"
        else:
            code = "upstream_error"
        return SearchEngineError(code=code, message=message, retryable=True)

    async def search(
        self,
        query: str,
        limit: int = 5,
        scrape_formats: Optional[List[OutputFormat]] = None,
        tbs: Optional[str] = None,
        country: Optional[str] = None,
        language: Optional[str] = None,
        engine: str = "auto",
        scrape_results: bool = True,
        scrape_kwargs: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResultItem]:
        """
        Search the web through health-selected StarSearch-rendered engines.

        Args:
            query: Search query
            limit: Number of results to return
            scrape_formats: Formats for scraped results
            tbs: Reserved search filter for schema compatibility
            country: Reserved localization hint for schema compatibility
            language: Reserved language hint for schema compatibility
            engine: ``auto``, ``bing``, or ``brave``
            scrape_results: Whether to scrape result URLs (disable for fast search-only)
        """
        if scrape_formats is None:
            scrape_formats = [OutputFormat.MARKDOWN]
        if engine not in {"auto", *STARSEARCH_ENGINE_NAMES}:
            raise ValueError(f"unsupported search engine: {engine}")

        from . import starsearch_scrape

        ordered, opened = self.health_registry.select(
            requested=engine,
            fallback_chain=self.fallback_chain,
        )
        attempts: List[SearchEngineAttempt] = [
            SearchEngineAttempt(
                engine=name,
                status="circuit_open",
                error=SearchEngineError(
                    code="circuit_open",
                    message=f"{name} search circuit is cooling down",
                    retryable=True,
                ),
            )
            for name in opened
        ]
        executed = 0
        for name in ordered:
            executed += 1
            started = time.monotonic()
            try:
                results = await starsearch_scrape.search_web(
                    name,
                    query,
                    limit,
                    proxy=self.proxy,
                )
            except Exception as exc:
                self._report_proxy_failure(exc)
                latency_ms = max(0, round((time.monotonic() - started) * 1000))
                error = self._classify_engine_error(exc)
                self.health_registry.record_failure(name, latency_ms, error.code)
                attempts.append(
                    SearchEngineAttempt(
                        engine=name,
                        status="error",
                        latency_ms=latency_ms,
                        error=error,
                    )
                )
                logger.warning("StarSearch->%s search failed: %s", name, exc)
                continue

            latency_ms = max(0, round((time.monotonic() - started) * 1000))
            if not results:
                error = SearchEngineError(
                    code="empty_results",
                    message=f"{name} rendered successfully but yielded no web results",
                    retryable=True,
                )
                self.health_registry.record_failure(name, latency_ms, error.code)
                attempts.append(
                    SearchEngineAttempt(
                        engine=name,
                        status="empty",
                        latency_ms=latency_ms,
                        error=error,
                    )
                )
                logger.warning("StarSearch->%s yielded no parseable web results", name)
                continue

            self.health_registry.record_success(name, latency_ms)
            self._report_proxy_success()
            attempts.append(
                SearchEngineAttempt(
                    engine=name,
                    status="success",
                    latency_ms=latency_ms,
                    result_count=len(results),
                )
            )
            self.last_metadata = SearchMetadata(
                selected_engine=name,
                fallback_used=executed > 1 or bool(opened),
                attempts=attempts,
                engines=self.health_registry.snapshot(),
            )
            annotated = [{**result, "engine": name, "render_mode": "starsearch"} for result in results]
            logger.info("Using StarSearch->%s for query with %d results", name, len(results))
            if scrape_results:
                return await self._scrape_results(annotated, scrape_formats, scrape_kwargs)
            return [
                SearchResultItem(
                    metadata={
                        "title": result.get("title", ""),
                        "url": result.get("link", ""),
                        "snippet": result.get("snippet", ""),
                        "engine": name,
                        "render_mode": "starsearch",
                        "egress": self._egress_metadata(result.get("_egress")),
                    }
                )
                for result in results[:limit]
            ]

        self.last_metadata = SearchMetadata(
            attempts=attempts,
            engines=self.health_registry.snapshot(),
        )
        logger.error("All eligible StarSearch-rendered search engines failed")
        return []

    async def _scrape_results(
        self,
        results: List[Dict],
        scrape_formats: List[OutputFormat],
        scrape_kwargs: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResultItem]:
        """Scrape content from search result URLs concurrently."""
        scraped_items = []

        # Scrape top results with limited concurrency
        sem = asyncio.Semaphore(3)

        async def scrape_one(result: Dict) -> Optional[SearchResultItem]:
            async with sem:
                try:
                    kwargs = {
                        "only_main_content": True,
                        "timeout": 15000,
                        **(scrape_kwargs or {}),
                        "url": result["link"],
                        "formats": scrape_formats,
                        "proxy": self.proxy,
                    }
                    data = await self.scraper.scrape(**kwargs)
                    failure = scrape_failure(data)
                    if failure:
                        raise RuntimeError(failure[1])
                    self._report_proxy_success()
                    if self.proxy_provider is not None and self.proxy_lease is not None:
                        data.metadata = data.metadata or {}
                        data.metadata["egress"] = self._egress_metadata(
                            data.metadata.get("egress")
                        )
                    item = SearchResultItem(
                        markdown=data.markdown,
                        html=data.html,
                        metadata={
                            "title": result.get("title", ""),
                            "url": result.get("link", ""),
                            "snippet": result.get("snippet", ""),
                            "engine": result.get("engine", ""),
                            "render_mode": result.get("render_mode", "starsearch"),
                            **(data.metadata or {}),
                        }
                    )
                    return item
                except Exception as e:
                    self._report_proxy_failure(e)
                    logger.warning(f"Failed to scrape {result.get('link', '')}: {e}")
                    # Return just the search snippet
                    return SearchResultItem(
                        metadata={
                            "title": result.get("title", ""),
                            "url": result.get("link", ""),
                            "snippet": result.get("snippet", ""),
                            "engine": result.get("engine", ""),
                            "render_mode": result.get("render_mode", "starsearch"),
                        }
                    )

        tasks = [scrape_one(r) for r in results]
        items = await asyncio.gather(*tasks, return_exceptions=True)

        for item in items:
            if isinstance(item, SearchResultItem):
                scraped_items.append(item)

        return scraped_items
