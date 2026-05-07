"""
Huginn Response Cache

A lightweight in-memory TTL cache for scrape/crawl responses.
Avoids re-scraping the same URL within a configurable time window.

Key design:
- Cache key is a hash of (url, formats, params) so different
  scrape options for the same URL are cached separately.
- Entries expire after TTL seconds.
- Thread-safe for asyncio usage.
- Optional size limit to prevent unbounded memory growth.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Optional

from .models import ScrapeData

logger = logging.getLogger(__name__)


class CacheEntry:
    __slots__ = ("value", "expires_at")

    def __init__(self, value: Any, ttl: float):
        self.value = value
        self.expires_at = time.monotonic() + ttl


class AsyncTTLCache:
    """
    Async-safe TTL cache with configurable size limit.

    Automatically evicts expired entries on access and enforces
    a max_size limit using simple FIFO eviction when full.
    """

    def __init__(self, max_size: int = 10_000, default_ttl: float = 300.0):
        """
        Args:
            max_size: Maximum number of entries. When exceeded, oldest entries
                      are evicted before new ones are added.
            default_ttl: Default time-to-live in seconds for each entry.
        """
        self._store: dict[str, CacheEntry] = {}
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._lock = asyncio.Lock()

    # ── public API ─────────────────────────────────────────────────────────────

    async def get(self, key: str) -> Optional[Any]:
        """Return cached value if present and not expired, else None."""
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if time.monotonic() > entry.expires_at:
                del self._store[key]
                return None
            return entry.value

    async def set(self, key: str, value: Any, ttl: Optional[float] = None):
        """Store value under key with optional custom TTL."""
        async with self._lock:
            # Evict oldest entries if at capacity
            while len(self._store) >= self._max_size:
                oldest_key = next(iter(self._store), None)
                if oldest_key is not None:
                    del self._store[oldest_key]

            ttl_seconds = ttl if ttl is not None else self._default_ttl
            self._store[key] = CacheEntry(value, ttl_seconds)

    async def delete(self, key: str):
        """Remove a specific entry."""
        async with self._lock:
            self._store.pop(key, None)

    async def clear(self):
        """Remove all entries."""
        async with self._lock:
            self._store.clear()

    async def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        async with self._lock:
            now = time.monotonic()
            expired = sum(1 for e in self._store.values() if now > e.expires_at)
            return {
                "entries": len(self._store),
                "max_size": self._max_size,
                "expired": expired,
                "ttl_seconds": self._default_ttl,
            }

    def get_sync(self, key: str) -> Optional[Any]:
        """Synchronous get — for use in non-async contexts."""
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.monotonic() > entry.expires_at:
            return None
        return entry.value


# ── Global cache instance ───────────────────────────────────────────────────────

_response_cache: Optional[AsyncTTLCache] = None
_cache_lock = asyncio.Lock()


async def get_response_cache() -> AsyncTTLCache:
    global _response_cache
    if _response_cache is None:
        async with _cache_lock:
            if _response_cache is None:
                _response_cache = AsyncTTLCache(max_size=10_000, default_ttl=300.0)
    return _response_cache


def make_cache_key(url: str, formats: tuple, extra: Optional[dict] = None) -> str:
    """
    Build a deterministic cache key for a scrape request.

    The key is a SHA256 of the canonical request parameters, so the same
    URL with identical options hits the cache, but different options miss.
    """
    canonical = {
        "url": url.lower().strip(),
        "formats": sorted(getattr(f, "name", f) for f in formats),  # handle both Enum and str
    }
    if extra:
        filtered = {k: v for k, v in extra.items() if v is not None}
        if filtered:
            canonical["extra"] = {k: v for k, v in sorted(filtered.items())}
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


async def cache_scrape_result(
    url: str,
    formats: list,
    result: ScrapeData,
    ttl: float = 300.0,
) -> None:
    """Cache a successful ScrapeData result."""
    cache = await get_response_cache()
    key = make_cache_key(url, tuple(formats))
    await cache.set(key, result.model_dump_json(), ttl=ttl)


async def get_cached_scrape_result(
    url: str,
    formats: list,
) -> Optional[ScrapeData]:
    """Retrieve a cached ScrapeData result, or None if not present/expired."""
    cache = await get_response_cache()
    key = make_cache_key(url, tuple(formats))
    data = await cache.get(key)
    if data is None:
        return None
    try:
        return ScrapeData.model_validate_json(data)
    except Exception:
        return None


async def invalidate_cache(url: Optional[str] = None):
    """Invalidate cache for url (or all if url is None)."""
    if url is None:
        cache = await get_response_cache()
        await cache.clear()
    else:
        # We can't know all format combinations, so we clear the whole cache
        # when invalidation is requested. In production, use a per-key approach.
        cache = await get_response_cache()
        await cache.clear()
        logger.debug(f"Cache invalidated for URL: {url}")
