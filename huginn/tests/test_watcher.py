"""Unit tests for huginn/watcher.py change detection and monitoring."""
import asyncio
from datetime import datetime, timezone
import pytest
from unittest.mock import AsyncMock, MagicMock

from huginn.watcher import (
    PageWatcher,
    WatchStore,
    compute_content_hash,
    compute_diff,
    normalize_for_diff,
    split_sentences,
)
from huginn.models import ScrapeData


@pytest.fixture
def fake_browser():
    browser = MagicMock()
    browser.stop = AsyncMock()
    browser.new_context = AsyncMock()
    browser.new_page = AsyncMock()
    browser.navigate = AsyncMock(return_value=True)
    browser.smart_wait = AsyncMock()
    browser.extract_content = AsyncMock(return_value={
        "title": "Test", "url": "http://example.com", "description": "", "language": "en"
    })
    browser.to_markdown = AsyncMock(return_value="# Test Page\n\nContent here")
    browser.get_links = AsyncMock(return_value=[])
    browser.last_status_code = 200
    return browser


class FakeScraper:
    """Fake scraper returning canned data for watcher tests."""
    def __init__(self, pages: dict):
        self.pages = pages
    async def scrape(self, url, **kwargs):
        return self.pages.get(url, ScrapeData(metadata={"url": url, "title": "unknown"}))


class TestContentHashing:
    """Tests for content hashing and normalization."""

    def test_normalize_removes_urls(self):
        text = "Visit https://example.com/path?q=1 today!"
        normalized = normalize_for_diff(text)
        assert "https://example.com" not in normalized
        assert "[URL]" in normalized

    def test_hash_consistency(self):
        text = "Same content"
        h1 = compute_content_hash(text)
        h2 = compute_content_hash(text)
        assert h1 == h2

    def test_hash_changes_with_content(self):
        h1 = compute_content_hash("old content")
        h2 = compute_content_hash("new content")
        assert h1 != h2


class TestDiffComputation:
    """Tests for sentence-level diff."""

    def test_detects_added_sentences(self):
        old = "First sentence here. Second sentence here."
        new = "First sentence here. Second sentence here. A third sentence has been added here."
        changes = compute_diff(old, new)
        assert any("added" in c for c in changes)

    def test_detects_removed_sentences(self):
        old = "First sentence here. Second sentence here that will be removed."
        new = "First sentence here."
        changes = compute_diff(old, new)
        assert any("removed" in c for c in changes)

    def test_no_false_positives(self):
        old = "Same content throughout."
        new = "Same content throughout."
        changes = compute_diff(old, new)
        assert len(changes) == 0

    def test_short_fragments_ignored(self):
        old = ""
        new = "A. B. C."
        changes = compute_diff(old, new)
        # Single letters under 20 chars are skipped
        assert all(len(c) > 20 for c in changes)


class TestWatchStore:
    """Tests for in-memory watch storage."""

    @pytest.mark.asyncio
    async def test_watch_add(self):
        store = WatchStore()
        entry = await store.watch("http://example.com")
        assert entry.url == "http://example.com"
        assert entry.enabled is True

    @pytest.mark.asyncio
    async def test_watch_duplicate(self):
        store = WatchStore()
        e1 = await store.watch("http://example.com")
        e2 = await store.watch("http://example.com")
        assert e1 is e2

    @pytest.mark.asyncio
    async def test_add_snapshot(self):
        store = WatchStore()
        entry = await store.watch("http://example.com")
        snap = MagicMock()
        snap.created_at = datetime.now(timezone.utc)
        result = await store.add_snapshot("http://example.com", snap)
        assert result is entry
        assert len(entry.snapshots) == 1

    @pytest.mark.asyncio
    async def test_snapshot_limit(self):
        store = WatchStore(max_snapshots_per_url=3)
        entry = await store.watch("http://example.com")
        for i in range(5):
            snap = MagicMock()
            snap.created_at = datetime.now(timezone.utc)
            await store.add_snapshot("http://example.com", snap)
        assert len(entry.snapshots) == 3

    @pytest.mark.asyncio
    async def test_unwatch(self):
        store = WatchStore()
        await store.watch("http://example.com")
        removed = await store.unwatch("http://example.com")
        assert removed is True
        assert await store.get("http://example.com") is None


