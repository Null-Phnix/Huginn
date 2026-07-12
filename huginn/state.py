"""
Huginn shared state — central registry for app-wide singletons.

All modules (routers, tasks, api.py) access browser, job store, replay log,
scheduler, watcher, and config through get_state().  This replaces the
module-level globals that previously lived in api.py.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

from slowapi import Limiter
from slowapi.util import get_remote_address

from .browser import BrowserManager
from .config import HuginnConfig
from .job_store import JobStore
from .replay_log import ReplayLog
from .scheduler import Scheduler

# Rate limiter — shared across all routers
limiter = Limiter(key_func=get_remote_address)


@dataclass
class AppState:
    """Holds all app-wide singletons populated during lifespan startup."""

    config: Optional[HuginnConfig] = None
    browser: Optional[BrowserManager] = None
    job_store: Optional[JobStore] = None
    replay_log: Optional[ReplayLog] = None
    scheduler: Optional[Scheduler] = None
    watcher: Optional[Any] = None
    crawl_tasks: dict = field(default_factory=dict)
    browser_sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    browser_session_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    proxy_provider: Optional[Any] = None


# Module-level singleton — populated by lifespan() in api.py
_state = AppState()


def get_state() -> AppState:
    """Return the shared AppState singleton."""
    return _state


def reset_state() -> None:
    """Reset state to defaults (used in tests)."""
    global _state
    _state = AppState()
    limiter.reset()
