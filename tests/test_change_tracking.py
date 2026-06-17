"""
Tests for changeTracking diff output — Firecrawl parity.

Feature: POST /v1/scrape accepts changeTracking: True. When set, the
response includes a `change_tracking` field with:
  - previous_hash: hash of last successful scrape (or None on first)
  - current_hash:  hash of this scrape
  - diff:          unified diff between previous and current content
  - changed:       bool — True if content changed since last scrape

The change tracker stores content + hash per URL so future scrapes can
compare against the last-seen state.
"""

import pytest
import hashlib
from unittest.mock import AsyncMock, MagicMock
from pydantic import TypeAdapter

from huginn.change_tracker import ChangeTracker


# ─── ChangeTracker unit tests ────────────────────────────────────────────────

class TestChangeTrackerHashing:
    """ChangeTracker computes stable SHA-256 hashes."""

    def test_compute_hash_returns_hex_string(self):
        """compute_hash returns a hex string."""
        ct = ChangeTracker()
        h = ct.compute_hash("hello world")
        assert isinstance(h, str)
        assert all(c in "0123456789abcdef" for c in h)

    def test_compute_hash_is_deterministic(self):
        """Same content always produces same hash."""
        ct = ChangeTracker()
        assert ct.compute_hash("hello") == ct.compute_hash("hello")

    def test_compute_hash_different_content_different_hash(self):
        """Different content produces different hash."""
        ct = ChangeTracker()
        assert ct.compute_hash("hello") != ct.compute_hash("world")

    def test_compute_hash_matches_sha256_prefix(self):
        """compute_hash matches hashlib.sha256(content).hexdigest()[:16]."""
        ct = ChangeTracker()
        content = "test content"
        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        assert ct.compute_hash(content) == expected

    def test_compute_hash_handles_unicode(self):
        """compute_hash works on unicode (e.g. non-ASCII markdown)."""
        ct = ChangeTracker()
        h = ct.compute_hash("héllo wörld 🌍")
        assert isinstance(h, str)
        assert len(h) > 0

    def test_compute_hash_handles_empty_string(self):
        """compute_hash of empty string is a valid (deterministic) hash."""
        ct = ChangeTracker()
        h = ct.compute_hash("")
        assert isinstance(h, str)
        assert len(h) > 0


class TestChangeTrackerDiffing:
    """ChangeTracker computes unified diffs between content versions."""

    def test_compute_diff_no_change_returns_empty(self):
        """compute_diff returns '' for identical content."""
        ct = ChangeTracker()
        assert ct.compute_diff("hello\n", "hello\n") == ""

    def test_compute_diff_added_line(self):
        """compute_diff shows added lines with + prefix."""
        ct = ChangeTracker()
        diff = ct.compute_diff("line1\n", "line1\nline2\n")
        assert "+line2" in diff or "+line2\n" in diff

    def test_compute_diff_removed_line(self):
        """compute_diff shows removed lines with - prefix."""
        ct = ChangeTracker()
        diff = ct.compute_diff("line1\nline2\n", "line1\n")
        assert "-line2" in diff or "-line2\n" in diff

    def test_compute_diff_changed_line(self):
        """compute_diff shows both - and + for a changed line."""
        ct = ChangeTracker()
        diff = ct.compute_diff("original\n", "modified\n")
        assert "-original" in diff or "-original\n" in diff
        assert "+modified" in diff or "+modified\n" in diff


class TestChangeTrackerStoreAndCheck:
    """ChangeTracker.check_and_store persists content + hash per URL."""

    @pytest.mark.asyncio
    async def test_first_scrape_has_no_previous_hash(self):
        """First check_and_store for a URL has previous_hash=None."""
        ct = ChangeTracker()
        result = await ct.check_and_store("https://example.com", "content v1")
        assert result["previous_hash"] is None
        assert result["current_hash"] is not None
        assert result["changed"] is False  # first time, nothing to compare
        assert result["diff"] == ""

    @pytest.mark.asyncio
    async def test_second_scrape_same_content_unchanged(self):
        """Second check_and_store with same content: changed=False, diff empty."""
        ct = ChangeTracker()
        await ct.check_and_store("https://example.com", "content v1")
        result = await ct.check_and_store("https://example.com", "content v1")
        assert result["previous_hash"] is not None
        assert result["current_hash"] == result["previous_hash"]
        assert result["changed"] is False
        assert result["diff"] == ""

    @pytest.mark.asyncio
    async def test_second_scrape_different_content_changed(self):
        """Second check_and_store with different content: changed=True, diff non-empty."""
        ct = ChangeTracker()
        await ct.check_and_store("https://example.com", "line1\n")
        result = await ct.check_and_store("https://example.com", "line1\nline2\n")
        assert result["previous_hash"] is not None
        assert result["current_hash"] != result["previous_hash"]
        assert result["changed"] is True
        assert "+line2" in result["diff"]

    @pytest.mark.asyncio
    async def test_third_scrape_replaces_previous(self):
        """check_and_store replaces the stored entry each time (FIFO)."""
        ct = ChangeTracker()
        await ct.check_and_store("https://example.com", "v1")
        await ct.check_and_store("https://example.com", "v2")
        result = await ct.check_and_store("https://example.com", "v3")
        # The "previous" should be v2 (not v1) since we replaced
        assert result["previous_hash"] == ct.compute_hash("v2")

    @pytest.mark.asyncio
    async def test_different_urls_independent(self):
        """Two URLs have independent state in the tracker."""
        ct = ChangeTracker()
        await ct.check_and_store("https://a.com", "a-v1")
        result = await ct.check_and_store("https://b.com", "b-v1")
        # https://b.com has no previous state
        assert result["previous_hash"] is None


