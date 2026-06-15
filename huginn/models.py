"""
Huginn API Models — Pydantic v2 schemas for all endpoints.

Firecrawl-compatible interface with Huginn extensions.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


# ─── Enums ───────────────────────────────────────────────────────────────────

class ErrorCode(str, Enum):
    """Machine-readable error codes for all Huginn endpoints.

    Unlike error messages, these codes are stable across versions and
    can be used for programmatic error handling.
    """
    # Success variants
    SUCCESS = "success"

    # Client errors (4xx) — do not retry
    BAD_REQUEST = "bad_request"                   # 400
    INVALID_URL = "invalid_url"                  # 400
    MISSING_REQUIRED_FIELD = "missing_required"   # 400
    UNSUPPORTED_FORMAT = "unsupported_format"    # 400
    VALIDATION_ERROR = "validation_error"         # 400
    UNAUTHORIZED = "unauthorized"                 # 401
    FORBIDDEN = "forbidden"                      # 403
    NOT_FOUND = "not_found"                      # 404
    RATE_LIMITED = "rate_limited"               # 429
    CONTENT_TOO_LARGE = "content_too_large"      # 413

    # Server errors (5xx) — may retry
    TIMEOUT = "timeout"                           # 408 / 504
    CONNECTION_ERROR = "connection_error"         # 502 / 503
    UPSTREAM_ERROR = "upstream_error"            # 502
    CIRCUIT_OPEN = "circuit_open"                 # 503
    SERVICE_UNAVAILABLE = "service_unavailable"   # 503
    INTERNAL_ERROR = "internal_error"            # 500

    # Scraping-specific errors
    NAVIGATION_FAILED = "navigation_failed"
    CAPTCHA_DETECTED = "captcha_detected"
    BLOCKED_BY_ROBOTS = "blocked_by_robots"
    SSL_ERROR = "ssl_error"
    INVALID_RESPONSE = "invalid_response"
    EMPTY_CONTENT = "empty_content"
    PARTIAL_FAILURE = "partial_failure"           # some URLs failed
    ALL_URLS_FAILED = "all_urls_failed"

    # Job errors
    JOB_NOT_FOUND = "job_not_found"
    JOB_EXPIRED = "job_expired"

    @classmethod
    def from_http_status(cls, status: int) -> "ErrorCode":
        """Map an HTTP status code to the most appropriate ErrorCode."""
        mapping = {
            400: cls.BAD_REQUEST,
            401: cls.UNAUTHORIZED,
            403: cls.FORBIDDEN,
            404: cls.NOT_FOUND,
            408: cls.TIMEOUT,
            413: cls.CONTENT_TOO_LARGE,
            429: cls.RATE_LIMITED,
            500: cls.INTERNAL_ERROR,
            502: cls.UPSTREAM_ERROR,
            503: cls.SERVICE_UNAVAILABLE,
            504: cls.TIMEOUT,
        }
        return mapping.get(status, cls.INTERNAL_ERROR)


class OutputFormat(str, Enum):
    MARKDOWN = "markdown"
    HTML = "html"
    RAW_HTML = "raw_html"
    SCREENSHOT = "screenshot"
    LINKS = "links"
    METADATA = "metadata"
    PDF = "pdf"  # Extract text from PDF using OCR


class ActionType(str, Enum):
    CLICK = "click"
    WAIT = "wait"
    SCROLL = "scroll"
    SCREENSHOT = "screenshot"
    TYPE = "type"
    PRESS = "press"
    SELECT = "select"
    HOVER = "hover"
    WAIT_FOR_SELECTOR = "wait_for_selector"


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
    values: Optional[List[str]] = None  # for select (multiple options)
    timeout: Optional[int] = None  # ms for wait_for_selector


class Location(BaseModel):
    """Geographic location for proxy."""
    country: Optional[str] = None
    languages: Optional[List[str]] = None


class DistillOptions(BaseModel):
    """LLM extraction options."""

    prompt: Optional[str] = None
    schema_: Optional[Dict[str, Any]] = Field(None)
    system_prompt: Optional[str] = Field(None)
    examples: Optional[List[Dict[str, Any]]] = Field(
        None, description="Example extraction results to guide the LLM (max 3)"
    )


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
    scroll: bool = Field(False, description="Auto-scroll page to load dynamic content")
    render_mode: str = Field("auto", description="Rendering mode: auto, full (browser), light (httpx)")


# ─── Scrape Endpoint ──────────────────────────────────────────────────────────

class ScrapeRequest(BaseModel):
    """POST /v1/scrape request body."""

    model_config = ConfigDict(populate_by_name=True)

    url: str
    formats: List[OutputFormat] = Field(default_factory=lambda: [OutputFormat.MARKDOWN])
    headers: Optional[Dict[str, str]] = None
    cookies: Optional[Dict[str, str]] = Field(None, description="Cookies to set on requests")
    wait_for: Optional[Union[int, str]] = Field(None)
    actions: Optional[List[Action]] = None
    extract: Optional[DistillOptions] = None
    include_tags: Optional[List[str]] = Field(None)
    exclude_tags: Optional[List[str]] = Field(None)
    only_main_content: bool = Field(True)
    timeout: int = 30000
    proxy: Optional[ProxyMode] = None
    location: Optional[Location] = None
    # Huginn extensions
    stealth_mode: bool = Field(True)
    max_retries: int = Field(2, ge=0, le=5, description="Max retry attempts on transient errors")
    scroll: bool = Field(False, description="Auto-scroll page to load dynamic content before extraction")
    render_mode: str = Field("auto", description="Rendering mode: auto, full (browser), light (httpx)")
    # Firecrawl parity: skip TLS certificate verification. Default True
    # to match Huginn's historical hardcoded `ignore_https_errors=True`
    # (self-signed certs and broken CA chains have always worked in
    # Huginn; this keeps that working). Set False for stricter security.
    skip_tls_verification: bool = Field(
        True,
        alias="skipTlsVerification",
        description="Skip TLS certificate verification. Default True (matches Firecrawl).",
    )
    # Firecrawl parity: auto-generate a 1-2 sentence summary of the page
    summary: bool = Field(
        False,
        alias="summary",
        description="Generate a 1-2 sentence summary of the page using LLM.",
    )
    # Firecrawl parity: mobile device emulation. When True, the browser
    # is launched with a Playwright device descriptor (iPhone 13 by default)
    # so the page renders with mobile viewport, mobile UA, and touch support.
    mobile: bool = Field(
        False,
        alias="mobile",
        description="Emulate a mobile device (iPhone 13) for the scrape.",
    )
    # Firecrawl parity: block known ad network requests via Playwright
    # route interception. When True, requests to ad domains (doubleclick.net,
    # googlesyndication.com, etc.) are aborted before they reach the page.
    block_ads: bool = Field(
        False,
        alias="blockAds",
        description="Block requests to known ad network domains.",
    )
    # Firecrawl parity: strip inline base64 image data URIs from the
    # extracted markdown. Saves tokens + payload size for pages with
    # inlined SVG/data-URI images.
    remove_base64_images: bool = Field(
        False,
        alias="removeBase64Images",
        description="Strip data:image/...;base64,... URIs from extracted markdown.",
    )


class ScrapeData(BaseModel):
    """Scrape result data."""

    markdown: Optional[str] = None
    html: Optional[str] = None
    raw_html: Optional[str] = Field(None)
    screenshot: Optional[str] = None  # base64
    links: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None
    extract: Optional[Dict[str, Any]] = None
    pdf_text: Optional[str] = None


class ScrapeResponse(BaseModel):
    """POST /v1/scrape response."""
    success: bool
    data: Optional[ScrapeData] = None
    error: Optional[str] = None
    error_code: Optional[ErrorCode] = Field(None, description="Machine-readable error code")
    cached: bool = Field(False, description="Whether result was served from cache")
    warnings: Optional[List[str]] = Field(default_factory=list)
    # Firecrawl parity: LLM-generated 1-2 sentence summary of the page
    summary: Optional[str] = Field(None, description="Auto-generated 1-2 sentence summary")


# ─── Crawl Endpoint ───────────────────────────────────────────────────────────

class CrawlRequest(BaseModel):
    """POST /v1/crawl request body."""

    model_config = ConfigDict(populate_by_name=True)

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
    format: str = Field("json", description="Response format: json (default), jsonl (NDJSON stream), sse (Server-Sent Events)")
    webhook_url: Optional[str] = Field(None, description="URL to POST job completion/failure notifications")
    # Firecrawl parity: per-job maxConcurrency. When set, overrides the
    # global _config.crawl.concurrency for this specific crawl. Useful for
    # one-off fast scrapes (high concurrency) or slow / careful scrapes
    # (low concurrency).
    max_concurrency: Optional[int] = Field(
        None,
        alias="maxConcurrency",
        ge=1,
        le=100,
        description="Per-job override of crawl concurrency (1-100).",
    )


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
    error_code: Optional[ErrorCode] = Field(None, description="Machine-readable error code")
    warnings: Optional[List[str]] = Field(default_factory=list,
        description="Non-fatal warnings (e.g. some URLs failed when partial=True)")
    partial: Optional[bool] = Field(False,
        description="True when some URLs failed but partial results are returned")


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
    error_code: Optional[ErrorCode] = Field(None)


class PageNode(BaseModel):
    """A single page in a crawl graph."""
    url: str
    title: Optional[str] = None
    status_code: Optional[int] = None
    content_type: Optional[str] = None
    depth: int = 0  # BFS distance from start_url


class PageEdge(BaseModel):
    """A directed link between two pages."""
    source: str
    target: str


class CrawlGraph(BaseModel):
    """Directed graph of discovered pages and their interconnections."""
    nodes: List[PageNode] = Field(default_factory=list)
    edges: List[PageEdge] = Field(default_factory=list)
    start_url: str
    total_discovered: int = 0
    total_crawled: int = 0


class GraphRequest(BaseModel):
    """POST /v1/graph request body — BFS site graph mapping."""
    url: str
    include_subdomains: bool = Field(False)
    limit: int = Field(500, ge=1, le=5000)
    max_depth: int = Field(3, ge=1, le=10, description="BFS depth limit")


class GraphResponse(BaseModel):
    """POST /v1/graph response."""
    success: bool
    data: Optional[CrawlGraph] = None
    error: Optional[str] = None
    error_code: Optional[ErrorCode] = Field(None)


# ─── Distill Endpoint ─────────────────────────────────────────────────────────

class DistillRequest(BaseModel):
    """POST /v1/distill request body — structured data extraction from URLs."""

    urls: List[str] = Field(default_factory=list)
    prompt: Optional[str] = None
    schema_: Optional[Dict[str, Any]] = Field(None, description="JSON schema for structured extraction")
    system_prompt: Optional[str] = Field(None, description="Custom system prompt for LLM")
    format: str = Field("markdown", description="Output format: text, markdown, or json")
    # Huginn extensions
    mental_model: bool = Field(True, description="Use DOM mental model for better extraction")
    max_retries: int = Field(3, ge=0, le=5, description="Max extraction retry attempts")
    stream: bool = Field(False)
    webhook_url: Optional[str] = Field(None, description="URL to POST job completion/failure notifications")
    template: Optional[str] = Field(None, description="Use a built-in template: product, article, job_posting, real_estate, person, event, review, faq, recipe, research_paper")

    @field_validator("urls")
    @classmethod
    def validate_urls(cls, v):
        if not v:
            raise ValueError("urls must contain at least one URL")
        return v

    @field_validator("format")
    @classmethod
    def validate_format(cls, v):
        allowed = {"text", "markdown", "json"}
        if v.lower() not in allowed:
            raise ValueError(f"format must be one of {allowed}, got '{v}'")
        return v.lower()


class DistillStartResponse(BaseModel):
    """POST /v1/distill response (async job started)."""

    success: bool
    id: str
    error: Optional[str] = None


class DistillStatusResponse(BaseModel):
    """GET /v1/distill/{id} response."""

    success: bool
    status: JobStatus
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    error_code: Optional[ErrorCode] = Field(None)
    warnings: Optional[List[str]] = Field(default_factory=list)

# ─── SSE Streaming Responses ─────────────────────────────────────────────────

class StreamCrawlResponse(BaseModel):
    """SSE event payload for crawl document/done events."""
    type: str  # "document" or "done"
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class StreamDistillResponse(BaseModel):
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
    scrape_results: bool = Field(True)   # Whether to scrape result URLs (disable for fast search-only)


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

    model_config = ConfigDict(populate_by_name=True)

    urls: List[str] = Field(..., min_length=1, max_length=50)
    formats: List[OutputFormat] = Field(default_factory=lambda: [OutputFormat.MARKDOWN])
    only_main_content: bool = Field(True)
    include_tags: Optional[List[str]] = Field(None)
    exclude_tags: Optional[List[str]] = Field(None)
    timeout: int = 30000
    webhook_url: Optional[str] = Field(None, description="URL to POST job completion/failure notifications")
    # Firecrawl parity: when True, invalid URLs (bad scheme, missing host, etc.)
    # are skipped with a warning instead of failing the entire batch.
    ignore_invalid_urls: bool = Field(
        False,
        alias="ignoreInvalidURLs",
        description="Skip invalid URLs (bad scheme, missing host) with a warning instead of failing.",
    )




class FlockResultItem(BaseModel):
    """Single result in a batch scrape response."""

    url: str
    success: bool
    data: Optional[ScrapeData] = None
    error: Optional[str] = None
    error_code: Optional[ErrorCode] = Field(None, description="Machine-readable error code")
    cached: bool = Field(False, description="Whether result was served from cache")


class FlockResponse(BaseModel):
    """POST /v1/batch/scrape response."""
    success: bool
    data: List[FlockResultItem] = Field(default_factory=list)
    error: Optional[str] = None
    error_code: Optional[ErrorCode] = Field(None)
    partial: bool = Field(False,
        description="True when some URLs failed but partial results are returned")
    warnings: Optional[List[str]] = Field(default_factory=list)


# ─── Job Management ──────────────────────────────────────────────────────────

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


# ─── Schedule Model ─────────────────────────────────────────────────────────────

class ScheduleStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    RUNNING = "running"
    FAILED = "failed"


class Schedule(BaseModel):

    """ORM-style model for a persisted schedule (DB row)."""

    id: str
    name: str
    job_type: str
    request_json: str
    cron: Optional[str] = None
    interval_seconds: Optional[int] = None
    enabled: bool = True
    webhook_url: Optional[str] = None
    created_at: Optional[datetime] = None
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None

    @property
    def status(self) -> str:
        if not self.enabled:
            return "paused"
        return "active"


class ScheduleRequest(BaseModel):
    """POST /v1/schedule request body."""

    name: str = Field(..., description="Human-readable name for this schedule")
    job_type: str = Field(..., description="Type of job: sweep, distill, flock, or probe")
    cron: Optional[str] = Field(None, description="Cron expression (e.g. '0 9 * * *' for daily at 9am)")
    interval_seconds: Optional[int] = Field(None, description="Run every N seconds (alternative to cron)")
    request: Dict[str, Any] = Field(..., description="The job request body to execute")
    webhook_url: Optional[str] = Field(None, description="URL to POST when scheduled job fires")
    enabled: bool = Field(True)


class ScheduleResponse(BaseModel):
    """Schedule response model."""

    id: str
    name: str
    job_type: str
    cron: Optional[str] = None
    interval_seconds: Optional[int] = None
    request_json: str
    webhook_url: Optional[str] = None
    enabled: bool
    status: str = "active"
    created_at: Optional[datetime] = None
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None


# ─── Research Endpoint ───────────────────────────────────────────────────────────

class ResearchRequest(BaseModel):
    """POST /v1/research request."""
    query: str = Field(..., description="The research question or topic")
    depth: int = Field(3, ge=1, le=5, description="Research depth (iterations)")
    max_sources: int = Field(20, ge=1, le=100, description="Maximum sources to consult")
    target_length: str = Field(
        "standard",
        description="Target report length: brief, standard, or comprehensive",
    )
    background_questions: Optional[List[str]] = Field(
        None, description="Specific sub-questions to investigate"
    )
    urls: Optional[List[str]] = Field(
        default_factory=list,
        description="Pre-specified URLs to scrape before doing any web search or query decomposition",
    )


class ResearchCitation(BaseModel):
    """A source citation in research response."""
    url: str
    title: str
    domain: str
    quote: str
    relevance_score: float
    timestamp: str
    accessed_at: datetime


class ResearchFinding(BaseModel):
    """A discrete finding from research."""
    topic: str
    claim: str
    supporting_citations: List[ResearchCitation]
    confidence: float
    contradicts: Optional[str] = None
    needs_verification: bool = False
    verified: bool = False


class ResearchResponse(BaseModel):
    """POST /v1/research response."""
    success: bool
    query: Optional[str] = None
    summary: Optional[str] = None
    report: Optional[str] = None
    findings: List[ResearchFinding] = Field(default_factory=list)
    citations: List[ResearchCitation] = Field(default_factory=list)
    confidence: float = 0.0
    sources_consulted: int = 0
    research_duration_seconds: float = 0.0
    depth_achieved: int = 0
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    error_code: Optional[ErrorCode] = None


# ─── Page Watch / Change Detection ─────────────────────────────────────────────

class WatchRequest(BaseModel):
    """POST /v1/watch request."""
    url: str = Field(..., description="URL to watch for changes")
    selectors: Optional[List[str]] = Field(
        None, description="CSS selectors to monitor (default: whole page)"
    )
    webhook_url: Optional[str] = Field(
        None, description="Webhook URL to fire when content changes"
    )
    check_interval_seconds: int = Field(
        3600, ge=60, le=604800,
        description="How often to check for changes (default: 1 hour)"
    )


class WatchSnapshot(BaseModel):
    """A point-in-time snapshot of a watched page."""
    content_hash: str
    detected_changes: Optional[List[str]] = None
    created_at: datetime


class WatchResponse(BaseModel):
    """POST /v1/watch response."""
    success: bool
    url: str
    domain: str
    content_hash: str
    change_count: int = 0
    last_check: Optional[datetime] = None
    last_change: Optional[datetime] = None
    message: Optional[str] = None
    error: Optional[str] = None
    error_code: Optional[ErrorCode] = None


class WatchStatusResponse(BaseModel):
    """GET /v1/watch/{url} response."""
    success: bool
    url: str
    domain: str
    enabled: bool
    webhook_url: Optional[str] = None
    selectors: List[str]
    snapshot_count: int
    change_count: int
    last_check: Optional[datetime] = None
    last_change: Optional[datetime] = None
    latest_hash: Optional[str] = None
    history: List[WatchSnapshot] = Field(default_factory=list)
    error: Optional[str] = None


# ─── Backward Compatibility Aliases ──────────────────────────────────────────
# Firecrawl-compatible names (deprecated, use HUGINN names instead)

ExtractOptions = DistillOptions
ExtractRequest = DistillRequest
ExtractStartResponse = DistillStartResponse
ExtractStatusResponse = DistillStatusResponse
StreamExtractResponse = StreamDistillResponse

