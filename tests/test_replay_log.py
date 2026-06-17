"""Tests for the ReplayLog SQLite-backed scrape audit trail."""

import json
import pytest
import pytest_asyncio

from huginn.replay_log import ReplayLog


@pytest_asyncio.fixture
async def replay_log(temp_db):
    """Create and initialize a ReplayLog instance."""
    rl = ReplayLog(temp_db)
    await rl.init()
    yield rl
    await rl.close()


class TestReplayLogInit:
    """Tests for ReplayLog initialization and schema."""

    async def test_init_creates_table(self, temp_db):
        rl = ReplayLog(temp_db)
        await rl.init()
        # Table should exist — verify by inserting a row
        log_id = await rl.log_scrape(url="https://example.com", method="scrape")
        assert log_id is not None
        await rl.close()

    async def test_reopen_existing_db(self, temp_db):
        """Opening the same DB twice should not fail (IF NOT EXISTS)."""
        rl1 = ReplayLog(temp_db)
        await rl1.init()
        await rl1.log_scrape(url="https://example.com")
        await rl1.close()

        rl2 = ReplayLog(temp_db)
        await rl2.init()
        entries = await rl2.list_replay_logs()
        assert len(entries) == 1
        await rl2.close()


class TestLogScrape:
    """Tests for log_scrape()."""

    async def test_basic_log_entry(self, replay_log):
        log_id = await replay_log.log_scrape(
            url="https://example.com",
            method="scrape",
            status="success",
            duration_ms=150,
        )
        assert log_id is not None

        entry = await replay_log.get_replay_log(log_id)
        assert entry is not None
        assert entry["url"] == "https://example.com"
        assert entry["method"] == "scrape"
        assert entry["status"] == "success"
        assert entry["duration_ms"] == 150
        assert entry["error"] is None

    async def test_log_with_error(self, replay_log):
        log_id = await replay_log.log_scrape(
            url="https://broken.com",
            method="scrape",
            status="error",
            error="Connection refused",
            duration_ms=50,
        )
        entry = await replay_log.get_replay_log(log_id)
        assert entry["status"] == "error"
        assert entry["error"] == "Connection refused"

    async def test_log_with_request_and_response(self, replay_log):
        req = {"url": "https://example.com", "formats": ["markdown", "html"]}
        resp = {"title": "Example", "markdown_length": 1234}
        log_id = await replay_log.log_scrape(
            url="https://example.com",
            request=req,
            response_summary=resp,
        )
        entry = await replay_log.get_replay_log(log_id)
        assert json.loads(entry["request_json"]) == req
        assert json.loads(entry["response_summary"]) == resp

    async def test_log_truncates_long_error(self, replay_log):
        long_error = "x" * 10000
        log_id = await replay_log.log_scrape(
            url="https://example.com",
            error=long_error,
        )
        entry = await replay_log.get_replay_log(log_id)
        assert len(entry["error"]) < 5000  # truncated

    async def test_log_truncates_long_response(self, replay_log):
        big_response = {"data": "x" * 10000}
        log_id = await replay_log.log_scrape(
            url="https://example.com",
            response_summary=big_response,
        )
        entry = await replay_log.get_replay_log(log_id)
        summary = entry["response_summary"]
        assert len(summary) < 5000
        assert summary.endswith("…")

    async def test_log_with_http_status(self, replay_log):
        log_id = await replay_log.log_scrape(
            url="https://example.com",
            http_status=200,
        )
        entry = await replay_log.get_replay_log(log_id)
        assert entry["http_status"] == 200

    async def test_log_minimal_fields(self, replay_log):
        """Only url is required."""
        log_id = await replay_log.log_scrape(url="https://minimal.com")
        entry = await replay_log.get_replay_log(log_id)
        assert entry["url"] == "https://minimal.com"
        assert entry["method"] == "scrape"  # default
        assert entry["status"] == "success"  # default


