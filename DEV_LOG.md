# BlackCrawl/Huginn Development Log

First-person notes on bugs found, fixes applied, architectural decisions, and dead-ends.

---

## 2026-05-07 — Session: CLI v1.3 + `_do_scrape()` signature fix

### The `_do_scrape()` signature mismatch

After adding `--out-format` (`-F`) globally to all CLI commands, `scrape_cmd` called `_do_scrape(url, fmt, output, outfmt)` with 4 arguments, but `_do_scrape()` only accepted 3 (`url`, `fmt`, `out`).

**Why the unit tests didn't catch it:** `tests/test_cli.py` only tests Click help text using `CliRunner.invoke(..., ['--help'])`. It never exercises the async internals. The bug was only caught when I actually ran:

```bash
huginn scrape file:///tmp/test_page.html
```

Which threw `TypeError: _do_scrape() takes 3 positional arguments but 4 were given`.

**Fix:** Added `outfmt: str = "json"` to `_do_scrape()`, replaced inline json dumping with `_write_output()` (the centralized serializer that every command now uses), and cleaned up `_i_scrape()` interactive flow that had gotten accidentally duplicated during the v1.2→v1.3 rewrite.

### Why I kept `_write_output()` as a single function

Every command (`scrape`, `extract`, `batch`, `memory`, `watch`, `jobs`, `research`, `map`, `config`, `doctor`) now supports `--out-format`. Centralizing serialization in one 20-line function means I only fix format bugs once. The alternative — inlining json/yaml/csv/markdown logic in every command — would be 15× the code.

### Network test exclusion

`tests/test_integration.py` has 6 tests that hit real URLs (`https://example.com`). They fail offline with `ERR_NAME_NOT_RESOLVED`. Rather than mocking them (which makes them not-integration-tests anymore), I excluded `@pytest.mark.network` from the default pytest run:

```toml
[tool.pytest.ini_options]
addopts = "-v --tb=short -m 'not network'"
```

Run with `pytest -m network` to include them. This is the standard pattern for mixed offline/online test suites.

### Why the CLI is a single file

`cli.py` is 1,066 lines. I considered splitting into `cli_scrape.py`, `cli_memory.py`, etc. But Click groups share state (`console`, `_run_async()`, `_get_browser()`) and the user explicitly said "make it feel like Blackreach" — which has a monolithic CLI. Single file is the right call until it hits 1,500+ lines.

---

## 2026-05-06 — Session: template + memory API endpoints

### The FastAPI factory gotcha

I added `GET /v1/templates`, `GET /v1/templates/{name}`, `GET /v1/memory/query`, etc. **I initially wrote them at module level**, outside `create_app()`:

```python
@app.get("/v1/templates")  # WRONG — 'app' doesn't exist here
def get_templates(): ...
```

Since Huginn uses an app factory (`create_app()` returns a new FastAPI instance), module-level decorators silently bind to nothing. The routes never registered.

**Fix:** Moved all routes inside `create_app()`. Verified with `TestClient`.

### Why memory endpoints use ChromaDB directly

The memory endpoints (`/v1/memory/query`, `/v1/memory/reports`) call `ResearchMemory` (ChromaDB + sentence-transformers) directly rather than going through the scraper/researcher pipeline. This is intentional: memory is a separate subsystem. The researcher *writes* to memory; the API *reads* from it. Keeping them decoupled means memory works even if the researcher is broken.

### `_config.server.data_dir` typo

In `api.py`, some endpoints referenced `_config.server.data_dir`. `HuginnConfig` doesn't have a `.server` attribute — it's `config.data_dir`. This was a copy-paste error from Blackreach's config structure. Fixed in the same commit.

---

## 2026-05-05 — Session: batch CLI + progress bars

### Why Rich `Progress` instead of spinners

The user specifically asked for "real progress bar" for batch operations. Rich's `Progress` class gives per-item progress with `SpinnerColumn()` + `TextColumn()` — it shows "3/50" style progress for batch jobs. Spinners (like `yaspin`) don't show completion count. For single-item operations (`scrape`, `extract`), a spinner is fine. For batch, the user wanted real progress.

### The `_do_batch()` async generator pattern

Batch reads URLs from a file or stdin, scrapes each, and outputs aggregated results. I use an async generator internally:

```python
async def _do_batch(urls, fmt, out, outfmt):
    results = []
    with Progress(...) as p:
        task = p.add_task("Batch scrape", total=len(urls))
        for url in urls:
            data = await scraper.scrape(url, ...)
            results.append(data.model_dump())
            p.advance(task)
    _write_output({"results": results}, out, outfmt)
```

This keeps memory bounded — results are accumulated in a list, but the scraper releases the browser page between URLs. For 1,000 URLs, this would still hold all results in memory. If the user hits that scale, streaming JSON lines (`jsonl`) would be better.

---

## 2026-05-04 — Session: interactive REPL menu

### Why I used `prompt-toolkit` for the REPL

The user wanted "if I type Huginn in terminal it pops up like Blackreach." Blackreach's CLI has an interactive mode with a banner and menu. `prompt-toolkit` gives line editing, history, and key bindings that raw `input()` doesn't. But `prompt-toolkit` is a heavy dependency. The user approved it — it's in the `.venv`.

### Menu keyboard shortcuts

The menu shows `[s] scrape`, `[e] extract`, etc. The letter is the first character of the command name for everything *except* `memory` (`[M]` capitalized to avoid conflict with `map` `[m]`) and `watch` (`[W]` capitalized to avoid conflict with... nothing, actually, but consistency with `M`). This was a design decision, not a technical one. The user hasn't complained.

---

## 2026-05-03 — Session: initial Click CLI setup

### Why I migrated from argparse to Click

The original `huginn_cli.py` (1,563 lines, still in the repo) used argparse. It was a flat script with no subcommands. Click enables:

1. Nested groups: `huginn memory query`, `huginn watch add`
2. Built-in help styling
3. Shell completion generation
4. Type-safe option parsing

The user explicitly said "like Blackreach" — Blackreach uses Click. Migration was straightforward: argparse `add_argument` → Click `@click.option`.

---

*Last updated: 2026-05-07 by agent*
