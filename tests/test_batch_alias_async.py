"""Firecrawl-style batch alias job lifecycle tests."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from huginn.api import create_app
from huginn.config import HuginnConfig
from huginn.state import get_state, reset_state


@pytest.mark.asyncio
async def test_batch_alias_submits_async_job_and_returns_canonical_id(monkeypatch):
    reset_state()
    state = get_state()
    state.job_store = AsyncMock()
    state.job_store.create_job.return_value = "batch-123"
    state.browser = MagicMock()

    completed = AsyncMock()
    monkeypatch.setattr("huginn.tasks.run_flock", completed)
    app = create_app(HuginnConfig())

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/batch/scrape",
            json={"urls": ["https://example.com"]},
        )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "id": "batch-123",
        "url": "/v1/batch/scrape/batch-123",
    }
    state.job_store.create_job.assert_awaited_once()
    reset_state()


@pytest.mark.asyncio
async def test_batch_alias_status_returns_results(monkeypatch):
    reset_state()
    state = get_state()
    state.job_store = AsyncMock()
    state.job_store.get_job.return_value = {
        "status": "completed",
        "completed": 1,
        "total": 1,
        "result_json": json.dumps({
            "results": [{"url": "https://example.com", "success": True}],
            "partial": False,
        }),
        "error": None,
    }
    state.browser = MagicMock()
    app = create_app(HuginnConfig())

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/batch/scrape/batch-123")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "batch-123"
    assert payload["data"][0]["success"] is True
    reset_state()
