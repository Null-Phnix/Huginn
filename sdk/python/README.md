# Huginn Python SDK

Async Python client for the [Huginn](https://github.com/Null-Phnix/Huginn) autonomous web scraping API.

```bash
pip install huginn-client
```

## Quick Start

```python
import asyncio
from huginn_client import HuginnClient

async def main():
    async with HuginnClient() as client:
        result = await client.probe("https://example.com")
        print(result.data.markdown)

asyncio.run(main())
```

## Authentication

Set the API key via environment variable or constructor:

```python
# Via environment
# HUGINN_API_KEY=your-key

# Via constructor
client = HuginnClient(api_key="your-key")
```

## Endpoints

### probe(url) — Scrape a single URL

```python
result = await client.probe(
    "https://example.com",
    formats=["markdown", "html"],
    only_main_content=True,
    scroll=True,
)
print(result.data.markdown)
```

### sweep_start(url) — Start an async crawl

```python
job = await client.sweep_start(
    "https://docs.python.org",
    max_depth=2,
    limit=50,
)
print(f"Crawl started: {job.id}")

# Poll for completion
while True:
    status = await client.sweep_status(job.id)
    print(f"{status.completed}/{status.total} pages")
    if status.status == "completed":
        for page in status.data:
            print(page.markdown[:200])
        break
    await asyncio.sleep(1)
```

### sweep_stream(url) — Crawl with SSE streaming

```python
async for event in client.sweep_stream("https://docs.python.org", max_depth=2):
    if event["type"] == "document":
        print(event["data"].get("markdown", "")[:100])
    elif event["type"] == "done":
        print("Crawl complete:", event["data"])
```

### chart(url) — Map all URLs on a site

```python
result = await client.chart("https://example.com", search="api", limit=100)
for link in result.links:
    print(link)
```

### seek(query) — Web search with scraping

```python
results = await client.seek(
    "Python async tutorial",
    search_options={"limit": 5},
    fallback_chain=True,
)
for item in results.data:
    print(item.metadata.get("title"))
```

### distill(urls, prompt, schema) — Structured extraction

Pass a Pydantic model for typed results:

```python
from pydantic import BaseModel
from huginn_client import HuginnClient

class Article(BaseModel):
    title: str
    paragraphs: list[str]

async with HuginnClient() as client:
    result = await client.distill(
        urls=["https://news.example.com"],
        prompt="Extract the headline and all paragraphs",
        schema=Article,
    )
    article = result.data  # Article(title=..., paragraphs=[...])
```

Or pass a raw JSON schema dict:

```python
result = await client.distill(
    urls=["https://news.example.com"],
    prompt="Extract titles",
    schema={"type": "object", "properties": {"titles": {"type": "array", "items": {"type": "string"}}}}
)
```

### distill_stream(urls, prompt, schema) — SSE extraction

```python
async for event in client.distill_stream(
    urls=["https://news.example.com"],
    prompt="Extract titles",
    schema=Article,
):
    if event["type"] == "done":
        print(event["data"])  # Structured result
```

### flock(urls) — Batch scrape multiple URLs

```python
result = await client.flock([
    "https://example.com",
    "https://example.org",
])
for item in result.data:
    print(f"{item.url}: {'OK' if item.success else item.error}")
```

## Error Handling

```python
from huginn_client import HuginnClient, HuginnError, JobNotFoundError

async with HuginnClient() as client:
    try:
        result = await client.probe("https://example.com")
    except HuginnError as e:
        print(f"API error {e.status_code}: {e.detail}")
```

## Configuration

| Option | Env Var | Default |
|--------|---------|---------|
| base_url | — | http://localhost:7432 |
| api_key | HUGINN_API_KEY | (none) |
| timeout | — | 60s |

## License

MIT
