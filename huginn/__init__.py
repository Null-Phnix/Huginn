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

__version__ = "1.3.0"
__author__ = "Phnix"

from .browser import BrowserManager, StarSearchBackend, WaitStrategy, parse_wait_for, ScrollConfig  # noqa: F401
from .cache import AsyncTTLCache, get_response_cache, cache_scrape_result, get_cached_scrape_result  # noqa: F401
from .circuit_breaker import CircuitBreaker, CircuitOpenError, extract_domain, get_circuit_breaker  # noqa: F401
from .memory import ResearchMemory  # noqa: F401
from .models import ActionType, Schedule, ErrorCode  # noqa: F401
from .research import ResearchAgent, ResearchReport, ResearchSource  # noqa: F401
from .scraper import RenderMode, detect_render_mode  # noqa: F401
from .scheduler import Scheduler  # noqa: F401
# Public SDK (sdk/python/huginn_client/) is the canonical source for
# HuginnClient, HuginnSync, and the error hierarchy. Huginn's own code
# re-uses it instead of carrying a separate internal SDK.
#
# In dev (running from source tree), the SDK is at sdk/python/, not pip-
# installed. Add that to sys.path so the import works without `pip
# install -e sdk/python`. In a real install, the user has huginn-client
# installed and this path is a no-op.
import os as _os
import sys as _sys
_sdk_python_path = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
    "sdk", "python",
)
if _os.path.isdir(_sdk_python_path) and _sdk_python_path not in _sys.path:
    _sys.path.insert(0, _sdk_python_path)

try:
    from huginn_client import (  # noqa: F401
        HuginnClient,
        HuginnError,
        HuginnSync,
        CircuitOpenError,
        RateLimitError,
    )
except ImportError:
    pass
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
