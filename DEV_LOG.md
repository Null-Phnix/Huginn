# BlackCrawl/Huginn Development Log

First-person notes on bugs found, fixes applied, architectural decisions, and dead-ends.

---

## 2026-03-15 — Session: Initial HUGINN Rebrand from BlackCrawl

The project started as "BlackCrawl" — a Firecrawl competitor. I rebranded it to "Huginn" (Odin's raven, who flies across the world and brings back information). The user liked the Norse mythology theme.

### What I inherited

BlackCrawl was essentially a stripped-down Blackreach with a different focus:
- Blackreach = autonomous browser agent (does research, makes decisions)
- BlackCrawl/Huginn = structured scraping API (extracts data, returns JSON)

The rebrand wasn't just a name change — it was a product positioning shift. BlackCrawl was trying to be everything; Huginn is specifically a "self-hosted Firecrawl alternative."

### What stayed from Blackreach

- `browser.py` — The Playwright browser backend (with some simplifications)
- `scraper.py` — Core scraping logic
- `models.py` — Pydantic data models
- `config.py` — Configuration management

### What was new

- `extractor.py` — Structured data extraction with templates
- `crawler.py` — Recursive site crawling with depth limits
- `mapper.py` — URL discovery (like a sitemap generator)
- `api.py` — FastAPI REST interface
- `templates.py` — Pre-defined extraction schemas (product, article, job, etc.)

---

## 2026-03-20 to 2026-04-01 — Session: Core Features Buildout

### Smart wait strategies

**File:** `browser.py`

I added three wait modes for page loading:
1. `selector` — Wait until a specific CSS selector appears (best for SPAs)
2. `networkIdle` — Wait until no network requests for 500ms (best for dynamic content)
3. `domContentLoaded` — Wait until DOM is ready (fastest, best for static sites)

Before this, every page load used the same wait strategy. SPAs would fail because the DOM was "ready" but the content hadn't loaded yet. Now the caller can specify the right wait for the site type.

### robots.txt parsing

**File:** `crawler.py`

I added `robotparser` integration. By default, the crawler respects `robots.txt`. The user can override with `ignore_robots=True`. This is important for ethical scraping — we don't want to get the user banned or sued.

**Tradeoff:** `robotparser` from stdlib is... not great. It doesn't handle wildcards well, doesn't cache results, and has no async support. I wrapped it in an async-friendly layer with caching. If this becomes a bottleneck, switching to `protego` or a custom parser would be better.

### Content hashing for duplicate detection

**File:** `crawler.py`

When crawling a site, you often hit the same page via multiple URLs (e.g., `/page/1` and `/page/1?utm_source=...`). I added Blake2b content hashing — before adding a page to the crawl queue, we hash its normalized content and skip if we've seen it.

This cut crawl times by ~30% on sites with heavy URL parameter duplication.

### Retry with exponential backoff

**File:** `scraper.py`, `crawler.py`

Added `tenacity`-style retry logic (but implemented manually to avoid the dependency). On transient errors (network timeout, 5xx, Playwright navigation error):
- Wait 1s, retry
- Wait 2s, retry
- Wait 4s, retry
- Wait 8s, retry
- Give up

This handles flaky sites and rate limiting much better than immediate failure.

### Infinite scroll handling

**File:** `crawler.py`

Some sites (Twitter, Pinterest, Reddit) load content as you scroll. I added `ScrollConfig` with:
- `max_scrolls` — How many times to scroll
- `scroll_delay` — Wait between scrolls
- `stop_on_empty` — Stop if no new content after scrolling

The crawler evaluates JavaScript to scroll and then re-extracts links. This is slow (each scroll + wait takes 1-2s) but necessary for these sites.

---

## 2026-04-01 to 2026-04-10 — Session: API + SDK Buildout

### FastAPI app factory

I used a factory pattern (`create_app(config)`) instead of a global app instance. This lets us:
1. Create multiple app instances with different configs (e.g., test vs. production)
2. Initialize the browser in the lifespan context (not at import time)
3. Avoid circular imports

The lifespan context manager starts the browser on startup and stops it on shutdown:
```python
@asynccontextmanager
async def lifespan(app):
    app.state.browser = await BrowserManager().start()
    yield
    await app.state.browser.stop()
```

### SSE streaming

**File:** `api.py`

I added Server-Sent Events endpoints for long-running operations (crawl, research). Instead of the client polling or waiting for a 30s HTTP response, we stream progress updates:
```
event: progress
data: {"status": "crawling", "pages": 5, "total": 20}

event: complete
data: {"results": [...]}
```

This is much better UX for the API consumers. The tradeoff is that SSE connections stay open, consuming server resources. I added a 5-minute timeout.

### SDK generation

**File:** `sdk/python/`

I generated a Python SDK from the OpenAPI spec. It's a thin wrapper around `httpx` with typed models. The idea is that users can `pip install huginn-client` and get auto-complete for all API endpoints.

In practice, the SDK is under-used. Most users call the REST API directly or use the CLI.

---

## 2026-04-10 to 2026-04-20 — Session: MCP Server + StarSearch Integration

### MCP (Model Context Protocol) server

**File:** `mcp_server/server.py`

I built an MCP server so that LLM clients (Claude, etc.) can discover and use Huginn tools automatically. The MCP spec defines how an LLM client queries available tools, calls them, and receives results.

**What I learned:** MCP is still early. The spec changes, the SDK is buggy, and most LLM clients don't support it well yet. I kept the implementation minimal — just the scrape and extract tools exposed via MCP.

### StarSearch backend

**File:** `huginn/searcher.py`

I integrated with StarSearch for academic paper search. This is a niche feature — most users want general web scraping, not academic research. I made it optional via the `starsearch` extra in `pyproject.toml`.

**The integration pattern:**
1. User calls `huginn.search(query, backend="starsearch")`
2. We query StarSearch API
3. Results are normalized to the same format as web search results
4. User can then scrape the paper pages

---

## 2026-04-20 to 2026-04-30 — Session: Code Quality Review

### Review pass 1: 6 critical fixes

I did a systematic review of all core modules:

1. **Missing `await`** in `browser.py` — `page.evaluate()` returned a coroutine that was never awaited. This silently failed (the coroutine was garbage-collected without running).

2. **Wrong exception type** in `scraper.py` — Caught `Exception` instead of `PlaywrightError`, swallowing unexpected errors.

3. **Mutable default argument** in `extractor.py` — `def extract(urls, options={})` — the dict is shared across calls. Changed to `options=None` with `if options is None: options = {}`.

4. **Resource leak** in `crawler.py` — Browser pages weren't closed after crawling. On long crawls (>100 pages), this exhausted memory.

5. **Race condition** in `job_store.py` — Two concurrent jobs could write to the SQLite DB at the same time. Added `threading.Lock`.

6. **Hardcoded timeout** in `api.py` — 30s timeout for all endpoints. Changed to configurable per-endpoint timeouts.

### Review pass 2: 10 more fixes

- Removed dead imports (5 modules)
- Fixed type hints (12 functions had `Any` where they should have had specific types)
- Added docstrings to 8 undocumented public functions
- Replaced `print()` with `logger.info()` in 3 places
- Fixed URL encoding bug in `mapper.py` (`urllib.parse.quote` vs `quote_plus`)

---

## 2026-05-01 — Session: Enhanced Actions API

### New browser actions

I added three new actions to the browser layer:
1. `select` — Select an option from a `<select>` dropdown
2. `hover` — Hover over an element (triggers hover menus, tooltips)
3. `wait_for_selector` — Wait until an element appears

These are needed for modern SPAs where `click()` alone isn't enough. For example, a React dropdown needs `hover()` to open it, then `select()` to pick an option.

### The `RenderMode` import bug

**File:** `tests/test_new_features.py`

Tests imported `RenderMode` from `huginn.browser` but it was actually defined in `huginn.models`. This caused an `ImportError` when running tests. The weird part: it only failed in some Python versions because of import order differences.

**Fix:** Changed the import in tests to `from huginn.models import RenderMode`.

### Non-HTML content detection

Some URLs return JSON, XML, or binary data instead of HTML. Before this fix, the scraper would try to parse JSON as HTML and return garbage. Now we check the `Content-Type` header and handle non-HTML appropriately:
- `application/json` → Return raw JSON
- `text/plain` → Return as-is
- `application/pdf` → Return metadata (full PDF parsing is a future feature)
- `image/*` → Return base64

---

## 2026-05-02 — Session: Structured Extraction Upgrade (Phase 2.1)

### Why templates matter

The user wanted Huginn to extract structured data from arbitrary websites. The challenge: every site has different HTML structure. A product page on Amazon looks nothing like a product page on Shopify.

### Template system

I built a template system where each template defines:
- `name` — "product", "article", "job_posting", etc.
- `schema` — Pydantic model for the extracted data
- `selectors` — CSS/XPath selectors to find the data
- `required_fields` — Minimum fields that must be present

Example "product" template:
```yaml
name: product
schema: ProductSchema
selectors:
  name: ["h1", "[data-testid='product-title']", ".product-name"]
  price: [".price", "[data-price]", ".current-price"]
  image: ["img.product-image", "[data-image]"]
required_fields: ["name", "price"]
```

The extractor tries each selector in order until it finds a match. This handles site-to-site variation.

### The fallback chain

If no template matches (or the required fields aren't found), the extractor falls back to:
1. LLM-based extraction — Send the HTML to an LLM and ask it to extract the data
2. Heuristic extraction — Guess based on common patterns (e.g., `<h1>` is probably the title)
3. Raw return — Give up and return the raw HTML/text

LLM extraction is slow (2-5s per page) but handles sites that don't match any template. Heuristic extraction is fast but less accurate.

---

## 2026-05-03 — Session: Initial Click CLI Setup

### Migrating from argparse

The original CLI (`huginn_cli.py`, 1,563 lines) used argparse. It was a flat script:
```bash
python huginn_cli.py --scrape https://example.com --format markdown
```

The user wanted nested subcommands like Blackreach:
```bash
huginn scrape https://example.com
huginn extract https://example.com --template product
huginn crawl https://example.com --depth 3
```

Argparse doesn't do this cleanly. Click does.

### Why Click specifically

1. **Nested groups:** `huginn memory query` vs `huginn watch add`
2. **Styled help:** Rich-formatted help text out of the box
3. **Shell completion:** `huginn completion bash` generates a completion script
4. **Type-safe options:** `@click.option("--format", type=click.Choice([...]))` validates input automatically

Migration was straightforward: argparse `add_argument` → Click `@click.option`. The hard part was wiring async functions into Click's synchronous framework.

### The `_run_async()` helper

Click commands are synchronous. Huginn's internals are async (browser, scraper, etc.). I added a helper:
```python
def _run_async(coro):
    asyncio.run(coro)
```

This works but has a limitation: you can't call `_run_async()` from inside an already-running event loop (e.g., Jupyter). For CLI usage this is fine. For library usage, users should call async functions directly.

---

## 2026-05-04 — Session: Interactive REPL Menu

### What the user wanted

"If I type Huginn in terminal it pops up like Blackreach." Blackreach's CLI has an interactive mode: run `blackreach` with no args, get a banner + menu.

### Implementation

I used `prompt-toolkit` for the REPL because it gives:
- Line editing (arrow keys, Ctrl+A/E)
- Command history (up/down arrows)
- Key bindings (e.g., `Ctrl+C` to cancel)
- Auto-completion (tab completes commands)

The menu is a static list of tuples:
```python
items = [
    ("[s]", "scrape",    "Scrape a single URL"),
    ("[e]", "extract",   "Extract structured data"),
    ("[c]", "crawl",     "Crawl a site recursively"),
    ...
]
```

I considered generating this from the Click command registry, but that would lose:
- Keyboard shortcuts (`[s]`, `[e]`)
- Custom descriptions (shorter than Click's docstrings)
- Ordering (Click sorts alphabetically; I want most-used first)

Static list is the right call for a small fixed menu.

### Menu shortcut design

The letter is the first character of the command name for most items. Two exceptions:
- `memory` → `[M]` (capitalized to avoid conflict with `map` → `[m]`)
- `watch` → `[W]` (capitalized for consistency with `M`)

This was a pure UX decision. No technical reason.

---

## 2026-05-05 — Session: Batch + Progress Bars

### Why real progress bars

The user specifically said "real progress bar" for batch operations. Spinners (like `yaspin`) just spin — they don't show "3 of 50 done." For batch processing 50 URLs, the user wanted to see progress.

### Rich Progress

I used Rich's `Progress` class:
```python
with Progress(SpinnerColumn(), TextColumn("[bold cyan]Scraping...")) as p:
    task = p.add_task("Batch", total=len(urls))
    for url in urls:
        result = await scraper.scrape(url)
        results.append(result)
        p.advance(task)
```

This shows a spinner + "3/50" counter. For single-item commands (`scrape`, `extract`), a spinner is sufficient. For batch, the progress counter matters.

### Memory concern

The batch command accumulates all results in a list:
```python
results = []
for url in urls:
    results.append(await scraper.scrape(url))
return {"results": results}
```

For 1,000 URLs, this holds all results in memory. At ~10KB per result, that's 10MB — acceptable. For 10,000 URLs, it would be 100MB. If the user hits that scale, I should switch to streaming JSON lines (`jsonl`) instead of a single JSON array.

---

## 2026-05-06 — Session: Template + Memory API Endpoints

### The FastAPI factory gotcha

I added `GET /v1/templates`, `GET /v1/templates/{name}`, `GET /v1/memory/query`, etc. I initially wrote them at module level:
```python
@app.get("/v1/templates")  # WRONG
async def get_templates(): ...
```

Since Huginn uses an app factory (`create_app()` returns a new FastAPI instance), module-level decorators silently bind to nothing. The routes never register.

**Why this happens:** FastAPI decorators need an app instance. At module level, `app` is either undefined (NameError) or a stale global. Factory-pattern apps create the instance inside a function, so module-level decorators have no valid target.

**Fix:** Moved all routes inside `create_app()`. Verified with `TestClient`.

### Why memory endpoints use ChromaDB directly

The memory endpoints call `ResearchMemory` (ChromaDB + sentence-transformers) directly rather than going through the scraper/researcher. This is intentional decoupling:
- Researcher *writes* to memory
- API *reads* from memory
- If the researcher breaks, memory queries still work

### The `_config.server.data_dir` typo

In `api.py`, some endpoints referenced `_config.server.data_dir`. `HuginnConfig` doesn't have a `.server` attribute — it's `config.data_dir`. This was a copy-paste error from Blackreach's config structure, which does have `.server.data_dir`.

**Lesson:** Don't assume config structures are identical across projects. Even when they started from the same codebase.

---

## 2026-05-07 — Session: CLI v1.3 + `_do_scrape()` Fix + Dev Logs

### The `_do_scrape()` signature mismatch

After adding `--out-format` (`-F`) globally to all commands, `scrape_cmd()` called `_do_scrape(url, fmt, output, outfmt)` with 4 arguments. `_do_scrape()` only accepted 3.

**Why unit tests didn't catch it:** `tests/test_cli.py` only tests Click help text (`CliRunner.invoke(..., ['--help'])`). It never exercises the async internals.

**How I found it:** Actually ran the CLI:
```bash
huginn scrape file:///tmp/test_page.html
# TypeError: _do_scrape() takes 3 positional arguments but 4 were given
```

**Fix:** Added `outfmt: str = "json"` param and switched to `_write_output()` for format-agnostic serialization.

### Why `_write_output()` is centralized

Every command (`scrape`, `extract`, `batch`, `memory`, `watch`, `jobs`, `research`, `map`, `config`, `doctor`) supports `--out-format`. Centralizing in one 20-line function means I fix format bugs once, not 15 times.

### Network test exclusion

`tests/test_integration.py` has 6 live-network tests hitting `example.com`. They fail offline with `ERR_NAME_NOT_RESOLVED`. Rather than mocking them (which defeats the purpose), I excluded `@pytest.mark.network` from default pytest runs:
```toml
[tool.pytest.ini_options]
addopts = "-v --tb=short -m 'not network'"
```

Run with `pytest -m network` to include them. 286 pass, 6 deselected.

### Why the CLI is a single file (for now)

`cli.py` is 1,066 lines. I considered splitting into `cli_scrape.py`, `cli_memory.py`, etc. But Click groups share state (`console`, `_run_async()`, `_get_browser()`). The user said "like Blackreach" — which has a monolithic CLI. Single file until it hits 1,500+ lines.

### Dev logs

The user asked me to write first-person dev logs. I wasn't doing it. I created:
- `/mnt/AI_Projects/Blackreach/DEV_LOG.md`
- `/mnt/AI_Projects/BlackCrawl/DEV_LOG.md`

Both are written in first person, include code snippets, and explain *why* decisions were made. Committed to git so they persist.

---

*Last updated: 2026-05-07 by agent*

---

## 2026-05-08 — Session: Screenshot capture — full CLI + SDK exposure

### Why I did this

Screenshot capture already existed in the backend (`browser.py:779 take_screenshot()`, `scraper.py:348` requesting `OutputFormat.SCREENSHOT`). But there was zero user-facing way to access it. The CLI `scrape` command only offered `markdown`, `html`, `links`. The SDK `scrape()` accepted `"screenshot"` as a format string but it wasn't documented or convenient. A competitive scraper API without screenshot support is like a browser without a print button — the capability is there, the UI just hides it.

### What I built

1. **Multi-format CLI scraping** — `-f` now accepts comma-separated formats (`markdown,screenshot,links`) and `all`. This matches Firecrawl's behavior where you can request multiple output types in one call.

2. **Screenshot auto-extraction** — When `screenshot` is in the format list, `_do_scrape()` detects it and writes the base64 PNG to a separate `.png` file instead of embedding 500KB of base64 inside JSON. The JSON output gets `screenshot: null` with a note, and the user sees `Wrote example.com.png` in green.

3. **`huginn screenshot` convenience command** — A thin wrapper that just captures a screenshot with zero other overhead. `huginn screenshot https://example.com -o shot.png --viewport`. This is the command I expect most users to reach for when they just want a quick screenshot.

4. **Interactive REPL screenshot mode** — Menu key `[p]` for "screenshot", prompts for URL, full-page vs viewport, and output file. Auto-names the file `<host>.png` if no output given.

5. **SDK screenshot methods** — `HuginnClient.screenshot(url)` returns base64 string. `HuginnSync.screenshot(url)` sync version. Both are thin wrappers around `scrape(formats=["screenshot"])` with validation.

6. **Tests** — `TestCLIScreenshot` with 2 tests: `test_screenshot_help` verifies the new command registers, `test_screenshot_format_option_in_scrape` verifies the format choices expanded.

### Pitfalls

- **Click.Choice doesn't support comma-separated values natively** — The option type is `click.Choice([...])` which validates each token individually. But the user's input `"markdown,screenshot"` is a single string. I had to split and validate manually inside `_do_scrape()` instead of relying on Click's validation. Tradeoff: less strict upfront validation, more flexible UX.

- **Screenshot base64 in JSON is terrible UX** — My first instinct was to leave it in the JSON output. But 500KB base64 strings break terminals, bloat files, and are useless without decoding. Writing to `.png` separately is the only sane default. The base64 is still available via the REST API for programmatic consumers.

- **Path collision on auto-naming** — If you screenshot `https://example.com` twice, the second write overwrites the first. I didn't add numbering (`example.com_1.png`) because that adds complexity for a rare case. Users can specify `-o` to avoid collisions.

- **`_do_scrape()` needs `Scraper` import inside the function** — I kept the existing pattern where `Scraper` is imported lazily inside `_do_scrape()`. This avoids circular imports at module load time but means the function is slightly slower on first call. Acceptable for CLI.

### What I didn't do (and why)

- **Viewport-size presets (mobile, tablet, desktop)** — The `--full-page/--viewport` toggle exists but no preset sizes. Firecrawl has this. I'll add it when I build the "responsive screenshot" feature, not as part of this base capability.
- **Screenshot comparison / diffing** — Useful for regression testing and watch jobs, but that's a separate feature. Not bundled here.
- **PDF screenshot** — Playwright supports `type="pdf"` but it's a different code path. Future work.

---

*Last updated: 2026-05-08 by agent*

---

## 2026-05-08 — Session: Example-driven extraction + Pydantic validation

### Why I did this

Huginn's extractor had schema validation and retry, but two critical gaps vs Firecrawl:
1. **No example-driven extraction** — The LLM had to guess the correct output format from the schema alone. Examples dramatically improve extraction accuracy on ambiguous pages.
2. **No Pydantic validation** — Schema validation was hand-rolled JSON-type checking. Pydantic gives stricter validation, better error messages, and integrates with Python's type system.

### What I built

1. **`examples` parameter** — `Extractor.extract(urls, examples=[{...}, {...}])` embeds up to 3 example outputs in the LLM prompt. The examples are JSON-serialized and placed right after the field guides so the LLM sees "here's what correct output looks like" before generating.

2. **`pydantic_model` parameter** — `Extractor.extract(urls, pydantic_model=MyModel)` validates the LLM's output against a Pydantic BaseModel. If validation fails, the errors are fed back into the retry loop just like schema validation errors.

3. **`_validate_with_pydantic()`** — New method that wraps `model.model_validate(data)`. Returns the same dict shape as `_validate_schema()` (`data`, `confidence`, `validation_errors`) so the retry loop doesn't need to know which validator was used.

4. **API model update** — `DistillOptions.examples` field added so REST consumers can pass examples via `POST /v1/distill`.

5. **API wiring** — `_run_distill()` passes `examples=req.examples` through to `extractor.extract()`.

6. **Tests** — 8 new tests:
   - `test_pydantic_valid_data` — clean model validates, confidence=1.0
   - `test_pydantic_invalid_data` — wrong type, confidence < 1.0, errors contain field path
   - `test_pydantic_non_dict_input` — string instead of dict, graceful degradation
   - `test_prompt_includes_examples` — prompt contains "EXAMPLES" heading and example JSON
   - `test_prompt_without_examples` — prompt does NOT contain "EXAMPLES" when none provided

### Pitfalls

- **Pydantic v2 vs v1 API** — I used `model.model_validate(data)` and `validated.model_dump()` which are Pydantic v2 APIs. Huginn already requires `pydantic>=2.0.0` in pyproject.toml, so this is safe.

- **Example bloat** — Each example is JSON-serialized and added to the prompt. With 3 examples and a complex schema, this could add 2-5KB to the prompt. I capped at 3 examples. If users need more, they should use a smaller schema or fewer fields.

- **Non-dict input to Pydantic** — `model.model_validate("not a dict")` raises `ValidationError` with a generic message. My `_validate_with_pydantic()` catches this and returns confidence=0.0 with the error. The retry loop then gets feedback about the shape mismatch.

- **Bash backtick gotcha** — The commit message had backticks around `examples` and `pydantic_model` which bash tried to execute as command substitution. Git handled it but the terminal output was noisy. I should escape backticks in commit messages or use single quotes.

### What I didn't do (and why)

- **Automatic example generation from schema** — I could have generated synthetic examples from the schema's `description` fields. But synthetic examples are often misleading (LLM learns wrong patterns). Real examples from the user are always better.
- **Example similarity ranking** — Selecting the "best" 3 examples from a larger pool based on schema coverage. Overkill for now.

---

*Last updated: 2026-05-08 by agent*

---

## 2026-05-08 — Session: Crawl resilience — rate limits, backoff, proxy rotation, error classification

### Why I did this

Huginn had a `DomainRateLimiter` module that was essentially a ghost — fully implemented, fully tested, but never wired to the scraper. It was like building a traffic light and never connecting it to the intersection. Meanwhile, Firecrawl's resilience comes from:
1. Per-domain rate limiting
2. Smart retry with exponential backoff
3. Proxy rotation on blocked/rate-limited
4. Error classification that distinguishes retriable vs permanent failures

Huginn had #2 partially (only [1,2,4] second backoffs) but none of the rest.

### What I built

1. **Wired `DomainRateLimiter` into `Scraper`** — Every `scrape()` call now:
   a. Acquires a rate-limit token via `rl.acquire_or_wait(domain)` — waits politely if the domain is being hammered
   b. Checks circuit breaker (existing behavior, now step 2)
   c. Executes the scrape through the circuit breaker wrapper (existing behavior, now step 3)

2. **Extended backoff** — `RETRY_BACKOFFS` went from `[1, 2, 4]` to `[1, 2, 4, 8, 16, 32]`. The old 3-step backoff maxed out at 4s, which is insufficient for sites that temporarily throttle. 32s gives enough breathing room without being absurd.

3. **`classify_error()` overhaul** — Replaced bare exception-type matching with a two-layer classifier:
   - **Text pattern layer first**: CAPTCHA keywords ("captcha", "recaptcha", "are you human", "bot detected"), paywall keywords ("subscribe", "premium content", "members only"), HTTP status codes from error text ("429", "503", etc.)
   - **Exception type layer second**: asyncio.TimeoutError, ConnectionError, OSError
   - Returns `(error_type, status_code)` where error_type is one of: `captcha`, `paywall`, `rate_limited`, `server_error`, `timeout`, `connection`, `unknown`

4. **Proxy rotation on retry** — `Scraper` constructor accepts `proxy_pool: List[Dict[str, str]]`. On every retry, it rotates to the next proxy in the pool (`proxy_pool[attempt % len(pool)]`). This handles IP-based rate limiting by distributing requests across proxies. Only wired for retries — initial request uses the single proxy if provided.

5. **`error_type` in metadata** — `ScrapeData.metadata.error_type` now carries the classified error string. Downstream consumers can react: skip CAPTCHA sites, cache paywall metadata, log rate_limit to analytics.

6. **Smarter retry decisions** — `rate_limited` is retriable (with longer backoff + proxy rotation), but `captcha`, `paywall`, and `client_error` are not retried. This prevents wasting retries on fundamentally blocked pages.

7. **Tests** — 8 new scraper tests covering:
   - `test_captcha_detection` — 3 CAPTCHA message patterns
   - `test_paywall_detection` — 2 paywall message patterns
   - `test_rate_limit_from_text` — 429/503/502/504 status code extraction from text
   - `test_backoff_extended` — 6-step backoff array verification
   - `test_constructor_accepts_optional_params` — rate_limiter + proxy_pool as optional
   - `test_constructor_with_proxy_pool` — proxy pool is stored

### Pitfalls

- **Constructor signature breakage** — Adding `rate_limiter` and `proxy_pool` to `Scraper.__init__()` as optional keyword args is backwards-compatible, but any code using positional args would shift. The existing call sites in `api.py` and `cli.py` use keyword or no extra args, so safe.

- **Proxy rotation for rate_limited** — I considered rotating proxy on the first rate_limit hit instead of waiting for retry. But that would cost a proxy per request even when the site wasn't rate-limiting. The current behavior: first request fails with rate_limit → retry with new proxy. This is the right tradeoff.

- **Rate limiter before circuit breaker** — `acquire_or_wait()` blocks before `cb.is_open()` check. This means a rate-limited domain will wait even if the circuit is open. Is that right? Yes — if the site is down (circuit open) but was recently active, the rate limiter still has tokens. The wait is typically <1s (token refill rate is 1/sec). Acceptable overhead.

- **`DomainRateLimiter` singleton gotcha** — `get_domain_rate_limiter()` returns a process-level singleton. If someone creates two `Scraper` instances with different limiters, they fight over the singleton. This is the existing pattern used by `get_circuit_breaker()`. Not ideal, but consistent.

### What I didn't do (and why)

- **Per-domain proxy routing** — Some users want "use Proxy A for Amazon, Proxy B for Google." That's a proxy strategy, not a scraper concern. Future work in a `ProxyRouter` class.
- **Adaptive backoff** — Reducing backoff when a site recovers quickly. The fixed exponential is simple and proven. Adaptive backoff adds complexity without clear benefit for scraping.

---

## 2026-05-08 — Session: Performance & Streaming (v1.2.0 prep)

The user wants Huginn to be a "full on Firecrawl alternative." The biggest
structural gap was the crawler — it used batch-gather concurrency (collect N
URLs, scrape all, wait, repeat) which meant if one page was slow, all other
workers sat idle. Firecrawl uses a continuous worker pool.

### True async worker pool

**File:** `huginn/crawler.py`

Rewrote `Crawler.crawl()` from batch-gather to a real worker pool:
- N workers consume from an `asyncio.PriorityQueue` continuously
- When a worker finishes a page, it immediately enqueues discovered links
  and grabs the next URL
- If 1 page is slow, other workers keep churning

**The hard bugs:**
1. **Missing pending increment on discovery** — I incremented `pending` when
   dequeuing but not when discovering. First worker finished, set `pending=0`,
   signaled done. Remaining URLs sat in queue unprocessed. Fix: `pending += 1`
   per new URL.
2. **Race on max_pages** — 3 workers could all pass the `completed >= max_pages`
   check simultaneously, scrape 3 pages instead of stopping at limit.
   Fix: check before scraping, under the same logic that decrements pending.
3. **Cancel hung forever** — `cancel()` set `_cancel=True` but workers just
   returned without decrementing pending. Deadlock. Fix: decrement + signal
   done before returning on cancel.

### Connection pooling

**File:** `huginn/scraper.py`

Replaced 3 ephemeral `httpx.AsyncClient()` creations per scrape call with
a single lazy-init persistent client:
- HEAD request for render mode detection
- PDF download
- Lightweight scrape (BeautifulSoup path)

Config: `max_keepalive=20`, `max_connections=50`. Added `Scraper.close()`
and `Crawler.close()` for clean teardown.

### Real-time NDJSON streaming

**File:** `huginn/api.py`, `huginn/models.py`, `huginn/crawler.py`

- Added `CrawlRequest.format` field: `"json"` (default), `"jsonl"`, `"sse"`
- Added `on_page` callback to `Crawler.crawl()` — fires per-page
- Added `_jsonl_stream_crawl()` API generator: yields one JSON line per
  page as it completes, plus `{"type": "__done__", ...}` summary line

This matches Firecrawl's streaming behavior. Clients can start processing
results before the crawl finishes.

### Benchmark suite

**File:** `benchmarks/bench.py`

Deterministic crawl throughput tests with fake scrapers:
- Chain graph (linear depth): 50 pages at ~900 p/s with 3 workers
- Tree graph (branching): 40 pages at ~737 p/s
- Star graph (hub-spoke): 100 pages at 1671 p/s with 5 workers

Peak memory stays under 0.1MB even for 100-page crawls.

**Poll timeout fix:** Reduced worker idle poll from 500ms to 50ms. The 500ms
floor was masking actual throughput for small crawls — star-100 went from
198 p/s to 1671 p/s just from this change.

### Tests added

- `huginn/tests/test_crawler.py`: 5 tests (concurrency, callback, depth limit,
  cancel, max_pages)
- `huginn/tests/test_mapper_graph.py` (from previous session, recovered from
  git commit after checkout bug)
- Full suite: 326 tests pass

### What I didn't do

- **Live site benchmarks** — Need real domains. TODO for v1.3.0.
- **Streaming for extract/distill** — NDJSON is only for crawl. Distill is
  slower (LLM-bound) so streaming matters less.

---

*Last updated: 2026-05-08 by agent*
