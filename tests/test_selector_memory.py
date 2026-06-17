"""
Tests for SelectorMemory — remembers successful CSS selectors per URL.

When a user scrapes a URL with `include_tags=['.athing']` and it
works, SelectorMemory remembers that pattern. Next time they scrape
the same URL, the memory suggests selectors that worked before.

The scraper can either auto-apply suggestions (opt-in via ScrapeRequest
flag) or just report them in the response for the caller to choose.

Memory is per-URL, in-memory, bounded. Stays out of the way of
cache invalidation — selectors are heuristics, not authoritative.
"""

import pytest
from collections import Counter

from huginn.selector_memory import SelectorMemory


# ─── Recording ───────────────────────────────────────────────────────────────

class TestRecordSuccess:
    """record_success(url, selector) — store a successful selector."""

    def test_record_success_basic(self):
        """First success for a URL+selector is recorded."""
        mem = SelectorMemory()
        mem.record_success("https://example.com", ".article")
        stats = mem.get_stats("https://example.com")
        assert stats is not None
        assert ".article" in stats

    def test_record_success_increments_count(self):
        """Recording the same selector multiple times increments its count."""
        mem = SelectorMemory()
        for _ in range(3):
            mem.record_success("https://example.com", ".article")
        stats = mem.get_stats("https://example.com")
        assert stats[".article"] == 3

    def test_record_success_records_last_used(self):
        """Each record updates the last_used timestamp for the selector."""
        mem = SelectorMemory()
        mem.record_success("https://example.com", ".article")
        last_used_1 = mem.get_last_used("https://example.com", ".article")
        assert last_used_1 is not None
        # Second record → newer last_used
        mem.record_success("https://example.com", ".article")
        last_used_2 = mem.get_last_used("https://example.com", ".article")
        assert last_used_2 > last_used_1

    def test_record_success_ignores_empty_selector(self):
        """Empty or whitespace-only selectors are not stored."""
        mem = SelectorMemory()
        mem.record_success("https://example.com", "")
        mem.record_success("https://example.com", "   ")
        assert mem.get_stats("https://example.com") is None


# ─── Suggestions ─────────────────────────────────────────────────────────────

class TestGetSuggestions:
    """get_suggestions(url) — return the best selectors for a URL."""

    def test_get_suggestions_sorted_by_score(self):
        """Suggestions are sorted by score (success_count × recency)."""
        mem = SelectorMemory()
        # .good is the best (3 successes, recent)
        for _ in range(3):
            mem.record_success("https://example.com", ".good")
        # .bad has 1 success
        mem.record_success("https://example.com", ".bad")
        # .okay has 2 successes
        mem.record_success("https://example.com", ".okay")
        mem.record_success("https://example.com", ".okay")

        suggestions = mem.get_suggestions("https://example.com")
        # Sorted by score descending
        assert len(suggestions) == 3
        assert suggestions[0][0] == ".good"  # highest count
        assert suggestions[1][0] == ".okay"  # middle
        assert suggestions[2][0] == ".bad"   # lowest

    def test_get_suggestions_respects_limit(self):
        """get_suggestions(url, limit=N) returns at most N suggestions."""
        mem = SelectorMemory()
        for i in range(5):
            mem.record_success("https://example.com", f".s{i}")
        suggestions = mem.get_suggestions("https://example.com", limit=3)
        assert len(suggestions) == 3

    def test_get_suggestions_empty_for_unknown_url(self):
        """No suggestions for a URL that has no recorded selectors."""
        mem = SelectorMemory()
        assert mem.get_suggestions("https://never-seen.com") == []

    def test_get_suggestions_returns_list_of_tuples(self):
        """Each suggestion is (selector, score) tuple."""
        mem = SelectorMemory()
        mem.record_success("https://example.com", ".article")
        suggestions = mem.get_suggestions("https://example.com")
        assert len(suggestions) == 1
        selector, score = suggestions[0]
        assert selector == ".article"
        assert isinstance(score, (int, float))
        assert score > 0


# ─── Forgetting ──────────────────────────────────────────────────────────────

class TestForget:
    """forget(url) / forget(url, selector) — clean up memory."""

    def test_forget_specific_selector(self):
        """forget(url, selector) removes only that selector, not others."""
        mem = SelectorMemory()
        mem.record_success("https://example.com", ".good")
        mem.record_success("https://example.com", ".bad")
        mem.forget("https://example.com", ".bad")
        stats = mem.get_stats("https://example.com")
        assert ".good" in stats
        assert ".bad" not in stats

    def test_forget_entire_url(self):
        """forget(url) without selector removes all of the URL's memory."""
        mem = SelectorMemory()
        mem.record_success("https://example.com", ".a")
        mem.record_success("https://example.com", ".b")
        mem.forget("https://example.com")
        assert mem.get_stats("https://example.com") is None

    def test_forget_nonexistent_url_is_noop(self):
        """forget on a URL that has no memory is a safe no-op."""
        mem = SelectorMemory()
        mem.forget("https://never-seen.com")  # should not raise


# ─── Memory bounds ───────────────────────────────────────────────────────────

class TestMemoryBounds:
    """Memory doesn't grow unbounded."""

    def test_max_selectors_per_url_enforced(self):
        """Only the top-N selectors per URL are kept (oldest pruned)."""
        mem = SelectorMemory(max_selectors_per_url=3)
        for i in range(10):
            mem.record_success("https://example.com", f".s{i:02d}")
        stats = mem.get_stats("https://example.com")
        assert stats is not None
        assert len(stats) <= 3  # capped at 3

    def test_max_urls_enforced(self):
        """Only the top-N URLs are kept (LRU eviction)."""
        mem = SelectorMemory(max_urls=3)
        for i in range(10):
            mem.record_success(f"https://example{i}.com", f".s{i}")
        all_stats = mem.get_all_stats()
        assert len(all_stats) <= 3  # capped at 3


# ─── Per-URL isolation ───────────────────────────────────────────────────────

class TestURLIsolation:
    """Different URLs have independent memory."""

    def test_different_urls_have_independent_memory(self):
        """a.com's selectors don't leak into b.com's suggestions."""
        mem = SelectorMemory()
        mem.record_success("https://a.com", ".a-only")
        mem.record_success("https://b.com", ".b-only")
        a_suggestions = mem.get_suggestions("https://a.com")
        b_suggestions = mem.get_suggestions("https://b.com")
        assert a_suggestions[0][0] == ".a-only"
        assert b_suggestions[0][0] == ".b-only"
        # And neither URL sees the other's selectors
        assert ".b-only" not in dict(a_suggestions).keys()
        assert ".a-only" not in dict(b_suggestions).keys()


# ─── get_all_stats ──────────────────────────────────────────────────────────

class TestGetAllStats:
    """get_all_stats() — summary of all remembered URLs."""

    def test_get_all_stats_returns_dict_of_counters(self):
        """get_all_stats returns {url: Counter({selector: count})}."""
        mem = SelectorMemory()
        mem.record_success("https://a.com", ".x")
        mem.record_success("https://a.com", ".x")
        mem.record_success("https://b.com", ".y")
        all_stats = mem.get_all_stats()
        assert "https://a.com" in all_stats
        assert "https://b.com" in all_stats
        assert all_stats["https://a.com"][".x"] == 2
        assert all_stats["https://b.com"][".y"] == 1

    def test_get_all_stats_empty_initially(self):
        """Fresh memory has empty stats."""
        mem = SelectorMemory()
        assert mem.get_all_stats() == {}
