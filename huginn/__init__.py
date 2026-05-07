"""
Huginn — Odin's raven. Flies out, brings back knowledge.

Autonomous web scraping and crawling API. Built on Blackreach's
browser engine, DOM walker, and mental model system.

- Playwright with anti-detection stealth patches (webdriver removal,
  plugin spoofing, viewport normalization, navigator.webdriver cleanup)
- Semantic DOM walker instead of raw HTML parsing
- Extraction templates: product, article, job, real-estate, person,
  event, review, faq, recipe, research_paper — with field guides
- JSON repair pipeline (6-step progressive fallback) for reliable structured output
- Mental model-assisted extraction with belief tracking and schema-guided retry
- Research memory: ChromaDB vector persistence for accumulated knowledge
- Stuck detection and resilience from 3k+ real-world test failures
- All self-hosted, all free, no cloud tier holding back features
"""

__version__ = "1.0.0"
__author__ = "Phnix"

from .browser import BrowserManager, StarSearchBackend, WaitStrategy, parse_wait_for, ScrollConfig  # noqa: F401
from .cache import AsyncTTLCache, get_response_cache, cache_scrape_result, get_cached_scrape_result  # noqa: F401
from .circuit_breaker import CircuitBreaker, CircuitOpenError, extract_domain, get_circuit_breaker  # noqa: F401
from .memory import ResearchMemory  # noqa: F401
from .models import ActionType, Schedule, ErrorCode  # noqa: F401
from .research import ResearchAgent, ResearchReport, ResearchSource  # noqa: F401
from .scraper import RenderMode, detect_render_mode  # noqa: F401
from .scheduler import Scheduler  # noqa: F401
from .sdk import HuginnClient, HuginnError  # noqa: F401
from .templates import ExtractTemplate, get_template, list_templates, register_template  # noqa: F401
from .webhook import send_webhook, send_webhook_with_retry, fire_webhook_for_job  # noqa: F401

__all__ = [
    "BrowserManager",
    "StarSearchBackend",
    "WaitStrategy",
    "parse_wait_for",
    "ScrollConfig",
    "ActionType",
    "RenderMode",
    "detect_render_mode",
    "Schedule",
    "Scheduler",
    "send_webhook",
    "send_webhook_with_retry",
    "fire_webhook_for_job",
    "ExtractTemplate",
    "get_template",
    "list_templates",
    "register_template",
]
