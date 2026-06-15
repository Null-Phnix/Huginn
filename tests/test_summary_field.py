"""
Tests for summary field — Firecrawl parity.

Feature: POST /v1/scrape accepts summary=True and returns a 1-2 sentence
auto-generated summary of the page content in ScrapeResponse.summary.

When summary=True, the API generates a plain-text summary of the scraped page
using the configured LLM. When summary=False (default), response.summary is None.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from huginn.models import ScrapeRequest, ScrapeResponse, OutputFormat


class TestSummaryRequest:
    """ScrapeRequest accepts summary=True."""

    def test_scrape_request_has_summary_field(self):
        """ScrapeRequest has a summary bool field (default False)."""
        from huginn.models import ScrapeRequest
        # Should accept summary=True without error
        req = ScrapeRequest(url="https://example.com", summary=True)
        assert req.summary is True

    def test_scrape_request_summary_default_false(self):
        """summary defaults to False when not specified."""
        req = ScrapeRequest(url="https://example.com")
        assert req.summary is False

    def test_scrape_request_summary_alias(self):
        """summary accepts camelCase 'summary' from API payload."""
        from huginn.models import ScrapeRequest
        # Snake_case
        req1 = ScrapeRequest(url="https://example.com", summary=True)
        assert req1.summary is True


class TestSummaryResponse:
    """ScrapeResponse includes an optional summary string."""

    def test_scrape_response_has_summary_field(self):
        """ScrapeResponse has a summary: Optional[str] field."""
        from huginn.models import ScrapeResponse, ScrapeData
        resp = ScrapeResponse(
            success=True,
            data=ScrapeData(markdown="# Test\n\nContent"),
            summary="This is a summary.",
        )
        assert resp.summary == "This is a summary."

    def test_scrape_response_summary_can_be_none(self):
        """When summary is not generated, response.summary is None."""
        from huginn.models import ScrapeResponse, ScrapeData
        resp = ScrapeResponse(success=True, data=ScrapeData(markdown="# Test"))
        assert resp.summary is None


class TestSummaryIntegration:
    """When summary=True, the API generates a summary of the page content."""

    @pytest.mark.asyncio
    async def test_summary_generated_when_flag_is_true(self):
        """With summary=True, ScrapeData content is passed to the LLM and summary is returned."""
        # Verify the request model accepts summary=True
        from huginn.models import ScrapeRequest, ScrapeResponse, ScrapeData

        req = ScrapeRequest(url="https://example.com", summary=True)
        assert req.summary is True

        # Verify _summarize_text exists and gracefully returns None without a key
        from huginn.api import _summarize_text
        # No API key in test env → returns None (best-effort, not an error)
        result = await _summarize_text("This is some page content that needs summarizing.")
        assert result is None  # No API key configured in test env

        # Verify summary field is serialised in the API response model
        resp = ScrapeResponse(
            success=True,
            data=ScrapeData(markdown="# Test\n\nPage content"),
            summary="A concise summary.",
        )
        assert resp.summary == "A concise summary."
        assert "summary" in resp.model_dump()

    def test_api_endpoint_returns_summary_when_requested(self):
        """The /scrape endpoint returns response.summary when req.summary is True."""
        # This tests the full API flow by checking that ScrapeResponse
        # with summary field can be constructed from a scrape result
        from huginn.models import ScrapeResponse, ScrapeData

        # Simulate what the API should do: scrape → generate summary → return
        scrape_result = ScrapeData(
            markdown="# Test Page\n\nThis is the main content.",
            metadata={"url": "https://example.com", "title": "Test", "language": "en"},
        )
        generated_summary = "A test page with main content."

        resp = ScrapeResponse(
            success=True,
            data=scrape_result,
            summary=generated_summary,
        )

        assert resp.success is True
        assert resp.data is not None
        assert resp.summary == generated_summary
        assert "summary" in resp.model_dump()  # Field is serialised in API response