class TestChangeTrackerConcurrency:
    """ChangeTracker is safe for concurrent use."""

    @pytest.mark.asyncio
    async def test_concurrent_check_and_store_doesnt_corrupt(self):
        """Many concurrent check_and_store calls on the same URL all succeed."""
        import asyncio
        ct = ChangeTracker()
        url = "https://example.com"

        async def store(i: int):
            await ct.check_and_store(url, f"content {i}")

        await asyncio.gather(*(store(i) for i in range(20)))
        # Last writer wins — we can read the state
        result = await ct.check_and_store(url, "final content")
        assert result["current_hash"] == ct.compute_hash("final content")


# ─── ScrapeRequest + ScrapeData model field ──────────────────────────────────

class TestChangeTrackingModelFields:
    """ScrapeRequest.change_tracking + ScrapeData.change_tracking fields."""

    def test_scrape_request_has_change_tracking_field(self):
        """ScrapeRequest.change_tracking exists, defaults to False."""
        from huginn.models import ScrapeRequest
        req = ScrapeRequest(url="https://example.com")
        assert hasattr(req, "change_tracking")
        assert req.change_tracking is False

    def test_scrape_request_change_tracking_camel_alias(self):
        """changeTracking (camelCase) accepted via alias."""
        from huginn.models import ScrapeRequest
        adapter = TypeAdapter(ScrapeRequest)
        req = adapter.validate_python({"url": "https://example.com", "changeTracking": True})
        assert req.change_tracking is True

    def test_scrape_data_has_change_tracking_field(self):
        """ScrapeData.change_tracking exists, defaults to None."""
        from huginn.models import ScrapeData
        data = ScrapeData(markdown="x")
        assert hasattr(data, "change_tracking")
        assert data.change_tracking is None

    def test_scrape_data_change_tracking_accepts_dict(self):
        """ScrapeData.change_tracking accepts the diff result dict."""
        from huginn.models import ScrapeData
        change = {
            "previous_hash": "abc123",
            "current_hash": "def456",
            "diff": "+new line",
            "changed": True,
        }
        data = ScrapeData(markdown="x", change_tracking=change)
        assert data.change_tracking is not None
        assert data.change_tracking["changed"] is True


# ─── Scraper integration ─────────────────────────────────────────────────────

class TestScraperChangeTrackingIntegration:
    """Scraper.scrape() calls change_tracker when change_tracking=True."""

    @pytest.mark.asyncio
    async def test_change_tracking_false_does_not_populate_field(self):
        """When change_tracking=False (default), ScrapeData.change_tracking stays None."""
        from huginn.scraper import Scraper
        from huginn.change_tracker import ChangeTracker

        mock_browser = AsyncMock()
        mock_browser.ignore_https_errors = True
        mock_context = MagicMock()
        mock_context.set_extra_http_headers = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_browser.new_page = AsyncMock(return_value=MagicMock())
        mock_browser.navigate = AsyncMock(return_value=True)
        mock_browser.last_status_code = 200
        mock_browser.extract_content = AsyncMock(return_value={
            "title": "Test", "description": "test", "language": "en",
        })
        mock_browser.to_markdown = AsyncMock(return_value="# Test\n\nContent")

        scraper = Scraper(mock_browser)
        scraper._get_http_client = MagicMock()

        result = await scraper.scrape(
            url="https://example.com",
            change_tracking=False,
            render_mode="full",
        )

        # No change tracking was done
        assert result.change_tracking is None

    @pytest.mark.asyncio
    async def test_change_tracking_true_populates_field(self):
        """When change_tracking=True, ScrapeData.change_tracking is populated."""
        from huginn.scraper import Scraper
        from huginn.change_tracker import ChangeTracker

        mock_browser = AsyncMock()
        mock_browser.ignore_https_errors = True
        mock_context = MagicMock()
        mock_context.set_extra_http_headers = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_browser.new_page = AsyncMock(return_value=MagicMock())
        mock_browser.navigate = AsyncMock(return_value=True)
        mock_browser.last_status_code = 200
        mock_browser.extract_content = AsyncMock(return_value={
            "title": "Test", "description": "test", "language": "en",
        })
        mock_browser.to_markdown = AsyncMock(return_value="# Test\n\nContent v1")

        scraper = Scraper(mock_browser)
        scraper._get_http_client = MagicMock()
        scraper._change_tracker = ChangeTracker()  # inject a fresh tracker

        result = await scraper.scrape(
            url="https://example.com",
            change_tracking=True,
            render_mode="full",
        )

        # change_tracking is populated
        assert result.change_tracking is not None
        assert "previous_hash" in result.change_tracking
        assert "current_hash" in result.change_tracking
        assert "changed" in result.change_tracking
        assert "diff" in result.change_tracking