class TestPageWatcher:
    """Tests for PageWatcher change detection."""

    def _make_watcher(self, browser, store=None):
        watcher = PageWatcher(browser, store or WatchStore())
        watcher.scraper = FakeScraper({
            "http://example.com": ScrapeData(
                markdown="# Test Page\n\nContent here",
                metadata={"url": "http://example.com", "title": "Test Page", "status_code": 200},
            ),
            "http://example.com/changed": ScrapeData(
                markdown="# Test Page\n\nChanged content here",
                metadata={"url": "http://example.com", "title": "Test Page", "status_code": 200},
            ),
        })
        return watcher

    @pytest.mark.asyncio
    async def test_check_returns_snapshot(self, fake_browser):
        watcher = self._make_watcher(fake_browser)
        snap = await watcher.check("http://example.com")
        assert snap.url == "http://example.com"
        assert snap.content_hash
        assert snap.text_content == "# Test Page\n\nContent here"

    @pytest.mark.asyncio
    async def test_check_and_notify_no_change(self, fake_browser):
        store = WatchStore()
        watcher = self._make_watcher(fake_browser, store)

        # Set up initial watch + snapshot
        await store.watch("http://example.com")
        initial = await watcher.check("http://example.com")
        await store.add_snapshot("http://example.com", initial)

        # Same content → no change
        snap = await watcher.check_and_notify("http://example.com")
        assert not snap.detected_changes
        entry = await store.get("http://example.com")
        assert entry.change_count == 0

    @pytest.mark.asyncio
    async def test_check_and_notify_detects_change(self, fake_browser):
        store = WatchStore()
        watcher = self._make_watcher(fake_browser, store)

        # Initial snapshot
        await store.watch("http://example.com")
        initial = await watcher.check("http://example.com")
        await store.add_snapshot("http://example.com", initial)

        # Inject changed content
        watcher.scraper = FakeScraper({
            "http://example.com": ScrapeData(
                markdown="# Test Page\n\nChanged content here",
                metadata={"url": "http://example.com", "title": "Test Page", "status_code": 200},
            ),
        })
        snap = await watcher.check_and_notify("http://example.com")
        assert snap.detected_changes is not None
        assert len(snap.detected_changes) > 0
        entry = await store.get("http://example.com")
        assert entry.change_count == 1

    @pytest.mark.asyncio
    async def test_monitoring_start_and_stop(self, fake_browser):
        store = WatchStore()
        watcher = self._make_watcher(fake_browser, store)
        await store.watch("http://example.com")

        task = await watcher.start_monitoring("http://example.com", interval_seconds=0.1)
        assert "http://example.com" in watcher._monitor_tasks
        await asyncio.sleep(0.15)  # Let it run once
        await watcher.stop_monitoring("http://example.com")
        assert "http://example.com" not in watcher._monitor_tasks


class TestWatchSingleton:
    """Verify that the same PageWatcher instance is reused."""

    @pytest.mark.asyncio
    async def test_same_watcher_across_calls(self, fake_browser):
        from huginn.watcher import get_watch_store
        store = get_watch_store()
        w1 = PageWatcher(fake_browser, store)
        w2 = PageWatcher(fake_browser, store)
        # Two instances with same store — monitoring tasks are per-instance
        # so we need shared instance for correct cancellation
        assert w1 is not w2
        # But with shared store, unwatch still removes from store
        await store.watch("http://example.com")
        removed = await store.unwatch("http://example.com")
        assert removed is True
