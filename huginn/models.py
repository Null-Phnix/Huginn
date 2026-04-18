"""
Huginn API Models — Pydantic v2 schemas for all endpoints.

Firecrawl-compatible interface with Huginn extensions.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, HttpUrl, field_validator


# ─── Enums ────────────────────────────────────────────────────────────────────

class OutputFormat(str, Enum):
    MARKDOWN = "markdown"
    HTML = "html"
    RAW_HTML = "raw_html"
    SCREENSHOT = "screenshot"
    LINKS = "links"
    METADATA = "metadata"


class ActionType(str, Enum):
    CLICK = "click"
    WAIT = "wait"
    SCROLL = "scroll"
    SCREENSHOT = "screenshot"
    TYPE = "type"
    PRESS = "press"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProxyMode(str, Enum):
    BASIC = "basic"
    STEALTH = "stealth"


# ─── Shared Models ─────────────────────────────────────────────────────────────

class Action(BaseModel):
    """Browser action to execute before extraction."""
    type: ActionType
    selector: Optional[str] = None
    text: Optional[str] = None
    key: Optional[str] = None
    direction: Optional[str] = "down"  # for scroll
    amount: Optional[int] = None  # pixels for scroll, ms for wait


class Location(BaseModel):
    """Geographic location for proxy."""
    country: Optional[str] = None
    languages: Optional[List[str]] = None


class ExtractOptions(BaseModel):
    """LLM extraction options."""

    prompt: Optional[str] = None
    schema_: Optional[Dict[str, Any]] = Field(None)
    system_prompt: Optional[str] = Field(None)


class ScrapeOptions(BaseModel):
    """Options passed to scrape during crawl."""

    formats: List[OutputFormat] = Field(default_factory=lambda: [OutputFormat.MARKDOWN])
    only_main_content: bool = Field(True)
    include_tags: Optional[List[str]] = Field(None)
    exclude_tags: Optional[List[str]] = Field(None)
    wait_for: Optional[Union[int, str]] = Field(None)
    timeout: int = 30000
    headers: Optional[Dict[str, str]] = None
    actions: Optional[List[Action]] = None
    max_retries: int = Field(2, ge=0, le=5)


# ─── Scrape Endpoint ──────────────────────────────────────────────────────────

class ScrapeRequest(BaseModel):
    """POST /v1/scrape request body."""

    url: str
    formats: List[OutputFormat] = Field(default_factory=lambda: [OutputFormat.MARKDOWN])
    headers: Optional[Dict[str, str]] = None
    wait_for: Optional[Union[int, str]] = Field(None)
    actions: Optional[List[Action]] = None
    extract: Optional[ExtractOptions] = None
    include_tags: Optional[List[str]] = Field(None)
    exclude_tags: Optional[List[str]] = Field(None)
    only_main_content: bool = Field(True)
    timeout: int = 30000
    proxy: Optional[ProxyMode] = None
    location: Optional[Location] = None
    # Huginn extensions
    stealth_mode: bool = Field(True)
    max_retries: int = Field(2, ge=0, le=5, description="Max retry attempts on transient errors")


class ScrapeData(BaseModel):
    """Scrape result data."""

    markdown: Optional[str] = None
    html: Optional[str] = None
    raw_html: Optional[str] = Field(None)
    screenshot: Optional[str] = None  # base64
    links: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None
    extract: Optional[Dict[str, Any]] = None


class ScrapeResponse(BaseModel):
    """POST /v1/scrape response."""
    success: bool
    data: Optional[ScrapeData] = None
    error: Optional[str] = None


# ─── Crawl Endpoint ───────────────────────────────────────────────────────────

class CrawlRequest(BaseModel):
    """POST /v1/crawl request body."""

    url: str
    max_depth: Optional[int] = Field(None)
    limit: Optional[int] = None  # max pages
    allow_backward_crawling: bool = Field(False)
    allow_external_links: bool = Field(False)
    include_paths: Optional[List[str]] = Field(None)
    exclude_paths: Optional[List[str]] = Field(None)
    scrape_options: Optional[ScrapeOptions] = Field(None)
    ignore_robots: bool = Field(False, description="Skip robots.txt checks when True")
    stream: bool = Field(False)


class CrawlStartResponse(BaseModel):
    """POST /v1/crawl response (async job started)."""
    success: bool
    id: str
    url: Optional[str] = None
    error: Optional[str] = None


class CrawlStatusResponse(BaseModel):
    """GET /v1/crawl/{id} response."""

    success: bool
    status: JobStatus
    completed: int = 0
    total: Optional[int] = None
    expires_at: Optional[datetime] = Field(None)
    data: Optional[List[ScrapeData]] = None
    error: Optional[str] = None


# ─── Map Endpoint ─────────────────────────────────────────────────────────────

class MapRequest(BaseModel):
    """POST /v1/map request body."""

    url: str
    search: Optional[str] = None
    include_subdomains: bool = Field(False)
    limit: int = 5000


class MapResponse(BaseModel):
    """POST /v1/map response."""
    success: bool
    links: List[str] = Field(default_factory=list)
    error: Optional[str] = None


# ─── Extract Endpoint ─────────────────────────────────────────────────────────

class ExtractRequest(BaseModel):
    """POST /v1/extract request body."""

    urls: List[str] = Field(default_factory=list)
    prompt: Optional[str] = None
    schema_: Optional[Dict[str, Any]] = Field(None)
    system_prompt: Optional[str] = Field(None)
    # Huginn extensions
    mental_model: bool = Field(True)
    max_retries: int = 3
    stream: bool = Field(False)

    @field_validator("urls")
    @classmethod
    def validate_urls(cls, v):
        if not v:
            raise ValueError("urls must contain at least one URL")
        return v


class ExtractStartResponse(BaseModel):
    """POST /v1/extract response (async)."""
    success: bool
    id: str
    error: Optional[str] = None


class ExtractStatusResponse(BaseModel):
    """GET /v1/extract/{id} response."""
    success: bool
    status: JobStatus
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None



# ─── SSE Streaming Responses ─────────────────────────────────────────────────

class StreamCrawlResponse(BaseModel):
    """SSE event payload for crawl document/done events."""
    type: str  # "document" or "done"
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class StreamExtractResponse(BaseModel):
    """SSE event payload for extract progress/done events."""
    type: str  # "progress" or "done"
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class SearchOptions(BaseModel):
    """Search-specific options."""

    limit: int = 5
    tbs: Optional[str] = None  # time range
    country: Optional[str] = None
    language: Optional[str] = None


class SearchRequest(BaseModel):
    """POST /v1/search request body."""

    query: str
    search_options: Optional[SearchOptions] = Field(None)
    scrape_options: Optional[ScrapeOptions] = Field(None)
    # Huginn extension
    fallback_chain: bool = Field(True)  # Bing->DDG->Brave


class SearchResultItem(BaseModel):
    """Single search result with scraped content."""
    markdown: Optional[str] = None
    html: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class SearchResponse(BaseModel):
    """POST /v1/search response."""
    success: bool
    data: List[SearchResultItem] = Field(default_factory=list)
    error: Optional[str] = None



# ─── Batch Scrape Endpoint ─────────────────────────────────────────────────────

class FlockRequest(BaseModel):
    """POST /v1/batch/scrape request body."""

    urls: List[str] = Field(..., min_length=1, max_length=50)
    formats: List[OutputFormat] = Field(default_factory=lambda: [OutputFormat.MARKDOWN])
    only_main_content: bool = Field(True)
    include_tags: Optional[List[str]] = Field(None)
    exclude_tags: Optional[List[str]] = Field(None)
    timeout: int = 30000




class FlockResultItem(BaseModel):
    """Single result in a batch scrape response."""
    url: str
    success: bool
    data: Optional[ScrapeData] = None
    error: Optional[str] = None


class FlockResponse(BaseModel):
    """POST /v1/batch/scrape response."""
    success: bool
    data: List[FlockResultItem] = Field(default_factory=list)
    error: Optional[str] = None


# ─── Job Management �──────────────────────────────────────────────────────────

class JobInfo(BaseModel):
    """Job status info."""
    id: str
    type: str  # "crawl" or "extract"
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    completed: int = 0
    total: Optional[int] = None
    error: Optional[str] = None

