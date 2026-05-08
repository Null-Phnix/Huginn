# Huginn Roadmap

**Current Version:** 1.2.0
**Status:** Beta — Feature Complete, Needs Real-World Validation

---

## Beta Definition

A Beta release means:
- Core features work reliably on test fixtures
- CLI is fully functional with 15 commands
- API exposes all major operations via REST
- Test coverage is comprehensive (286 tests)
- Docker image builds and runs

**All criteria met!**

---

## Milestone 1: Core Scraping (Priority: Critical) ✅ COMPLETE

**Goal:** Reliable single-page and multi-page extraction

### Tasks
- [x] 1.1 Playwright browser backend with stealth patches
- [x] 1.2 Smart wait strategies (selector, networkIdle, domContentLoaded, timeout)
- [x] 1.3 Render mode selection (AUTO / FULL / LIGHT)
- [x] 1.4 Markdown, HTML, links, screenshot, raw output formats
- [x] 1.5 robots.txt respect with override flag
- [x] 1.6 Retry with exponential backoff
- [x] 1.7 Error recovery for transient failures

**Success Criteria:** `huginn scrape <url>` works on static, SPA, and JS-heavy sites ✅

---

## Milestone 2: Crawling & Mapping (Priority: Critical) ✅ COMPLETE

**Goal:** Recursive site exploration without duplicates

### Tasks
- [x] 2.1 Depth-limited recursive crawl
- [x] 2.2 Content hashing for duplicate detection (Blake2b)
- [x] 2.3 URL normalization (strip fragments, deduplicate params)
- [x] 2.4 robots.txt filtering
- [x] 2.5 Sitemap/URL discovery (`/v1/chart`)
- [x] 2.6 Infinite scroll handling (ScrollConfig)

**Success Criteria:** Can crawl a 100-page site without duplicates ✅

---

## Milestone 3: Structured Extraction (Priority: High) ✅ COMPLETE

**Goal:** Extract typed data from unstructured HTML

### Tasks
- [x] 3.1 10 built-in templates (product, article, job, real_estate, person, event, review, faq, recipe, research_paper)
- [x] 3.2 Template API with schema validation
- [x] 3.3 LLM-guided extraction with fallback chain
- [x] 3.4 JSON repair pipeline (6-step progressive fallback)
- [x] 3.5 Schema-guided retry with field-level error reporting
- [x] 3.6 Mental model extraction for complex pages

**Success Criteria:** `/v1/distill` with `template: "product"` returns valid JSON ✅

---

## Milestone 4: Research & Memory (Priority: High) ✅ COMPLETE

**Goal:** Deep multi-hop research with persistent knowledge

### Tasks
- [x] 4.1 DeepResearcher with iterative investigation
- [x] 4.2 ChromaDB vector memory for findings
- [x] 4.3 Semantic search over accumulated research
- [x] 4.4 Related topic discovery
- [x] 4.5 Report persistence and deletion
- [x] 4.6 Chunking with overlap for long pages

**Success Criteria:** Research pipeline runs end-to-end and stores results ✅

---

## Milestone 5: CLI & API (Priority: Critical) ✅ COMPLETE

**Goal:** Full user interface via terminal and HTTP

### Tasks
- [x] 5.1 Click-based CLI with 15 commands
- [x] 5.2 Interactive REPL mode with banner
- [x] 5.3 FastAPI REST server (`huginn serve`)
- [x] 5.4 SSE streaming for long operations
- [x] 5.5 Batch processing with progress bars
- [x] 5.6 Shell completion generation
- [x] 5.7 Output formats (json, yaml, csv, markdown, raw)

**Success Criteria:** `huginn doctor` reports 13 OKs ✅

---

## Milestone 6: Watch & Monitor (Priority: Medium) — Partial

**Goal:** Detect page changes over time

### Tasks
- [x] 6.1 Watch add/list/remove CLI commands
- [x] 6.2 Page content hashing for change detection
- [x] [ ] 6.3 Background polling daemon (no scheduler yet)
- [x] [ ] 6.4 Webhook notifications on change
- [x] [ ] 6.5 Cron-based scheduling

**Status:** CLI works. No automatic polling — watches are checked manually via `huginn watch check`.

---

## Milestone 7: PyPI & Distribution (Priority: High) — In Progress

**Goal:** Install via `pip install huginn`

### Tasks
- [x] 7.1 `pyproject.toml` with proper metadata
- [x] 7.2 Wheel builds successfully
- [x] 7.3 Docker image builds and runs
- [ ] 7.4 Publish to PyPI
- [ ] 7.5 `pip install huginn` smoke test on clean machine
- [ ] 7.6 Version tagging and GitHub Release

---

## Milestone 8: Performance & Scale (Priority: Medium) — Future

**Goal:** Handle large-scale operations efficiently

### Tasks
- [ ] 8.1 Streaming JSON lines (jsonl) for batch outputs
- [ ] 8.2 Concurrent crawl workers (currently sequential)
- [ ] 8.3 Connection pooling for HTTP requests
- [ ] 8.4 Memory pressure handling for long crawls
- [ ] 8.5 Benchmark suite vs Firecrawl

---

## Milestone 9: Real-World Validation (Priority: Critical) — Future

**Goal:** Prove it works on actual websites

### Tasks
- [ ] 9.1 Smoke test against 10 diverse live sites
- [ ] 9.2 E-commerce extraction benchmark (Amazon, Shopify, eBay)
- [ ] 9.3 News/article extraction benchmark
- [ ] 9.4 Academic paper extraction benchmark
- [ ] 9.5 Failure mode catalog (what breaks and why)

---

## Milestone 10: Ecosystem (Priority: Low) — Future

**Goal:** Integrate with the broader tooling ecosystem

### Tasks
- [x] 10.1 MCP server (7 tools: probe, sweep, chart, seek, distill, flock, jobs)
- [ ] 10.2 Enhanced MCP (streaming progress, error details)
- [ ] 10.3 GitHub Actions CI for tests
- [ ] 10.4 Pre-built Docker image on GHCR
- [ ] 10.5 SDK for additional languages (JS, Go)

---

## Progress Summary

| Milestone | Status | Notes |
|-----------|--------|-------|
| 1. Scraping | ✅ 100% | All formats, all wait strategies |
| 2. Crawling | ✅ 100% | Deduplication, infinite scroll |
| 3. Extraction | ✅ 100% | 10 templates, JSON repair |
| 4. Research | ✅ 100% | Vector memory, semantic search |
| 5. CLI/API | ✅ 100% | 15 commands, SSE, batch |
| 6. Watch | 🔶 50% | CLI done, no daemon |
| 7. PyPI | 🔶 60% | Wheel + Docker ready, not published |
| 8. Performance | ❌ 0% | Not started |
| 9. Real-World | ❌ 0% | Not started |
| 10. Ecosystem | 🔶 30% | MCP exists, needs polish |

**Overall Progress: 75% Beta Complete**

---

## Post-Beta / 1.0.0 Targets

### v1.3.0 — Watch Daemon + Scheduler
- Background polling with `APScheduler` or `systemd` timer
- Webhook notifications via `huginn/webhook.py`
- `huginn schedule` command for cron-style jobs

### v1.4.0 — Performance
- Concurrent workers for crawl
- Streaming JSONL output
- Benchmark suite and comparison docs

### v1.5.0 — Real-World Hardening
- Live site test matrix (50 sites)
- Failure catalog with remediation
- Rate limit and CAPTCHA handling improvements

### v2.0.0 — Ecosystem
- Plugin system for custom templates
- JavaScript/Go SDKs
- GitHub marketplace action

---

*Last updated: 2026-05-07*
