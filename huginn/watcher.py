"""
Huginn Page Watch / Change Detection

Monitors pages for content changes over time.
Firecrawl has NOTHING like this.

How it works:
1. POST /v1/watch {url, selectors?} → stores initial content hash, returns snapshot
2. Subsequent calls compare content hash against stored hash
3. If changed → fires webhook with diff summary
4. GET /v1/watch/{url} → current snapshot + history
5. DELETE /v1/watch/{url} → stop watching

Content hash = SHA256(normalized_text_content)
Diff = sentences added/removed since last check
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from .browser import BrowserManager
from .circuit_breaker import get_circuit_breaker, extract_domain
from .scraper import Scraper


# ─── Data Models ───────────────────────────────────────────────────────────────


@dataclass
class PageSnapshot:
    """A point-in-time snapshot of a page."""
    url: str
    content_hash: str  # SHA256 of normalized text
    text_content: str  # Normalized text (used for diffs)
    html_content: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    detected_changes: Optional[List[str]] = None  # Sentences added/removed
    selectors: Optional[List[str]] = None  # CSS selectors monitored
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    size_bytes: int = 0

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "content_hash": self.content_hash,
            "text_content": self.text_content[:500],  # Truncate for storage
            "metadata": self.metadata,
            "detected_changes": self.detected_changes,
            "selectors": self.selectors,
            "created_at": self.created_at.isoformat(),
            "size_bytes": self.size_bytes,
        }


@dataclass
class WatchEntry:
    """A watched URL with its history of snapshots."""
    url: str
    domain: str
    snapshots: List[PageSnapshot] = field(default_factory=list)
    webhook_url: Optional[str] = None
    selectors: List[str] = field(default_factory=list)
    last_check: Optional[datetime] = None
    last_change: Optional[datetime] = None
    change_count: int = 0
    enabled: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def latest_snapshot(self) -> Optional[PageSnapshot]:
        return self.snapshots[-1] if self.snapshots else None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "domain": self.domain,
            "snapshot_count": len(self.snapshots),
            "webhook_url": self.webhook_url,
            "selectors": self.selectors,
            "last_check": self.last_check.isoformat() if self.last_check else None,
            "last_change": self.last_change.isoformat() if self.last_change else None,
            "change_count": self.change_count,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat(),
            "latest_hash": self.latest_snapshot().content_hash if self.latest_snapshot() else None,
        }


# ─── Content Hashing ───────────────────────────────────────────────────────────


def compute_content_hash(text: str) -> str:
    """Compute SHA256 hash of normalized text content."""
    normalized = normalize_for_diff(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalize_for_diff(text: str) -> str:
    """
    Normalize text for comparison:
    - Lowercase
    - Collapse whitespace
    - Remove URLs (they change often without meaning)
    - Remove timestamps
    - Sort bullet points for consistent hashing
    """
    if not text:
        return ""

    text = text.lower()
    # Remove common noise
    text = re.sub(r'https?://\S+', '[URL]', text)  # URLs
    text = re.sub(r'\d{1,2}[/:]\d{2}[/:]\d{2,4}', '[DATE]', text)  # Dates
    text = re.sub(r'\d{1,2}:\d{2}(?::\d{2})?(?:am|pm)?', '[TIME]', text)  # Times
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text)
    # Remove extra punctuation noise
    text = re.sub(r'[\u200b\u200c\u200d]', '', text)  # Zero-width chars
    return text.strip()


def compute_diff(old_text: str, new_text: str) -> List[str]:
    """
    Compute sentences that are new or removed between two texts.
    Returns a list of human-readable change descriptions.
    """
    old_sentences = set(normalize_sentence(s) for s in split_sentences(old_text))
    new_sentences = set(normalize_sentence(s) for s in split_sentences(new_text))

    added = new_sentences - old_sentences
    removed = old_sentences - new_sentences

    changes = []
    for s in sorted(added):
        if len(s) > 20:  # Skip very short fragments
            changes.append(f"+ Added: {s[:200]}")
    for s in sorted(removed):
        if len(s) > 20:
            changes.append(f"- Removed: {s[:200]}")

    return changes[:50]  # Cap at 50 changes


def split_sentences(text: str) -> List[str]:
    """Split text into sentences."""
    if not text:
        return []
    sentences = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in sentences if len(s.strip()) > 10]


def normalize_sentence(s: str) -> str:
    """Normalize a sentence for comparison."""
    s = s.lower().strip()
    s = re.sub(r'\s+', ' ', s)
    s = re.sub(r'[^\w\s]', '', s)
    return s[:200]


# ─── Watch Store ───────────────────────────────────────────────────────────────


class WatchStore:
    """
    In-memory store of watched pages and their snapshots.
    Production deployment would use Redis or the job store DB.
    """

    def __init__(self, max_snapshots_per_url: int = 10):
        self._watched: Dict[str, WatchEntry] = {}
        self._lock = asyncio.Lock()
        self._max_snapshots = max_snapshots_per_url

    async def watch(
        self,
        url: str,
        selectors: Optional[List[str]] = None,
        webhook_url: Optional[str] = None,
    ) -> WatchEntry:
        """Register a URL for watching."""
        async with self._lock:
            domain = extract_domain(url)
            if url in self._watched:
                entry = self._watched[url]
                entry.webhook_url = webhook_url or entry.webhook_url
                entry.selectors = selectors or entry.selectors
                entry.enabled = True
                return entry

            entry = WatchEntry(
                url=url,
                domain=domain,
                selectors=selectors or [],
                webhook_url=webhook_url,
            )
            self._watched[url] = entry
            return entry

    async def get(self, url: str) -> Optional[WatchEntry]:
        """Get a watched entry."""
        async with self._lock:
            return self._watched.get(url)

    async def add_snapshot(self, url: str, snapshot: PageSnapshot) -> Optional[WatchEntry]:
        """Add a snapshot to a watched URL."""
        async with self._lock:
            entry = self._watched.get(url)
            if not entry:
                return None

            entry.snapshots.append(snapshot)
            # Keep only last N snapshots
            if len(entry.snapshots) > self._max_snapshots:
                entry.snapshots = entry.snapshots[-self._max_snapshots:]

            entry.last_check = snapshot.created_at
            return entry

    async def update_change(self, url: str, snapshot: PageSnapshot, changes: List[str]) -> Optional[WatchEntry]:
        """Record that a change was detected for a watched URL."""
        async with self._lock:
            entry = self._watched.get(url)
            if not entry:
                return None

            snapshot.detected_changes = changes
            entry.snapshots.append(snapshot)
            if len(entry.snapshots) > self._max_snapshots:
                entry.snapshots = entry.snapshots[-self._max_snapshots:]

            entry.last_check = snapshot.created_at
            entry.last_change = snapshot.created_at
            entry.change_count += 1
            return entry

    async def unwatch(self, url: str) -> bool:
        """Stop watching a URL."""
        async with self._lock:
            if url in self._watched:
                del self._watched[url]
                return True
            return False

    async def list_watched(self) -> List[WatchEntry]:
        """List all watched URLs."""
        async with self._lock:
            return list(self._watched.values())

    async def get_history(self, url: str) -> List[PageSnapshot]:
        """Get full snapshot history for a URL."""
        async with self._lock:
            entry = self._watched.get(url)
            return entry.snapshots if entry else []


# ─── Global singleton ─────────────────────────────────────────────────────────

_watch_store: Optional[WatchStore] = None


def get_watch_store() -> WatchStore:
    global _watch_store
    if _watch_store is None:
        _watch_store = WatchStore()
    return _watch_store


# ─── Watch Checker ─────────────────────────────────────────────────────────────


class PageWatcher:
    """
    Checks pages for changes and fires webhooks.
    Use check_and_notify() for one-off checks,
    or start_background_monitoring() for continuous watching.
    """

    def __init__(self, browser: BrowserManager, watch_store: Optional[WatchStore] = None):
        self.browser = browser
        self.scraper = Scraper(browser)
        self.store = watch_store or get_watch_store()
        self._cb = get_circuit_breaker()
        self._monitor_tasks: Dict[str, asyncio.Task] = {}

    async def check(self, url: str) -> PageSnapshot:
        """Take a snapshot of a URL."""
        domain = extract_domain(url)
        if self._cb.is_open(domain):
            raise RuntimeError(f"Circuit breaker open for {domain}")

        scraped = await self.scraper.scrape(
            url=url,
            formats=[],  # Just need text
            only_main_content=True,
            timeout=20000,
        )

        text = scraped.markdown or scraped.raw_html or ""
        content_hash = compute_content_hash(text)

        snapshot = PageSnapshot(
            url=url,
            content_hash=content_hash,
            text_content=text,
            html_content=scraped.raw_html,
            metadata=scraped.metadata,
            size_bytes=len(text.encode("utf-8")),
        )

        await self._cb.record_success(domain)
        return snapshot

    async def check_and_notify(self, url: str, force: bool = False) -> PageSnapshot:
        """
        Check a URL for changes and fire webhook if content changed.

        Returns the new snapshot with detected_changes populated if changed.
        """
        entry = await self.store.get(url)
        if not entry:
            raise ValueError(f"URL {url} is not being watched. POST /v1/watch first.")

        # Take new snapshot
        snapshot = await self.check(url)
        latest = entry.latest_snapshot()

        if latest and snapshot.content_hash != latest.content_hash:
            # Content changed — compute diff
            changes = compute_diff(latest.text_content, snapshot.text_content)
            snapshot.detected_changes = changes

            # Update store
            await self.store.update_change(url, snapshot, changes)

            # Fire webhook
            if entry.webhook_url:
                await self._fire_change_webhook(
                    entry.webhook_url,
                    url,
                    changes,
                    snapshot,
                    entry.change_count,
                )
        else:
            # No change
            await self.store.add_snapshot(url, snapshot)

        return snapshot

    async def _fire_change_webhook(
        self,
        webhook_url: str,
        url: str,
        changes: List[str],
        snapshot: PageSnapshot,
        change_count: int,
    ):
        """Fire a webhook notification for page changes."""
        from .webhook import send_webhook_with_retry

        payload = {
            "event": "page_changed",
            "url": url,
            "domain": extract_domain(url),
            "change_count": change_count,
            "change_count_this_watch": len(changes),
            "changes": changes[:20],  # Send top 20 changes
            "content_hash": snapshot.content_hash,
            "detected_at": datetime.now(timezone.utc).isoformat(),
            "metadata": snapshot.metadata,
        }

        try:
            await send_webhook_with_retry(webhook_url, payload, max_retries=3)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Change webhook failed: {e}")

    async def start_monitoring(
        self,
        url: str,
        interval_seconds: int = 3600,
    ) -> asyncio.Task:
        """Start background monitoring of a URL."""
        if url in self._monitor_tasks:
            self._monitor_tasks[url].cancel()

        async def _monitor_loop():
            while True:
                try:
                    await asyncio.sleep(interval_seconds)
                    await self.check_and_notify(url)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning(f"Monitor check failed for {url}: {e}")

        task = asyncio.create_task(_monitor_loop())
        self._monitor_tasks[url] = task
        return task

    async def stop_monitoring(self, url: str):
        """Stop background monitoring of a URL."""
        if url in self._monitor_tasks:
            self._monitor_tasks[url].cancel()
            del self._monitor_tasks[url]