class TestListReplayLogs:
    """Tests for list_replay_logs()."""

    async def test_list_all(self, replay_log):
        for i in range(5):
            await replay_log.log_scrape(url=f"https://example{i}.com")
        entries = await replay_log.list_replay_logs()
        assert len(entries) == 5

    async def test_list_filter_by_url(self, replay_log):
        await replay_log.log_scrape(url="https://a.com")
        await replay_log.log_scrape(url="https://b.com")
        await replay_log.log_scrape(url="https://a.com")
        entries = await replay_log.list_replay_logs(url="https://a.com")
        assert len(entries) == 2
        assert all(e["url"] == "https://a.com" for e in entries)

    async def test_list_filter_by_status(self, replay_log):
        await replay_log.log_scrape(url="https://a.com", status="success")
        await replay_log.log_scrape(url="https://b.com", status="error")
        await replay_log.log_scrape(url="https://c.com", status="success")
        entries = await replay_log.list_replay_logs(status="error")
        assert len(entries) == 1
        assert entries[0]["status"] == "error"

    async def test_list_filter_by_method(self, replay_log):
        await replay_log.log_scrape(url="https://a.com", method="scrape")
        await replay_log.log_scrape(url="https://b.com", method="crawl")
        entries = await replay_log.list_replay_logs(method="crawl")
        assert len(entries) == 1
        assert entries[0]["method"] == "crawl"

    async def test_list_limit_and_offset(self, replay_log):
        for i in range(10):
            await replay_log.log_scrape(url=f"https://example{i}.com")
        entries = await replay_log.list_replay_logs(limit=3, offset=0)
        assert len(entries) == 3
        entries2 = await replay_log.list_replay_logs(limit=3, offset=3)
        assert len(entries2) == 3
        # Ensure offset works — different entries
        assert entries[0]["id"] != entries2[0]["id"]

    async def test_list_ordered_descending(self, replay_log):
        """Most recent entries should come first."""
        import time
        await replay_log.log_scrape(url="https://first.com")
        time.sleep(0.01)
        await replay_log.log_scrape(url="https://second.com")
        entries = await replay_log.list_replay_logs()
        assert entries[0]["url"] == "https://second.com"
        assert entries[1]["url"] == "https://first.com"


class TestGetReplayLog:
    """Tests for get_replay_log()."""

    async def test_get_nonexistent(self, replay_log):
        entry = await replay_log.get_replay_log("nonexistent-id")
        assert entry is None

    async def test_get_existing(self, replay_log):
        log_id = await replay_log.log_scrape(url="https://example.com")
        entry = await replay_log.get_replay_log(log_id)
        assert entry is not None
        assert entry["id"] == log_id


class TestCleanupExpired:
    """Tests for cleanup_expired()."""

    async def test_cleanup_removes_old_entries(self, replay_log):
        import time
        from datetime import datetime, timedelta, timezone

        # Log an entry
        await replay_log.log_scrape(url="https://old.com")

        # Manually backdate the entry
        old_time = (datetime.now(timezone.utc) - timedelta(hours=200)).isoformat()
        await replay_log._db.execute(
            "UPDATE scrape_replay_log SET created_at = ? WHERE url = ?",
            (old_time, "https://old.com"),
        )
        await replay_log._db.commit()

        # Log a recent entry
        await replay_log.log_scrape(url="https://new.com")

        # Cleanup entries older than 168 hours (7 days)
        deleted = await replay_log.cleanup_expired(max_age_hours=168)
        assert deleted == 1

        entries = await replay_log.list_replay_logs()
        assert len(entries) == 1
        assert entries[0]["url"] == "https://new.com"

    async def test_cleanup_with_no_expired(self, replay_log):
        await replay_log.log_scrape(url="https://example.com")
        deleted = await replay_log.cleanup_expired(max_age_hours=168)
        assert deleted == 0


class TestStats:
    """Tests for stats()."""

    async def test_stats_empty(self, replay_log):
        stats = await replay_log.stats()
        assert stats["total"] == 0
        assert stats["by_status"] == {}
        assert stats["by_method"] == {}

    async def test_stats_with_entries(self, replay_log):
        await replay_log.log_scrape(url="https://a.com", status="success", method="scrape")
        await replay_log.log_scrape(url="https://b.com", status="error", method="scrape")
        await replay_log.log_scrape(url="https://c.com", status="success", method="crawl")

        stats = await replay_log.stats()
        assert stats["total"] == 3
        assert stats["by_status"]["success"] == 2
        assert stats["by_status"]["error"] == 1
        assert stats["by_method"]["scrape"] == 2
        assert stats["by_method"]["crawl"] == 1