"""
Tests for Huginn Scraper — Unit tests for retry logic and error classification.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from huginn.models import ScrapeData, OutputFormat


class TestClassifyError:
    """Test error classification for retry decisions."""

    def test_timeout_error(self):
        from huginn.scraper import classify_error
        result = classify_error(asyncio.TimeoutError("timed out"))
        assert result == ("timeout", 408)

    def test_connection_refused(self):
        from huginn.scraper import classify_error
        result = classify_error(ConnectionRefusedError("refused"))
        assert result == ("connection", 502)

    def test_connection_error(self):
        from huginn.scraper import classify_error
        result = classify_error(ConnectionError("reset"))
        assert result == ("connection", 502)

    def test_generic_error(self):
        from huginn.scraper import classify_error
        result = classify_error(RuntimeError("unknown"))
        assert result == ("unknown", 500)

    def test_type_error(self):
        from huginn.scraper import classify_error
        result = classify_error(TypeError("bad type"))
        assert result == ("unknown", 500)

    def test_value_error(self):
        from huginn.scraper import classify_error
        result = classify_error(ValueError("bad value"))
        assert result == ("unknown", 500)


class TestRetryConstants:
    """Test retry configuration constants."""

    def test_default_max_retries(self):
        from huginn.scraper import DEFAULT_MAX_RETRIES
        assert DEFAULT_MAX_RETRIES == 2

    def test_backoff_durations(self):
        from huginn.scraper import RETRY_BACKOFFS
        assert len(RETRY_BACKOFFS) > 0
        # Backoff should increase
        for i in range(1, len(RETRY_BACKOFFS)):
            assert RETRY_BACKOFFS[i] >= RETRY_BACKOFFS[i - 1]


class TestScraperRetry:
    """Test retry logic in Scraper.scrape()."""

    @pytest.mark.asyncio
    async def test_success_on_first_try(self):
        """No retry needed when scrape succeeds."""
        from huginn.scraper import Scraper
        mock_browser = AsyncMock()
        mock_browser.last_status_code = 200
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
        mock_browser.to_markdown.return_value = "# Hello"

        scraper = Scraper(mock_browser)
        result = await scraper.scrape("https://example.com")

        assert result.metadata["status_code"] == 200
        assert mock_browser.navigate.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_timeout(self):
        """Should retry on TimeoutError."""
        from huginn.scraper import Scraper
        mock_browser = AsyncMock()
        mock_browser.new_context.return_value = AsyncMock()
        mock_browser.new_page.return_value = AsyncMock()
        # Fail twice, succeed third time
        mock_browser.navigate.side_effect = [
            asyncio.TimeoutError("timed out"),
            asyncio.TimeoutError("timed out"),
            True,
        ]
        mock_browser.extract_content.return_value = {
            "url": "https://example.com",
            "title": "OK",
            "description": "",
            "language": "en",
        }
        mock_browser.last_status_code = 200
        mock_browser.to_markdown.return_value = "OK"

        scraper = Scraper(mock_browser)
        result = await scraper.scrape("https://example.com", max_retries=2)

        assert result.metadata["status_code"] == 200
        assert mock_browser.navigate.call_count == 3

    @pytest.mark.asyncio
    async def test_returns_408_after_exhausted_retries(self):
        """Should return 408 when all retries fail on timeout."""
        from huginn.scraper import Scraper
        mock_browser = AsyncMock()
        mock_browser.new_context.return_value = AsyncMock()
        mock_browser.new_page.return_value = AsyncMock()
        mock_browser.navigate.side_effect = asyncio.TimeoutError("timed out")

        scraper = Scraper(mock_browser)
        result = await scraper.scrape("https://example.com", max_retries=1)

        assert result.metadata["status_code"] == 408
        assert "408" in str(result.metadata) or "timed out" in result.metadata.get("error", "").lower() or result.metadata["error"] == "Request timed out"

    @pytest.mark.asyncio
    async def test_no_retry_on_zero_max_retries(self):
        """With max_retries=0, should fail immediately."""
        from huginn.scraper import Scraper
        mock_browser = AsyncMock()
        mock_browser.new_context.return_value = AsyncMock()
        mock_browser.new_page.return_value = AsyncMock()
        mock_browser.navigate.side_effect = asyncio.TimeoutError("timed out")

        scraper = Scraper(mock_browser)
        result = await scraper.scrape("https://example.com", max_retries=0)

        assert result.metadata["status_code"] == 408
        assert mock_browser.navigate.call_count == 1


class TestScrapeRequestMaxRetries:
    """Test that ScrapeRequest model accepts max_retries."""

    def test_default_max_retries(self):
        from huginn.models import ScrapeRequest
        req = ScrapeRequest(url="https://example.com")
        assert req.max_retries == 2

    def test_custom_max_retries(self):
        from huginn.models import ScrapeRequest
        req = ScrapeRequest(url="https://example.com", max_retries=0)
        assert req.max_retries == 0

    def test_max_retries_validation(self):
        import pydantic
        from huginn.models import ScrapeRequest
        with pytest.raises(pydantic.ValidationError):
            ScrapeRequest(url="https://example.com", max_retries=10)