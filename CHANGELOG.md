# Changelog

## [1.2.0] - 2026-05-06

### Added
- **REST API endpoints for templates**: `GET /v1/templates` lists all 10 extraction templates with schemas, `GET /v1/templates/{name}` gets a single template's full details.
- **REST API endpoints for research memory**: `GET /v1/memory/query` (semantic search), `GET /v1/memory/reports` (list all reports), `DELETE /v1/memory/reports/{id}` (delete report), `GET /v1/memory/related` (find related topics).
- **Full interactive CLI rebuilt with Click**:
  - `huginn` (no args) → interactive REPL menu with ASCII banner
  - Commands: `scrape`, `extract`, `crawl`, `search`, `map`, `research`, `serve`, `templates`, `config`, `doctor`
  - `doctor` now launches Chromium to verify Playwright is truly working, not just importable.
  - `--version` flag works.
- **Click-based subcommands** with full `--help` for every command and consistent option style (`--port`, `--depth`, `--limit`, etc.).
- **New CLI tests** (`tests/test_cli.py`) using `click.testing.CliRunner` — 4 tests covering help, templates, doctor, config.

### Fixed
- `_config.server.data_dir` typo in `/v1/research` and memory APIs → now uses correct path.
- Template/memory API endpoints were accidentally defined at module level (outside `create_app`); they now live inside the factory function and register correctly.
- Duplicate `get_template_api` definition removed, bad indentation on dict lines fixed.
- `huginn/cli.py` fully rewritten (640 lines) replacing the old basic argparse script.

### Changed
- `pyproject.toml` now formally requires `click` (already present via transitive deps, now explicit).
- `prompt-toolkit` added for future interactive enhancements.

## [1.1.0] - Unreleased

### Added
- **Extractor Templates** (`huginn/templates.py`) — 10 battle-tested extraction schemas:
  `product`, `article`, `job_posting`, `real_estate`, `person`, `event`, `review`, `faq`, `recipe`, `research_paper`
  Each template includes a JSON schema, field guides (hints for the LLM on where to find data), system prompt, and merge strategy.
- **Template API integration** — `POST /v1/distill` now accepts a `template` field (e.g. `"template": "product"`) that auto-populates schema, system prompt, and field guides.
- **JSON repair pipeline** (`extractor.py`) — 6-step progressive fallback for when LLMs return malformed JSON:
  1. Direct parse after stripping markdown wrappers
  2. Outermost object via bracket matching
  3. Regex object extraction
  4. Array extraction and wrapping
  5. Automatic repair (trailing commas, unescaped newlines, comments)
  6. Graceful degradation to `{"raw_response": ...}` with parse flag
- **Schema-guided retry** (`extractor.py`) — each failed attempt gets context about what fields failed validation, improving extraction success rate.
- **Field-level validation reporting** — `_validate_schema` now reports per-field type errors and nested required-field violations, not just top-level.
- **Research Memory** (`huginn/memory.py`) — persistent ChromaDB vector store for accumulated research knowledge:
  - Semantic search over findings, citations, and page snippets
  - Chunking with overlap for long pages
  - Filter by type (`finding`, `citation`, `snippet`, `report_summary`) and report ID
  - `ResearchMemory.query()`, `get_related_topics()`, `get_all_reports()`, `delete_report()`
- **DeepResearcher persistence** — Research reports are automatically persisted to vector memory after synthesis.
- **ResearchMemory integration in API** — `/v1/research` endpoint auto-initializes `ResearchMemory` if `HUGINN_DATA_DIR` is configured.
- **defusedxml** added to dependencies for XML bomb protection.
- **chromadb** added to dependencies for vector storage.

### Changed
- **Stealth claims corrected** — README and `__init__.py` docstrings no longer claim StarSearch Rust daemon with "15 JS modules, 80+ fingerprints." Stealth is now accurately described as Playwright + patches (webdriver removal, plugin spoofing, viewport normalization).
- **`_build_prompt`** signature updated to include `output_format` parameter; `text` format prompt now correctly asks for plain text instead of JSON.
- **`_update_beliefs`** now takes an `errors` parameter to track validation failures.
- **`pyproject.toml`** dependencies updated to include `chromadb` and `defusedxml`.

### Fixed
- **Syntax error** in `extractor.py` Google call (unterminated f-string literal on Gemini API URL).
- **Missing dependency** `cryptography` was discovered manually installed but not in pyproject.toml (already present in Blackreach, added here for shared code path).
- **Extractor test updates** — `test_extractor.py` updated to pass `output_format` and `errors` params matching new signatures. All 35 extractor tests pass.
- **Template tests** (`tests/test_templates.py`) — 7 tests covering all 10 built-in templates.
- **Memory tests** (`tests/test_memory.py`) — 6 tests covering initialization and chunking (with a bugfix for edge-case chunking loop on short text).

## [1.0.0] — Initial Release

- Firecrawl-compatible API: `/v1/scrape`, `/v1/crawl`, `/v1/map`, `/v1/extract`, `/v1/search`, `/v1/research`, `/v1/watch`, `/v1/flock`
- Playwright-based browser with stealth patches
- DOM walker with semantic role extraction
- Autonomous Deep Researcher with multi-hop investigation
- Page change detection monitoring
- Async job queue with SQLite backend
- LLM extraction via OpenAI, Anthropic, Google, Ollama
- Rate limiting, circuit breakers, robots.txt support
- Docker one-liner deployment

## [1.2.1] - 2026-05-08

### Fixed
- **Interactive REPL command dispatch**: full command names (`scrape`, `config`, etc.) are now recognised, not only single-letter keys (`s`, `o`)
- **Case-sensitive dispatch bug**: capital `M` (`memory`) and `W` (`watch`) collided with `.lower()` converting to `m`/`w`, causing those commands to silently fail; now checks uppercase identity for `M`/`W`
- Unknown commands print an error instead of silently ignoring input

### Added
- Regression tests for interactive-mode dispatch (`map`, `memory`)
