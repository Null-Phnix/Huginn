# Legacy Python MCP compatibility adapter

This directory is retained for compatibility tests and older clients that use
the six native Huginn tool names (`probe`, `sweep`, `chart`, `seek`, `distill`,
and `flock`). It is not the production agent surface.

Register only the Node [`blackreach-mcp`](https://github.com/Null-Phnix/blackreach-mcp)
adapter in Hermes, Claude, Codex, or another MCP host. That adapter exposes the
stable suite schemas, durable job IDs, screenshots, authenticated browser
sessions, and goal-driven Blackreach jobs while keeping Huginn as the single
deterministic data plane.

Do not register this server beside `blackreach-mcp`: doing so creates duplicate
tool surfaces with different timeout and error semantics. The copied
`blackreach_http_server.py` runtime has been deliberately retired because it
used machine-specific paths, conflicted with Huginn's port, and bypassed the
current service boundary.

For the supported architecture, configuration, and runbook, see
[`Blackreach/docs/WEB_TOOL_SUITE.md`](https://github.com/Null-Phnix/Blackreach/blob/main/docs/WEB_TOOL_SUITE.md).

Compatibility development checks can still run with:

```bash
python -m pytest tests/test_mcp_server.py
python mcp_server/server.py
```

`server.py` reads the Huginn bearer key from `HUGINN_API_KEY_FILE` (default
`~/.config/huginn/api-key`) and talks only to the loopback Huginn API.
