"""
BlackCrawl - Autonomous web scraping and crawling API.

A Firecrawl-compatible API built on Blackreach's stealth browser engine,
DOM walker, and mental model system. Better than Firecrawl because:

- StarSearch Rust daemon for anti-detection (15 JS modules, 80+ fingerprints)
- Semantic DOM walker instead of raw HTML parsing
- Mental model-assisted extraction with belief tracking
- Stuck detection and resilience from 3k+ real-world test failures
- All self-hosted, all free, no cloud tier holding back features
"""

__version__ = "1.0.0"
__author__ = "Phnix"

from .browser import BrowserManager, StarSearchBackend  # noqa: F401

__all__ = ["BrowserManager", "StarSearchBackend"]
