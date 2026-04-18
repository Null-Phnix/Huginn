"""
Tests for Huginn Job Store.
"""

import asyncio
import json
import os
import tempfile
from datetime import datetime, timedelta

import pytest

from huginn.job_store import JobStore


@pytest.fixture
async def job_store(tmp_path):
    """Create a JobStore with a temporary database."""
    db_path = str(tmp_path / "test.db")
    store = JobStore(db_path)
    await store.init()
    yield store
    await store.close()


class TestJobStore:
    """Test JobStore CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_job(self, job_store):
        """Should create a job and return its ID."""
        job_id = await job_store.create_job("crawl", {"url": "https://example.com"})
        assert job_id is not None
        assert len(job_id) == 36  # UUID format

    @pytest.mark.asyncio
    async def test_get_job(self, job_store):
        """Should retrieve a created job."""
        request = {"url": "https://example.com", "depth": 3}
        job_id = await job_store.create_job("crawl", request)
        job = await job_store.get_job(job_id)

        assert job is not None
        assert job["id"] == job_id
        assert job["type"] == "crawl"
        assert job["status"] == "pending"
        assert json.loads(job["request_json"]) == request

    @pytest.mark.asyncio
    async def test_get_nonexistent_job(self, job_store):
        """Should return None for nonexistent job."""
        job = await job_store.get_job("nonexistent-id")
        assert job is None

    @pytest.mark.asyncio
    async def test_update_job_status(self, job_store):
        """Should update job status."""
        job_id = await job_store.create_job("crawl", {"url": "https://example.com"})
        await job_store.update_job(job_id, status="running")
        job = await job_store.get_job(job_id)
        assert job["status"] == "running"

    @pytest.mark.asyncio
    async def test_update_job_result(self, job_store):
        """Should update job result data."""
        job_id = await job_store.create_job("extract", {"urls": ["https://example.com"]})
        result = {"data": {"title": "Example"}, "confidence": 0.9}
        await job_store.update_job(job_id, status="completed", job_result=result)
        job = await job_store.get_job(job_id)

        assert job["status"] == "completed"
        assert json.loads(job["result_json"]) == result

    @pytest.mark.asyncio
    async def test_update_job_error(self, job_store):
        """Should update job error."""
        job_id = await job_store.create_job("crawl", {"url": "https://example.com"})
        await job_store.update_job(job_id, status="failed", error="Timeout")
        job = await job_store.get_job(job_id)

        assert job["status"] == "failed"
        assert job["error"] == "Timeout"

    @pytest.mark.asyncio
    async def test_update_progress(self, job_store):
        """Should update completed/total counts."""
        job_id = await job_store.create_job("crawl", {"url": "https://example.com"})
        await job_store.update_job(job_id, status="running", completed=5, total=20)
        job = await job_store.get_job(job_id)
        assert job["completed"] == 5
        assert job["total"] == 20

    @pytest.mark.asyncio
    async def test_list_jobs(self, job_store):
        """Should list jobs."""
        id1 = await job_store.create_job("crawl", {"url": "https://a.com"})
        id2 = await job_store.create_job("extract", {"urls": ["https://b.com"]})
        id3 = await job_store.create_job("crawl", {"url": "https://c.com"})

        # Update one to completed
        await job_store.update_job(id1, status="completed")

        all_jobs = await job_store.list_jobs()
        assert len(all_jobs) == 3

        completed_jobs = await job_store.list_jobs(status="completed")
        assert len(completed_jobs) == 1
        assert completed_jobs[0]["id"] == id1

        pending_jobs = await job_store.list_jobs(status="pending")
        assert len(pending_jobs) == 2

    @pytest.mark.asyncio
    async def test_delete_job(self, job_store):
        """Should delete a job."""
        job_id = await job_store.create_job("crawl", {"url": "https://example.com"})
        deleted = await job_store.delete_job(job_id)
        assert deleted is True

        job = await job_store.get_job(job_id)
        assert job is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_job(self, job_store):
        """Should return False for deleting nonexistent job."""
        deleted = await job_store.delete_job("nonexistent")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_cleanup_expired(self, job_store):
        """Should clean up expired jobs."""
        # Create a job with short TTL
        job_id = await job_store.create_job("crawl", {"url": "https://example.com"}, ttl=0)

        # Expire it immediately
        await job_store._db.execute(
            "UPDATE jobs SET expires_at = ? WHERE id = ?",
            ((datetime.utcnow() - timedelta(hours=1)).isoformat(), job_id)
        )
        await job_store._db.commit()

        deleted = await job_store.cleanup_expired()
        assert deleted >= 1

        job = await job_store.get_job(job_id)
        assert job is None

    @pytest.mark.asyncio
    async def test_multiple_job_types(self, job_store):
        """Should handle different job types."""
        crawl_id = await job_store.create_job("crawl", {"url": "https://a.com"})
        extract_id = await job_store.create_job("extract", {"urls": ["https://b.com"]})

        crawl_job = await job_store.get_job(crawl_id)
        extract_job = await job_store.get_job(extract_id)

        assert crawl_job["type"] == "crawl"
        assert extract_job["type"] == "extract"