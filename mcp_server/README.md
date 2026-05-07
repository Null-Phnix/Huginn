# Huginn MCP Server

A Model Context Protocol (MCP) server that exposes Huginn web intelligence tools to any MCP client (e.g., Claude Desktop, Cursor, Continue).

## Tools

| Tool | Description |
|------|-------------|
| **probe** | Lightweight URL inspection — returns page structure and metadata without full crawling |
| **sweep** | Deep crawl — submits a crawl job, polls until done, returns full crawled data (all pages, content, errors) |
| **chart** | Generate a chart (bar, line, pie, scatter, etc.) from structured data; returns a base64 PNG data URI |
| **seek** | Lightweight web search — returns ranked results with snippets |
| **distill** | Structured extraction — submits a JSON Schema + URL, polls until done, returns schema-conforming JSON |

## Setup

### 1. Install the package

```bash
# Create a virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e .

# Or with pipx
pipx install -e /mnt/GameDrive/AI_Projects/BlackCrawl/mcp_server
```

### 2. Configure environment variables

```bash
# Required
export HUGINN_API_KEY="your-api-key"

# Optional (defaults shown)
export HUGINN_BASE_URL="http://localhost:7432"
export HUGINN_POLL_INTERVAL="2"   # seconds between job status polls
export HUGINN_POLL_TIMEOUT="300"   # max seconds to wait for a job
```

Or create a `.env` file in the `mcp_server/` directory:

```env
HUGINN_API_KEY=your-api-key
HUGINN_BASE_URL=http://localhost:7432
HUGINN_POLL_INTERVAL=2
HUGINN_POLL_TIMEOUT=300
```

### 3. Configure your MCP client

#### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "huginn": {
      "command": "/path/to/.venv/bin/python",
      "args": ["/mnt/GameDrive/AI_Projects/BlackCrawl/mcp_server/server.py"],
      "env": {
        "HUGINN_API_KEY": "your-api-key",
        "HUGINN_BASE_URL": "http://localhost:7432"
      }
    }
  }
}
```

#### Continue (VS Code / JetBrains)

Add to your `~/.continue/config.py`:

```python
from continue.server.main import mcp_server

tools = [
    mcp_server(
        "huginn",
        "/mnt/GameDrive/AI_Projects/BlackCrawl/mcp_server/server.py",
        env={
            "HUGINN_API_KEY": "your-api-key",
            "HUGINN_BASE_URL": "http://localhost:7432",
        }
    )
]
```

#### Cursor

Add to Cursor settings (JSON):

```json
{
  "mcp.servers": {
    "huginn": {
      "command": "/path/to/.venv/bin/python",
      "args": ["/mnt/GameDrive/AI_Projects/BlackCrawl/mcp_server/server.py"],
      "env": {
        "HUGINN_API_KEY": "your-api-key",
        "HUGINN_BASE_URL": "http://localhost:7432"
      }
    }
  }
}
```

### 4. Start the Huginn API server

The Huginn API server must be running at `HUGINN_BASE_URL` before using this MCP server:

```bash
python /mnt/GameDrive/AI_Projects/BlackCrawl/mcp_server/blackreach_http_server.py
```

### 5. Verify

Restart your MCP client and ask Claude/Cursor to use a Huginn tool:

```
Use the 'probe' tool to inspect https://example.com
```

## Development

```bash
# Run directly (stdio transport)
python server.py

# Test syntax
python -m py_compile server.py

# Run with debug logging
FASTMCP_DEBUG=true python server.py
```

## API Endpoints Used

This MCP server connects to the following Huginn API endpoints:

| Endpoint | Method | Tool | Async? |
|----------|--------|------|--------|
| `/probe` | POST | probe | No |
| `/browse` | POST | sweep | Yes (polls `/jobs/{id}`) |
| `/search` | POST | seek | No |
| `/chart` | POST | chart | No |
| `/scrape-jobs` | POST | distill | Yes (polls `/jobs/{id}`) |
| `/jobs/{id}` | GET | sweep, distill | Polls for async job status |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HUGINN_BASE_URL` | `http://localhost:7432` | Huginn API base URL |
| `HUGINN_API_KEY` | _(none)_ | Bearer token for authentication |
| `HUGINN_POLL_INTERVAL` | `2` | Seconds between job status polls |
| `HUGINN_POLL_TIMEOUT` | `300` | Max seconds to wait for async job completion |
