"""
ChangeTracker — store last-seen DOM content per URL and compute diffs.

Used by the /v1/scrape endpoint when changeTracking=True. Stores a SHA-256
hash + the actual content per URL so future scrapes can compute a unified
diff between the previous and current content.

Threading: uses an asyncio.Lock for safe concurrent use. Storage is in-memory
(per-process) — for multi-process / multi-host deployments, swap _store for
a Redis or sqlite backend without changing the public API.
"""

import asyncio
import difflib
import hashlib
import time
from typing import Any, Dict, Optional


class ChangeTracker:
    """Stores last-seen content per URL and computes diffs.

    Public API:
      compute_hash(content) -> str          # stable hash of content
      compute_diff(old, new) -> str         # unified diff (empty if unchanged)
      await check_and_store(url, content) -> dict  # main entry point
    """

    def __init__(self) -> None:
        # url -> {"hash": str, "content": str, "timestamp": float}
        self._store: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def compute_hash(content: str) -> str:
        """SHA-256 hash of content, hex prefix (16 chars).

        16 hex chars = 64 bits = 16^16 = effectively collision-free for the
        number of URLs any single Huginn instance will see in its lifetime.
        """
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def compute_diff(old: str, new: str, n: int = 3) -> str:
        """Unified diff between old and new content (empty string if identical).

        n is the number of context lines (default 3, matching standard diff).
        """
        if old == new:
            return ""
        # unified_diff returns a generator that joins cleanly
        diff_lines = difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            n=n,
        )
        return "".join(diff_lines)

    async def check_and_store(self, url: str, content: str) -> Dict[str, Any]:
        """Compare `content` against the last-seen content for `url`, then store the new state.

        Returns a dict with:
          previous_hash: str | None  — hash of previous content (None on first scrape)
          current_hash:  str        — hash of this scrape
          diff:          str        — unified diff (empty if no change or no previous)
          changed:       bool       — True iff previous content existed AND hashes differ
        """
        current_hash = self.compute_hash(content)
        async with self._lock:
            entry = self._store.get(url)
            previous_hash: Optional[str] = entry["hash"] if entry else None
            previous_content: Optional[str] = entry["content"] if entry else None
            diff: str = self.compute_diff(previous_content or "", content) if previous_content is not None else ""

            # Store the new state (FIFO replace — we only keep the most recent)
            self._store[url] = {
                "hash": current_hash,
                "content": content,
                "timestamp": time.time(),
            }

        return {
            "previous_hash": previous_hash,
            "current_hash": current_hash,
            "diff": diff,
            "changed": previous_hash is not None and previous_hash != current_hash,
        }

    def clear(self) -> None:
        """Reset all stored state. Useful for tests."""
        self._store.clear()

    def stats(self) -> Dict[str, Any]:
        """Return a snapshot of tracker state. Useful for /health endpoints."""
        return {
            "tracked_urls": len(self._store),
        }


# ─── Module-level singleton ──────────────────────────────────────────────────
# One tracker per process. The Scraper accesses this via get_change_tracker().
# Tests can call clear() to reset between runs.

_singleton: Optional[ChangeTracker] = None
_singleton_lock = asyncio.Lock()


def get_change_tracker() -> ChangeTracker:
    """Return the process-wide ChangeTracker singleton.

    Async-safe: first call creates the singleton, subsequent calls return it.
    """
    global _singleton
    if _singleton is None:
        _singleton = ChangeTracker()
    return _singleton
