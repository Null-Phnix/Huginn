# Start Here — Huginn

Huginn is the deterministic data plane in the local Blackreach web-tool
suite. It owns search, scrape, crawl, extraction, batch work, caching, durable
jobs, and authenticated browser-session policy. StarSearch owns browser
execution; Blackreach owns goal-driven browsing; `blackreach-mcp` is the one
adapter agents should register.

## Production start

Install StarSearch and the local secret files first, following the complete
[`WEB_TOOL_SUITE.md`](https://github.com/Null-Phnix/Blackreach/blob/main/docs/WEB_TOOL_SUITE.md)
runbook. Then:

```bash
cd /mnt/WorkDrive/AI_Projects/BlackCrawl
docker compose up -d --build
curl --fail http://127.0.0.1:7432/health/ready
```

The production Compose profile binds loopback, requires bearer auth for
data-bearing routes, persists `/data/huginn.db`, selects StarSearch, and fails
closed instead of silently launching Playwright.

## Development

```bash
cd /mnt/WorkDrive/AI_Projects/BlackCrawl
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

Use Playwright only when deliberately testing the compatibility backend. It
is not the production browser path and it does not provide proxy egress.

## Agent access

Do not register `mcp_server/server.py` beside the suite adapter. Build and
register only:

```text
/mnt/WorkDrive/AI_Projects/blackreach-mcp/dist/index.js
```

That keeps MCP schemas, job IDs, errors, screenshots, sessions, and agent
browsing consistent across Hermes, Claude, and Codex.
