# Firecrawl Competitive Research — HUGINN

**Date**: April 17, 2026
**Purpose**: Understand Firecrawl's real-world usage, pain points, and gaps to position HUGINN as the superior alternative.

---

## 1. How People Use Firecrawl Daily

- Converting web pages to LLM-ready markdown for RAG/AI pipelines
- Providing scraped data to AI agents via MCP server (Claude, Cursor)
- Full website crawling for knowledge base building
- Structured data extraction using natural language prompts
- Social media monitoring (one user built a full social crawl tool on top)
- Used as fallback behind Crawl4AI for sites Crawl4AI can't handle
- AI agent tool — Firecrawl's MCP integration is a major adoption driver

---

## 2. Firecrawl's Critical Pain Points

### A. Self-Hosting is Broken (CRITICAL)
- Their own repo says **"not fully ready for self-hosted deployment yet"**
- Docker deployments fail with ERR_PNPM errors (#2516)
- Playwright microservice constantly crashes (#902, 34 comments)
- Screenshots not supported on self-host, closed as **"not planned"** (#1028)
- 186 closed self-host issues, 11 still open — **largest issue category by label**
- Bull queue auth key regex bug breaks Redis connections with special chars (#1791)
- Google Search rate limiting on self-host (#1140, 17 comments)

### B. Anti-Bot Only Works on Cloud (CRITICAL)
- "Fire-engine" anti-bot solution is **CLOSED SOURCE, SaaS-only**
- Self-hosted Firecrawl gets blocked by Cloudflare, Yahoo Finance, AT&T, GoDaddy
- firecrawl-simple fork creators called this **"the biggest deal breaker"**
- This is the entire reason people pay — and it's gated behind a paywall
- One HN user: runs local Crawl4AI first, Firecrawl API only as expensive fallback

### C. Pricing is Punitive
- $190/mo user quit entirely, wrote own scraper in 2700 lines of Elixir
- Extract plans are separate from scraping: **$89-$719/mo** for LLM extraction
- 500 one-time free credits, **no monthly renewal** on free tier
- 1 credit = 1 page scrape, 2 credits = 1 search (10 results), 2 credits = 1 browser minute
- Hobby tier at $19/mo for only 3,000 pages — heavy use is prohibitively expensive
- HN quote: *"a price tag for an MCP tool nearly as much as Claude Code makes no sense"*

### D. Deceptive Practices
- No identifiable user-agent — site owners **can't block it** (#1169, 7 thumbs up)
- HN users identified Firecrawl as **"problematic bot traffic"**
- AGPL license scares commercial users from forking/contributing
- Closed-source feature gating creates two-tier system undermining "open source" claim
- Firecrawl MCP vulnerability: *"A malicious tool might override the permissions of a more trusted one"*

### E. Product Instability
- Crawl status polling has **race conditions** (#2662)
- **Infinite loops** on anti-bot blocks (#2350, 25 comments)
- Non-deterministic scraping results — **even maintainers acknowledge this**
- Discussion #51 has **211+ reports** of broken URLs
- No graceful error handling for network failures

---

## 3. Feature Gaps People Want

| Feature | Firecrawl Status | Notes |
|---------|-----------------|-------|
| OCR for PDFs | Open since Oct 2024 | No progress |
| Embedded PDF scraping | Missing | Frequently requested |
| Screenshots on self-host | Closed as "not planned" | SaaS-only |
| Built-in scheduling/cron | Missing | Users want recurring crawls |
| Identifiable user-agent | Rejected by Firecrawl | Site owners can't block |
| Headful browser mode | Not available | Only headless Playwright |
| Webhook from SDK extraction | Missing | No async notification |
| Form interaction/search | Not available | Can't fill forms before scraping |
| Structured extraction schema | Available on cloud only | Pydantic schema enforcement |

---

## 4. Competitor Landscape

| Competitor | Stars/Size | Strengths | Weaknesses |
|-----------|-----------|-----------|------------|
| **Crawl4AI** | 64.2k stars | Most popular free alternative, modular, no LLM dependency | No anti-bot stealth |
| **Teracrawl** | Small | Headful Chrome, built because Firecrawl fails on JS-heavy sites | New, unproven |
| **Browserless.io** | Established | Works where Firecrawl self-host fails | SaaS, expensive |
| **Apify** | 6000+ scrapers | Massive library, anti-blocking, SOC 2 compliance | SaaS, complex |
| **firecrawl-simple** | Fork | Stable self-host on k8s | Still Firecrawl underneath |
| **Custom roll-your-own** | N/A | Full control | Maintenance burden |

---

## 5. Key Vulnerability Window

Firecrawl v3.0 was announced Feb 10, 2026 with promises of "open-source + local-first by default" — but got **ZERO community comments**. This signals low trust in their open-source commitment. Window is open for a credible alternative.

---

## 6. HUGINN Positioning

**Tagline**: "Self-hosted. Stealth-first. Actually works."

**Core differentiators vs Firecrawl**:

1. **Self-host that actually works** — Lean FastAPI + Playwright, no Bull queue, no Redis dependency, no pnpm microservice architecture. One Docker command. 134 tests passing.

2. **Stealth in the open-source version** — Firecrawl gatekeeps anti-bot behind SaaS. We include it. Free. Always. Our stealth browser patches are core, not premium.

3. **Identifiable user-agent** — Be a good web citizen. Sites can block us if they want. Ethical scraping.

4. **Local LLM extraction** — Ollama integration built-in. Extract from private pages, never send data to OpenAI. Firecrawl can't do this.

5. **Mental model extraction** — DOM walker + mental model system understands page structure before extracting. Smarter than raw LLM prompt extraction.

6. **Stability over features** — No race conditions, no infinite loops, deterministic results. Every edge case gets a test.

---

## 7. What We Need to Build (Priority Order)

### P0 — Ship-Stoppers (Must Have to Compete)

| # | Feature | Why | Effort |
|---|---------|-----|--------|
| 1 | **Structured extraction with Pydantic schema** | Firecrawl's #1 killer feature. Accept schema, extract with LLM, validate output, retry on failure. | Medium |
| 2 | **Smart page rendering** — networkIdle, waitForSelector, infinite scroll | Without this we can't render modern SPAs reliably. Our stealth gets us IN but we can't read the page properly. | Medium |
| 3 | **Docker one-liner deploy** | Firecrawl's Docker is broken. Ours must be trivial: `docker run huginn`. | Small |
| 4 | **Crawl intelligence** — content hashing, priority queue, pagination detection | Our crawler is dumb BFS. Need to skip duplicates, detect pagination, prioritize interesting pages. | Medium |

### P1 — Competitive Edge

| # | Feature | Why | Effort |
|---|---------|-----|--------|
| 5 | **Actions API** — click, scroll, fill, then scrape | Firecrawl has basic actions. We can do better with our browser foundation. | Medium |
| 6 | **Built-in scheduling** — cron-like recurring crawl/extract jobs | Frequently requested. No one offers this well in self-hosted. | Small |
| 7 | **Webhook notifications** — POST to URL when job completes | Essential for production workflows. | Small |
| 8 | **PDF/OCR extraction** | Open since Oct 2024 on Firecrawl with no progress. | Medium |

### P2 — Polish & Adoption

| # | Feature | Why | Effort |
|---|---------|-----|--------|
| 9 | **Python SDK client** | Developer ergonomics. Firecrawl has JS + Python SDKs. | Small |
| 10 | **OpenAPI spec** | Auto-generates SDKs in any language. | Small |
| 11 | **Web dashboard** — status, history, logs | Observability. We have nothing. | Large |
| 12 | **MCP server integration** | Firecrawl's MCP is a major adoption driver. Build one for HUGINN. | Small |

---

*This document should be updated as we complete each phase and gather more competitive intelligence.*