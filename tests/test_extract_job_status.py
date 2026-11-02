"""
Extract jobs must report hard failures as failed, with the real reason.

Regression for blackreach-mcp's ``blackreach_extract``: with no LLM key (or
every scrape failing) the job used to finish as ``status=completed`` with
``error=None``, so MCP callers saw success, no data, and no explanation. The
actual cause only reached the server log.
"""

from unittest.mock import AsyncMock

import pytest

from huginn import tasks
from huginn.config import HuginnConfig
from huginn.extractor import Extractor
from huginn.job_store import JobStore
from huginn.models import DistillRequest, JobStatus, ScrapeData
from huginn.routers.extract import _do_get_distill_status
from huginn.state import get_state, reset_state

MISSING_KEY = "LLM provider 'openai' requires OPENAI_API_KEY environment variable."


def _extractor_with_page() -> Extractor:
    extractor = Extractor(browser=None, llm_provider="openai", max_retries=2, mental_model=False)
    extractor.scraper = AsyncMock()
    extractor.scraper.scrape = AsyncMock(
        return_value=ScrapeData(markdown="# Widget\nPrice: $9.99", metadata={"title": "Widget"})
    )
    return extractor


class TestExtractorSurfacesErrors:
    async def test_every_llm_call_raising_reports_last_error(self):
        extractor = _extractor_with_page()
        extractor._call_llm = AsyncMock(side_effect=RuntimeError(MISSING_KEY))

        result = await extractor.extract(urls=["https://shop.example/w"], prompt="title")

        assert result["success"] is False
        assert "All 2 extraction attempts failed" in result["error"]
        assert MISSING_KEY in result["error"]

    async def test_llm_answer_with_low_confidence_is_not_an_error(self):
        extractor = _extractor_with_page()
        extractor._call_llm = AsyncMock(return_value=({"title": "Widget"}, '{"title": "Widget"}'))
        schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}

        result = await extractor.extract(urls=["https://shop.example/w"], schema=schema)

        assert "error" not in result
        assert result["attempts"] >= 1

    async def test_scrape_failure_reason_is_kept_per_url(self):
        extractor = Extractor(browser=None, llm_provider="openai")
        extractor.scraper = AsyncMock()
        extractor.scraper.scrape = AsyncMock(side_effect=RuntimeError("HTTP 403 bot wall"))

        result = await extractor.extract(urls=["https://shop.example/w"], prompt="title")

        assert result["success"] is False
        assert result["sources"] == [{"url": "https://shop.example/w", "error": "HTTP 403 bot wall"}]


@pytest.fixture
async def job_state(tmp_path, monkeypatch):
    reset_state()
    state = get_state()
    state.config = HuginnConfig()
    state.browser = object()
    state.job_store = JobStore(str(tmp_path / "jobs.db"))
    await state.job_store.init()
    yield state, monkeypatch
    await state.job_store.close()
    reset_state()


def _patch_extract(monkeypatch, result):
    class FakeExtractor:
        def __init__(self, *args, **kwargs):
            pass

        async def extract(self, **kwargs):
            return result

    monkeypatch.setattr(tasks, "Extractor", FakeExtractor)


async def _run(state, req: DistillRequest):
    job_id = await state.job_store.create_job("extract", req.model_dump(exclude_none=True))
    await tasks.run_distill(job_id, req)
    return await _do_get_distill_status(job_id)


class TestExtractJobStatus:
    async def test_llm_failure_marks_job_failed_with_reason(self, job_state):
        state, monkeypatch = job_state
        _patch_extract(monkeypatch, {
            "data": {"error": "All extraction attempts failed"},
            "confidence": 0.0,
            "attempts": 3,
            "sources": [{"url": "https://shop.example/w", "title": "Widget", "length": 21}],
            "success": False,
            "error": f"All 3 extraction attempts failed. Last error: {MISSING_KEY}",
        })

        status = await _run(state, DistillRequest(urls=["https://shop.example/w"], prompt="title"))

        assert status.status == JobStatus.FAILED
        assert MISSING_KEY in status.error

    async def test_nothing_scraped_marks_job_failed(self, job_state):
        state, monkeypatch = job_state
        _patch_extract(monkeypatch, {
            "error": "No content could be extracted from any URL",
            "success": False,
            "sources": [{"url": "https://shop.example/w", "error": "HTTP 403 bot wall"}],
        })

        status = await _run(state, DistillRequest(urls=["https://shop.example/w"], prompt="title"))

        assert status.status == JobStatus.FAILED
        assert status.error == "No content could be extracted from any URL"

    async def test_low_confidence_answer_stays_completed(self, job_state):
        state, monkeypatch = job_state
        result = {
            "data": {"title": "Widget"},
            "confidence": 0.25,
            "attempts": 3,
            "sources": [],
            "success": False,
            "validation_errors": ["Missing required field: 'product_name'"],
        }
        _patch_extract(monkeypatch, result)

        status = await _run(state, DistillRequest(urls=["https://shop.example/w"], template="product"))

        assert status.status == JobStatus.COMPLETED
        assert status.data == result
        assert status.error is None

    async def test_success_stays_completed(self, job_state):
        state, monkeypatch = job_state
        _patch_extract(monkeypatch, {"data": {"title": "Widget"}, "confidence": 0.9, "success": True})

        status = await _run(state, DistillRequest(urls=["https://shop.example/w"], prompt="title"))

        assert status.status == JobStatus.COMPLETED
        assert status.error is None
