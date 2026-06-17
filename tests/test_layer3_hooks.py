"""
Tests for layer-3 hook wiring in the Scraper.

Verifies that:
1. Adaptive throttling (record_outcome) is called after each scrape
2. Selector memory records successful include_tags usage
3. Selector memory surfaces hints when include_tags is empty
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from huginn.circuit_breaker import CircuitBreaker
from huginn.domain_rate_limiter import DomainRateLimiter
from huginn.selector_memory import SelectorMemory


@pytest.fixture(autouse=True)
def _reset_sm_singleton():
    """Reset the SelectorMemory singleton between tests."""
    yield
    import huginn.selector_memory as sm_mod
    sm_mod._selector_memory = None


def _make_mock_browser(status_code=200):
    """Build a mock BrowserManager that returns a successful scrape."""
    mock_browser = AsyncMock()
    mock_browser.last_status_code = status_code
    mock_context = AsyncMock()
    mock_page = AsyncMock()
    mock_browser.new_context.return_value = mock_context
    mock_browser.new_page.return_value = mock_page
    mock_browser.navigate.return_value = True
    mock_browser.extract_content.return_value = {
        "url": "https://example.com",
        "title": "Example",
        "description": "",
        "language": "en",
    }
    mock_browser.to_markdown.return_value = "# Hello World"
    mock_browser.to_html.return_value = "<html></html>"
    return mock_browser


async def _call_through(domain, fn, *args, **kwargs):
    """Side-effect that actually awaits the wrapped async fn."""
    return await fn(*args, **kwargs)


def _make_mock_cb():
    """Circuit breaker mock whose call() actually invokes the wrapped fn."""
    mock_cb = MagicMock(spec=CircuitBreaker)
    mock_cb.is_open.return_value = False
    mock_cb.call = AsyncMock(side_effect=_call_through)
    mock_cb.record_failure = AsyncMock()
    return mock_cb


def _make_mock_rl():
    """Rate limiter mock with async record_outcome."""
    mock_rl = MagicMock(spec=DomainRateLimiter)
    mock_rl.acquire_or_wait = AsyncMock(return_value=0.0)
    mock_rl.record_outcome = AsyncMock()
    return mock_rl


class TestAdaptiveThrottlingWired:
    """Verify record_outcome is called on the rate limiter after scrapes."""

    @pytest.mark.asyncio
    async def test_success_calls_record_outcome_success(self):
        """A successful scrape should call record_outcome(domain, success=True)."""
        from huginn.scraper import Scraper

        mock_browser = _make_mock_browser(200)
        mock_cb = _make_mock_cb()
        mock_rl = _make_mock_rl()

        scraper = Scraper(mock_browser, circuit_breaker=mock_cb, rate_limiter=mock_rl)
        await scraper.scrape("https://example.com", render_mode="full")

        mock_rl.record_outcome.assert_called_once_with("example.com", success=True)

    @pytest.mark.asyncio
    async def test_failure_calls_record_outcome_failure(self):
        """A failed scrape should call record_outcome(domain, success=False)."""
        from huginn.scraper import Scraper

        mock_browser = _make_mock_browser(500)
        mock_cb = MagicMock(spec=CircuitBreaker)
        mock_cb.is_open.return_value = False
        mock_cb.call = AsyncMock(side_effect=RuntimeError("boom"))
        mock_cb.record_failure = AsyncMock()
        mock_rl = _make_mock_rl()

        scraper = Scraper(mock_browser, circuit_breaker=mock_cb, rate_limiter=mock_rl)

        with pytest.raises(RuntimeError):
            await scraper.scrape("https://example.com")

        mock_rl.record_outcome.assert_called_once_with("example.com", success=False)


class TestSelectorMemoryWired:
    """Verify selector memory records and surfaces hints."""

    @pytest.mark.asyncio
    async def test_records_successful_include_tags(self):
        """When include_tags is provided and scrape succeeds, selectors are recorded."""
        from huginn.scraper import Scraper

        mock_browser = _make_mock_browser(200)
        mock_cb = _make_mock_cb()
        mock_rl = _make_mock_rl()

        scraper = Scraper(mock_browser, circuit_breaker=mock_cb, rate_limiter=mock_rl)
        fresh_mem = SelectorMemory()
        scraper._selector_memory = fresh_mem

        await scraper.scrape(
            "https://example.com",
            include_tags=["article", ".content"],
            render_mode="full",
        )

        stats = fresh_mem.get_stats("https://example.com")
        assert stats is not None
        assert stats["article"] == 1
        assert stats[".content"] == 1

    @pytest.mark.asyncio
    async def test_does_not_record_on_error_status(self):
        """When scrape returns 503, selectors should NOT be recorded."""
        from huginn.scraper import Scraper

        mock_browser = _make_mock_browser(503)
        mock_cb = _make_mock_cb()
        mock_rl = _make_mock_rl()

        scraper = Scraper(mock_browser, circuit_breaker=mock_cb, rate_limiter=mock_rl)
        fresh_mem = SelectorMemory()
        scraper._selector_memory = fresh_mem

        await scraper.scrape(
            "https://example.com",
            include_tags=["article"],
            render_mode="full",
        )

        stats = fresh_mem.get_stats("https://example.com")
        assert stats is None

    @pytest.mark.asyncio
    async def test_surfaces_hints_when_include_tags_empty(self):
        """When include_tags is empty and memory has suggestions, hints appear in metadata."""
        from huginn.scraper import Scraper

        mock_browser = _make_mock_browser(200)
        mock_cb = _make_mock_cb()
        mock_rl = _make_mock_rl()

        scraper = Scraper(mock_browser, circuit_breaker=mock_cb, rate_limiter=mock_rl)
        fresh_mem = SelectorMemory()
        fresh_mem.record_success("https://example.com", "article")
        fresh_mem.record_success("https://example.com", "article")
        fresh_mem.record_success("https://example.com", ".content")
        scraper._selector_memory = fresh_mem

        result = await scraper.scrape("https://example.com", render_mode="full")

        assert "selector_hints" in result.metadata
        hints = result.metadata["selector_hints"]
        assert len(hints) > 0
        # article was used twice, .content once — article should rank first
        assert hints[0]["selector"] == "article"
        assert hints[0]["score"] > 0

    @pytest.mark.asyncio
    async def test_no_hints_when_memory_empty(self):
        """When include_tags is empty and memory is empty, no hints key is added."""
        from huginn.scraper import Scraper

        mock_browser = _make_mock_browser(200)
        mock_cb = _make_mock_cb()
        mock_rl = _make_mock_rl()

        scraper = Scraper(mock_browser, circuit_breaker=mock_cb, rate_limiter=mock_rl)
        fresh_mem = SelectorMemory()
        scraper._selector_memory = fresh_mem

        result = await scraper.scrape("https://no-memory.com", render_mode="full")

        assert "selector_hints" not in result.metadata

    @pytest.mark.asyncio
    async def test_does_not_record_when_include_tags_none(self):
        """When include_tags is None, nothing should be recorded in selector memory."""
        from huginn.scraper import Scraper

        mock_browser = _make_mock_browser(200)
        mock_cb = _make_mock_cb()
        mock_rl = _make_mock_rl()

        scraper = Scraper(mock_browser, circuit_breaker=mock_cb, rate_limiter=mock_rl)
        fresh_mem = SelectorMemory()
        scraper._selector_memory = fresh_mem

        await scraper.scrape("https://example.com", render_mode="full")

        stats = fresh_mem.get_stats("https://example.com")
        assert stats is None

    @pytest.mark.asyncio
    async def test_hint_score_has_selector_and_score_fields(self):
        """Hints should be dicts with 'selector' and 'score' keys."""
        from huginn.scraper import Scraper

        mock_browser = _make_mock_browser(200)
        mock_cb = _make_mock_cb()
        mock_rl = _make_mock_rl()

        scraper = Scraper(mock_browser, circuit_breaker=mock_cb, rate_limiter=mock_rl)
        fresh_mem = SelectorMemory()
        fresh_mem.record_success("https://example.com", "main")
        scraper._selector_memory = fresh_mem

        result = await scraper.scrape("https://example.com", render_mode="full")

        hints = result.metadata["selector_hints"]
        for hint in hints:
            assert "selector" in hint
            assert "score" in hint
            assert isinstance(hint["selector"], str)
            assert isinstance(hint["score"], float)