"""
SelectorMemory — remembers successful CSS selectors per URL.

When a user scrapes a URL with `include_tags=['.article']` and gets good
results, SelectorMemory records that pattern. Next time they scrape
the same URL, get_suggestions() returns the selectors that worked,
sorted by score (success count × recency).

Memory is per-URL, in-memory, bounded. Stays out of the way of cache
invalidation — selectors are heuristics, not authoritative.

Public API:
  - record_success(url, selector) — store a successful selector use
  - get_suggestions(url, limit=5) — best selectors for a URL, sorted
  - get_stats(url) — Counter of {selector: success_count} for a URL
  - get_all_stats() — Counter for all remembered URLs
  - get_last_used(url, selector) — timestamp of last success
  - forget(url) / forget(url, selector) — cleanup
  - clear() — wipe all memory

Used by the Scraper when include_tags is empty: if selector memory
has suggestions for a URL, they're returned in the response metadata
as a hint (caller can opt-in to auto-apply via a future flag).
"""

import time
from collections import Counter
from typing import Dict, List, Optional, Tuple


class SelectorMemory:
    """Per-URL selector memory with bounded growth.

    Args:
        max_selectors_per_url: Per-URL cap. When exceeded, the
            lowest-scored selector is evicted.
        max_urls: Total URLs cap. When exceeded, the
            least-recently-used URL is evicted.
    """

    def __init__(
        self,
        max_selectors_per_url: int = 20,
        max_urls: int = 1000,
    ) -> None:
        self.max_selectors_per_url = max_selectors_per_url
        self.max_urls = max_urls
        # url -> {"selectors": {selector: count}, "last_used": {selector: ts}, "url_last_used": ts}
        self._store: Dict[str, Dict] = {}

    def record_success(self, url: str, selector: str) -> None:
        """Record that `selector` worked for `url`."""
        if not selector or not selector.strip():
            return
        selector = selector.strip()
        entry = self._store.setdefault(
            url,
            {"selectors": {}, "last_used": {}, "url_last_used": 0.0},
        )
        entry["selectors"][selector] = entry["selectors"].get(selector, 0) + 1
        entry["last_used"][selector] = time.monotonic()
        entry["url_last_used"] = time.monotonic()

        # Enforce per-URL cap (evict the selector with the lowest count)
        if len(entry["selectors"]) > self.max_selectors_per_url:
            sorted_selectors = sorted(
                entry["selectors"].items(),
                key=lambda kv: (kv[1], entry["last_used"].get(kv[0], 0)),
            )
            # Drop the lowest-scored ones (oldest weak signals)
            to_drop = len(entry["selectors"]) - self.max_selectors_per_url
            for sel, _ in sorted_selectors[:to_drop]:
                del entry["selectors"][sel]
                entry["last_used"].pop(sel, None)

        # Enforce URL cap (evict the least-recently-used URL)
        if len(self._store) > self.max_urls:
            sorted_urls = sorted(
                self._store.items(),
                key=lambda kv: kv[1]["url_last_used"],
            )
            to_drop = len(self._store) - self.max_urls
            for old_url, _ in sorted_urls[:to_drop]:
                del self._store[old_url]

    def get_suggestions(
        self,
        url: str,
        limit: int = 5,
    ) -> List[Tuple[str, float]]:
        """Return the top-`limit` selectors for `url`, sorted by score.

        Score = success_count * recency_factor. Recency_factor is a
        linear decay: 1.0 for selectors used in the last hour, 0.5
        for the last day, 0.1 for older.
        """
        entry = self._store.get(url)
        if entry is None:
            return []
        now = time.monotonic()
        scored: List[Tuple[str, float]] = []
        for sel, count in entry["selectors"].items():
            last_used = entry["last_used"].get(sel, now)
            age_seconds = now - last_used
            # Recency factor: 1.0 if used in the last hour, fading to 0.1 for > 1 day
            if age_seconds < 3600:
                recency = 1.0
            elif age_seconds < 86400:
                recency = 0.5
            else:
                recency = 0.1
            score = count * recency
            scored.append((sel, score))
        # Sort by score desc, then by selector name for stable order
        scored.sort(key=lambda kv: (-kv[1], kv[0]))
        return scored[:limit]

    def get_stats(self, url: str) -> Optional[Counter]:
        """Return a Counter of {selector: success_count} for `url`, or None if no memory."""
        entry = self._store.get(url)
        if entry is None:
            return None
        return Counter(entry["selectors"])

    def get_all_stats(self) -> Dict[str, Counter]:
        """Return a {url: Counter} for all remembered URLs."""
        return {url: Counter(entry["selectors"]) for url, entry in self._store.items()}

    def get_last_used(self, url: str, selector: str) -> Optional[float]:
        """Return the monotonic timestamp of the last successful use, or None."""
        entry = self._store.get(url)
        if entry is None:
            return None
        return entry["last_used"].get(selector)

    def forget(self, url: str, selector: Optional[str] = None) -> None:
        """Forget a URL (all selectors) or a specific URL+selector pair.

        No-op if the URL/selector is not in memory.
        """
        if selector is None:
            self._store.pop(url, None)
            return
        entry = self._store.get(url)
        if entry is None:
            return
        entry["selectors"].pop(selector, None)
        entry["last_used"].pop(selector, None)
        # If the URL has no selectors left, remove it entirely
        if not entry["selectors"]:
            del self._store[url]

    def clear(self) -> None:
        """Wipe all memory."""
        self._store.clear()


# ─── Global singleton ─────────────────────────────────────────────────────────

_selector_memory: Optional[SelectorMemory] = None


def get_selector_memory() -> SelectorMemory:
    global _selector_memory
    if _selector_memory is None:
        _selector_memory = SelectorMemory()
    return _selector_memory
