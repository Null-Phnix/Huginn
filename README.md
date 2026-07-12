# Huginn — Odin's Raven 🜏

> *Huginn (Old Norse: "thought") is one of Odin's ravens — he flies across the world and brings back information.*

[![Version](https://img.shields.io/badge/version-1.3.0-7c3aed?style=flat-square&labelColor=07061a)](https://github.com/Null-Phnix/Huginn)
[![Python](https://img.shields.io/badge/python-3.10%2B-4ade80?style=flat-square&labelColor=07061a)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-829_passing-22d3ee?style=flat-square&labelColor=07061a)](tests/)
[![License](https://img.shields.io/badge/license-MIT-facc15?style=flat-square&labelColor=07061a)](LICENSE)

**Local-first search, scraping, crawling, extraction, browser-session, and job API.**

> Huginn is the data plane in the Blackreach/StarSearch suite. See the
> [suite architecture and honest replacement boundary](https://github.com/Null-Phnix/Blackreach/blob/main/docs/WEB_TOOL_SUITE.md).

---

## Table of Contents

- [Why I Built This](#why-i-built-this)
- [What It Does](#what-it-does)
- [Honest Boundaries](#honest-boundaries)
- [Near-Term Direction](#near-term-direction)
- [Quick Start](#quick-start)
- [Replacement Boundary](#replacement-boundary)
- [Architecture](#architecture)
- [Benchmarks](#benchmarks)
- [CLI](#cli)
- [API Endpoints](#api-endpoints)
- [Templates](#templates)
- [Installation](#installation)
- [Environment](#environment)
- [License](#license)

---

## Why I Built This

Huginn started as **BlackCrawl** — a stripped-down Blackreach focused on structured data extraction. I needed to scrape mythology texts for Bifrost (my knowledge base project) and Firecrawl wanted $0.005 per page. For a 10,000-page crawl that's $50. Per crawl. I do this weekly.

The rebrand to Huginn wasn't just a name change — it established a clean product boundary. Blackreach owns goal-driven browsing; Huginn owns deterministic web operations, durable jobs, caching, and policy. StarSearch owns browser execution. `blackreach-mcp` is the one adapter agents should register.

Blackreach handles "go find me state space model papers from 2024." Huginn handles "scrape this product page and give me structured JSON with price, availability, and specs." Production explicitly selects StarSearch and fails closed if it is unavailable. Playwright remains an opt-in development compatibility path, not a silent production fallback.

---

## What It Does

| Feature | What It Does |
|---------|-------------|
| **Search** | Keyless browser-rendered search through StarSearch |
| **Scrape** | URL → Markdown, HTML, links, screenshot, actions, and metadata |
| **Crawl** | Entire sites recursively with depth limits, dedup, robots.txt respect |
| **Map** | Site structure → sitemap-like URL lists with BFS graph (nodes + edges) |
| **Extract** | Structured data via LLM-guided templates (10 built-in schemas) |
| **Research** | Multi-hop deep dives with ChromaDB vector memory persistence |
| **Watch** | Page change detection with webhook notifications |
| **Batch/jobs** | Durable IDs, polling, cancellation, replay, and persisted state |
| **Browser sessions** | Authenticated StarSearch lifecycle and typed commands |
| **Stream/cache** | SSE/NDJSON progress, success-only caching, and request tracing |

---

## Honest Boundaries

- StarSearch changes browser identity signals; it does not create a new IP, residential egress, or geographic location.
- `direct` egress is the default. Real rotation requires explicitly configured proxy endpoints; unhealthy endpoints cool down and no proxy policy silently falls back to direct.
- Browser sessions are local, authenticated, capacity-limited StarSearch sessions. This is not managed multi-host Browserbase, public live view, or durable remote CDP.
- Structured LLM extraction still needs a configured local or hosted model. Search, scrape, crawl, screenshots, and browser commands do not need an LLM key.
- DNS rebinding after initial validation remains a documented residual risk. Redirects and browser subresources are revalidated.
- Keyless search currently has one browser-rendered engine, so a second engine and health scoring remain important resilience work.

## Near-Term Direction

1. Durable browser contexts and an explicitly scoped remote debugging boundary.
2. A provider plugin for managed proxy inventory, health checks, and geographic/session policy.
3. DNS pinning or equivalent connect-time destination enforcement.
4. Search-engine failover and scoring.
5. Retention policies for research memory and long-lived job/replay state.

---

## Quick Start

```bash
# After installing the StarSearch service and local secret files documented in
# Blackreach/docs/WEB_TOOL_SUITE.md:
docker compose up -d --build
curl --fail http://127.0.0.1:7432/health/ready
```

```bash
# Scrape a URL
curl -X POST http://127.0.0.1:7432/v1/scrape \
  -H "Authorization: Bearer $(<~/.config/huginn/api-key)" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "formats": ["markdown"]}'

# Start a durable crawl job
curl -X POST http://127.0.0.1:7432/v1/crawl \
  -H "Authorization: Bearer $(<~/.config/huginn/api-key)" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://docs.python.org/3", "max_depth": 1, "limit": 10}'

# Search through StarSearch
curl -X POST http://127.0.0.1:7432/v1/search \
  -H "Authorization: Bearer $(<~/.config/huginn/api-key)" \
  -H "Content-Type: application/json" \
  -d '{"query": "Prometheus monitoring", "limit": 5}'
```

---

## Replacement Boundary

| Capability | Local suite path | Boundary |
|---|---|---|
| **Deterministic web work** | Huginn REST/jobs/cache/streaming | Useful Firecrawl-style operations, not full wire compatibility |
| **Browser execution** | StarSearch local pool | No managed multi-host fleet or public live-view/CDP service |
| **Proxying** | Direct or configured static endpoints | No bundled residential/mobile proxy network |
| **Agent access** | One `blackreach-mcp` stdio adapter | Clients should not register legacy per-repo MCP servers |
| **Operations** | Loopback services, bearer auth, metrics, persisted SQLite | Operator owns capacity, egress, upgrades, and recovery |

---

## Architecture

```
Agents ──▶ blackreach-mcp ──┬──▶ Huginn ──▶ StarSearch browser pool
                            │      │
                            │      ├──▶ SQLite jobs/cache/replay
                            │      ├──▶ proxy provider/leases
                            │      └──▶ optional extraction LLM
                            └──▶ Blackreach goal orchestrator
```

---

## Benchmarks

Deterministic crawl throughput (fake scraper, no network):

| Graph | Workers | Pages | Time | Pages/sec |
|-------|---------|-------|------|-----------|
| Chain (depth 50) | 3 | 50 | 0.06s | **899** |
| Tree (branching 3, depth 3) | 5 | 40 | 0.05s | **737** |
| Star (hub + 100 leaves) | 5 | 101 | 0.06s | **1,671** |

Peak memory: **< 0.1 MB** for 100-page crawls.

Run your own: `python benchmarks/bench.py`

---

## CLI

```bash
huginn                          # Interactive mode with ASCII banner
huginn scrape <url> --format markdown
huginn crawl <url> --depth 3 --limit 50
huginn map <url> --limit 5000
huginn extract <url> --template product
huginn watch add <url> --webhook https://myapp.com/hook
huginn watch check <url>
huginn search <query>
huginn research "your research question"
huginn templates                # List all 10 extraction schemas
huginn serve --port 7432
huginn doctor                   # Check configured runtime and system health
huginn config                   # Show current config
```

---

## API Endpoints

### Canonical Suite Surface

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health`, `/health/ready` | GET | Liveness/readiness and StarSearch capacity |
| `/health/detailed`, `/v1/metrics` | GET | Authenticated egress, pool, and operation detail |
| `/v1/search` | POST | Browser-rendered keyless search |
| `/v1/scrape` | POST | Scrape one URL with formats/actions/screenshot |
| `/v1/crawl` | POST | Start a durable crawl job |
| `/v1/crawl/{id}` | GET/DELETE | Poll or delete a crawl job |
| `/v1/extract` | POST | Start structured extraction |
| `/v1/batch/scrape` | POST | Start a durable batch scrape |
| `/v1/browser/sessions` | POST/GET | Create or list authenticated browser sessions |
| `/v1/browser/sessions/{id}/commands` | POST | Execute a typed StarSearch command |

### Native Huginn Surface

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/probe` | POST | Native scrape route behind `/v1/scrape` |
| `/v1/sweep` | POST | Native crawl route with SSE/NDJSON support |
| `/v1/flock` | POST | Native batch operation |
| `/v1/distill` | POST | Structured LLM extraction with templates |
| `/v1/seek` | POST | Web search |
| `/v1/chart`, `/v1/graph` | POST | Site map and BFS graph |
| `/v1/research` | POST | Deep multi-hop research with memory |

### Templates & Memory

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/templates` | GET | List all extraction templates |
| `/v1/templates/{name}` | GET | Single template schema + field guides |
| `/v1/memory/query` | GET | Semantic search over research memory |
| `/v1/memory/reports` | GET | List all research reports |
| `/v1/memory/reports/{id}` | DELETE | Delete a report |
| `/v1/memory/related` | GET | Find related topics |

### Watch & Schedule

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/watch` | POST | Start page change detection |
| `/v1/watch/{url}` | GET | Watch status |
| `/v1/watch/{url}/check` | POST | Manual check |
| `/v1/watch/{url}` | DELETE | Stop watching |
| `/v1/schedule` | POST | Cron/interval scheduling |

---

## Templates

10 built-in extraction schemas with JSON schema, field guides, and merge strategy:

| Template | Use case |
|----------|----------|
| `product` | E-commerce listings — price, availability, specs |
| `article` | News/blog posts — title, author, publish date, summary |
| `job_posting` | Careers pages — role, company, salary, requirements |
| `real_estate` | Property listings — price, beds, baths, location |
| `person` | Profiles — name, title, company, links |
| `event` | Event pages — date, venue, tickets, lineup |
| `review` | Review aggregators — rating, text, reviewer, date |
| `faq` | FAQ pages — question, answer, category |
| `recipe` | Food sites — ingredients, steps, time, nutrition |
| `research_paper` | Academic PDFs — title, authors, abstract, citations |

---

## Installation

```bash
git clone https://github.com/Null-Phnix/Huginn.git
cd Huginn
uv sync --locked --extra dev

# Development-only Playwright compatibility backend
uv run playwright install chromium
```

The production suite uses the repository Docker Compose service plus the
authenticated StarSearch daemon. Its image build consumes `uv.lock` with
`--locked`, so dependency drift fails the build. Follow the complete install order in
`Blackreach/docs/WEB_TOOL_SUITE.md`; installing this package alone does not
create a proxy network or a browser pool.

## Environment

The checked-in [`.env.example`](.env.example) is authoritative. Important
production settings are:

| Variable | Production value/purpose |
|---|---|
| `HUGINN_HOST` | `127.0.0.1`; opt into wider exposure deliberately |
| `HUGINN_API_KEY_FILE` | Bearer key file; preferred over a direct secret value |
| `HUGINN_DATA_DIR` | Persistent state root; Compose uses `/data` |
| `HUGINN_BROWSER_BACKEND` | `starsearch` |
| `HUGINN_STARSEARCH_TCP` | Authenticated daemon address, normally `127.0.0.1:7676` |
| `HUGINN_STARSEARCH_TOKEN_FILE` | StarSearch TCP bearer-token file |
| `HUGINN_ALLOW_PLAYWRIGHT_FALLBACK` | `false` in production |
| `HUGINN_PROXY_PROVIDER` | `direct` or `static`; direct means host egress |
| `HUGINN_PROXY_URLS_FILE` | Secret file for configured proxy endpoints |
| `HUGINN_PROXY_ROTATION` | `round_robin` or `sticky` |
| `HUGINN_DB_PATH` | Explicit SQLite path, otherwise derived from `HUGINN_DATA_DIR` |

---

## License

MIT — see [LICENSE](LICENSE)

---

Built with ❤️ by [Phnix](https://github.com/Null-Phnix)
