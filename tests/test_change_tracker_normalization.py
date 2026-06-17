"""
Tests for ChangeTracker ↔ watcher.compute_content_hash delegation.

The change tracker should use the same content-hashing algorithm as the
watcher module so cosmetic changes (URLs, dates, times) don't trigger
false "content changed" alerts. watcher.compute_content_hash() normalizes
text before hashing:
  - Lowercases
  - Replaces URLs with [URL]
  - Replaces dates/times with [DATE]/[TIME]
  - Collapses whitespace
  - Strips zero-width chars

ChangeTracker.compute_hash() should delegate to watcher.compute_content_hash
to ensure consistent behavior across both the change-tracking endpoint
(/v1/scrape?changeTracking=true) and the watch endpoint (/v1/watch).
"""

import pytest
from huginn.change_tracker import ChangeTracker
from huginn.watcher import compute_content_hash


class TestChangeTrackerDelegatesToWatcher:
    """ChangeTracker.compute_hash should use watcher's normalized hashing."""

    def test_compute_hash_matches_watcher_for_plain_text(self):
        """Plain text hash matches watcher's compute_content_hash (truncated to 16)."""
        text = "Hello world\nThis is some content."
        ct_hash = ChangeTracker.compute_hash(text)
        # watcher returns 64-char hex; ct_hash is 16-char prefix
        assert ct_hash == compute_content_hash(text)[:16]

    def test_compute_hash_ignores_url_changes(self):
        """The same content with different URLs hashes the same (URLs are normalized)."""
        # Without delegation, this would fail — raw SHA-256 would differ
        text_a = "Check out https://example.com/article-1 for more details."
        text_b = "Check out https://different-site.org/another-path for more details."
        assert ChangeTracker.compute_hash(text_a) == ChangeTracker.compute_hash(text_b)

    def test_compute_hash_ignores_timestamp_changes(self):
        """Two texts differing only in their date/time hash the same."""
        # Both contain dates/times; watcher normalizes both to [DATE] [TIME].
        text_a = "Updated 15/06/2026 at 14:30, this article covers topics."
        text_b = "Updated 16/06/2026 at 09:15, this article covers topics."
        # Both normalize to "updated [date] at [time], this article covers topics."
        assert ChangeTracker.compute_hash(text_a) == ChangeTracker.compute_hash(text_b)

    def test_compute_hash_ignores_case_changes(self):
        """Case-only changes do not affect the hash (text is lowercased)."""
        text_a = "Hello World"
        text_b = "hello world"
        assert ChangeTracker.compute_hash(text_a) == ChangeTracker.compute_hash(text_b)

    def test_compute_hash_ignores_extra_whitespace(self):
        """Different whitespace patterns hash the same (whitespace collapsed)."""
        text_a = "Line 1\nLine 2\nLine 3"
        text_b = "Line 1  Line 2   Line 3"
        # Both normalize to "line 1 line 2 line 3"
        assert ChangeTracker.compute_hash(text_a) == ChangeTracker.compute_hash(text_b)

    def test_compute_hash_real_content_change_differs(self):
        """A real content change DOES produce a different hash."""
        text_a = "This is the original content."
        text_b = "This is completely different content."
        assert ChangeTracker.compute_hash(text_a) != ChangeTracker.compute_hash(text_b)

    def test_compute_hash_returns_16_char_prefix(self):
        """compute_hash returns the 16-char prefix of the watcher hash (not full 64)."""
        text = "test content"
        h = ChangeTracker.compute_hash(text)
        assert len(h) == 16
        assert h == compute_content_hash(text)[:16]


class TestEndToEndWithWatcher:
    """End-to-end: ChangeTracker.check_and_store uses watcher's normalization."""

    @pytest.mark.asyncio
    async def test_url_change_does_not_trigger_changed_flag(self):
        """Storing content that differs only in URLs should NOT report changed=True."""
        ct = ChangeTracker()
        url = "https://tracked.example.com"
        # First scrape
        await ct.check_and_store(url, "Read the article at https://a.com/1")
        # Second scrape — only URL changed
        result = await ct.check_and_store(url, "Read the article at https://b.com/2")
        # Bug fix: should NOT report changed (URL is normalized out)
        assert result["changed"] is False, (
            f"URL-only change incorrectly reported as content change. "
            f"Diff: {result['diff']!r}"
        )

    @pytest.mark.asyncio
    async def test_real_content_change_still_detected(self):
        """Real content changes still produce changed=True and a non-empty diff."""
        ct = ChangeTracker()
        url = "https://tracked.example.com"
        await ct.check_and_store(url, "Original article body text.")
        result = await ct.check_and_store(url, "Completely new article body text.")
        assert result["changed"] is True
        assert result["diff"]
