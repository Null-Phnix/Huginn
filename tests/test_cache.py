"""
Tests for the response cache module.
"""

import asyncio

from huginn.cache import (
    AsyncTTLCache,
    make_cache_key,
)
from huginn.models import ScrapeRequest
from huginn.routers.scrape import _cache_context
from huginn.utils import EGRESS_CACHE_CONTRACT


class TestAsyncTTLCache:
    async def test_set_and_get(self):
        cache = AsyncTTLCache(default_ttl=60.0)
        await cache.set("key1", "value1")
        result = await cache.get("key1")
        assert result == "value1"

    async def test_expiry(self):
        cache = AsyncTTLCache(default_ttl=0.05)
        await cache.set("key1", "value1")
        assert await cache.get("key1") == "value1"
        await asyncio.sleep(0.1)
        assert await cache.get("key1") is None

    async def test_delete(self):
        cache = AsyncTTLCache(default_ttl=60.0)
        await cache.set("key1", "value1")
        await cache.delete("key1")
        assert await cache.get("key1") is None

    async def test_clear(self):
        cache = AsyncTTLCache(default_ttl=60.0)
        await cache.set("k1", "v1")
        await cache.set("k2", "v2")
        await cache.clear()
        assert await cache.get("k1") is None
        assert await cache.get("k2") is None

    async def test_max_size_eviction(self):
        cache = AsyncTTLCache(max_size=3, default_ttl=60.0)
        for i in range(3):
            await cache.set(f"key{i}", f"value{i}")
        assert await cache.get("key0") == "value0"

        # Adding 4th should evict oldest
        await cache.set("key3", "value3")
        # key0 may or may not be evicted depending on dict iteration order,
        # but the cache size should be bounded
        stats = await cache.stats()
        assert stats["entries"] <= 3

    async def test_stats(self):
        cache = AsyncTTLCache(max_size=100, default_ttl=30.0)
        await cache.set("k1", "v1")
        await cache.set("k2", "v2")
        stats = await cache.stats()
        assert stats["entries"] == 2
        assert stats["max_size"] == 100
        assert stats["ttl_seconds"] == 30.0
        assert stats["expired"] == 0

    async def test_sync_get(self):
        cache = AsyncTTLCache(default_ttl=60.0)
        await cache.set("k1", "v1")
        assert cache.get_sync("k1") == "v1"
        assert cache.get_sync("nonexistent") is None

    async def test_concurrent_access(self):
        cache = AsyncTTLCache(default_ttl=60.0)
        await asyncio.gather(*[cache.set(f"k{i}", f"v{i}") for i in range(100)])
        results = await asyncio.gather(*[cache.get(f"k{i}") for i in range(100)])
        assert all(r == f"v{i}" for i, r in enumerate(results))


class TestMakeCacheKey:
    def test_same_url_same_key(self):
        k1 = make_cache_key("https://example.com", ("markdown",))
        k2 = make_cache_key("https://example.com", ("markdown",))
        assert k1 == k2

    def test_url_normalized(self):
        # Case is normalized but trailing slash is preserved
        k1 = make_cache_key("https://Example.com/page/", ())
        k2 = make_cache_key("https://example.com/page/", ())
        assert k1 == k2

    def test_different_urls_different_keys(self):
        k1 = make_cache_key("https://example.com/page1", ())
        k2 = make_cache_key("https://example.com/page2", ())
        assert k1 != k2

    def test_different_formats_different_key(self):
        k1 = make_cache_key("https://example.com", ("markdown",))
        k2 = make_cache_key("https://example.com", ("html",))
        assert k1 != k2

    def test_with_extra_params(self):
        k1 = make_cache_key("https://example.com", ("markdown",), {"timeout": 30000})
        k2 = make_cache_key("https://example.com", ("markdown",), {"timeout": 30000})
        assert k1 == k2

    def test_extra_order_doesnt_matter(self):
        k1 = make_cache_key("https://example.com", (), {"a": 1, "b": 2})
        k2 = make_cache_key("https://example.com", (), {"b": 2, "a": 1})
        assert k1 == k2

    def test_none_extras_ignored(self):
        k1 = make_cache_key("https://example.com", (), {"timeout": None})
        k2 = make_cache_key("https://example.com", (), {})
        assert k1 == k2

    def test_socket_gateway_contract_invalidates_legacy_scrape_entries(self):
        request = ScrapeRequest(url="https://example.com", render_mode="starsearch")
        current = _cache_context(request, {"mode": "direct"})
        legacy = dict(current)
        legacy.pop("_egress_contract")

        assert current["_egress_contract"] == EGRESS_CACHE_CONTRACT
        assert make_cache_key(request.url, ("markdown",), current) != make_cache_key(
            request.url,
            ("markdown",),
            legacy,
        )
