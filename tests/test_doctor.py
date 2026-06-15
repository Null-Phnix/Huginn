"""
Tests for the expanded huginn doctor --check command.

The default `huginn doctor` checks Python + deps + Chromium launch.
The expanded `--check` flag also runs:
  - Change tracking round-trip (verifies hashing + diff + storage work)
  - Webhook HMAC round-trip (verifies _compute_signature is correct)
  - LLM credentials (warns if no API key set for the configured provider)
  - API server reachability (probes a local Huginn instance)
  - Cache backend health (verifies the response cache is functional)

Each check returns a CheckResult with (component, status, details).
Status is one of: OK, WARN, FAIL, SKIP.
"""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from huginn.doctor import (
    CheckStatus,
    CheckResult,
    check_change_tracking,
    check_webhook_signature,
    check_llm_credentials,
    check_api_server,
    run_all_checks,
)


# ─── CheckStatus + CheckResult ──────────────────────────────────────────────

class TestCheckResult:
    """CheckResult is a simple (component, status, details) tuple."""

    def test_check_result_attributes(self):
        """CheckResult has component, status, details attributes."""
        r = CheckResult(component="test", status=CheckStatus.OK, details="works")
        assert r.component == "test"
        assert r.status == CheckStatus.OK
        assert r.details == "works"

    def test_check_status_values(self):
        """CheckStatus has the expected string values."""
        assert CheckStatus.OK.value == "ok"
        assert CheckStatus.WARN.value == "warn"
        assert CheckStatus.FAIL.value == "fail"
        assert CheckStatus.SKIP.value == "skip"

    def test_check_result_is_immutable(self):
        """CheckResult is frozen (immutable)."""
        from dataclasses import FrozenInstanceError
        r = CheckResult(component="x", status=CheckStatus.OK, details="")
        with pytest.raises(FrozenInstanceError):
            r.component = "y"  # type: ignore[misc]


# ─── check_change_tracking ──────────────────────────────────────────────────

class TestCheckChangeTracking:
    """check_change_tracking() verifies ChangeTracker works end-to-end."""

    @pytest.mark.asyncio
    async def test_change_tracking_check_ok_when_roundtrip_works(self):
        """Round-trip: store content, store different content, verify changed=True."""
        result = await check_change_tracking()
        assert result.component == "Change tracking"
        assert result.status == CheckStatus.OK
        assert "round-trip" in result.details.lower() or "verified" in result.details.lower()

    @pytest.mark.asyncio
    async def test_change_tracking_check_uses_fresh_tracker(self):
        """Each call uses a fresh tracker (no state leak between calls)."""
        # First call should leave no state
        r1 = await check_change_tracking()
        # Second call should also pass — fresh tracker instance
        r2 = await check_change_tracking()
        assert r1.status == CheckStatus.OK
        assert r2.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_change_tracking_check_catches_hash_mismatch(self):
        """If hashing is broken, the check returns FAIL."""
        from huginn import change_tracker
        original_compute_hash = change_tracker.ChangeTracker.compute_hash
        # Sabotage the hash function
        change_tracker.ChangeTracker.compute_hash = staticmethod(lambda content: "broken")
        try:
            result = await check_change_tracking()
            assert result.status == CheckStatus.FAIL
        finally:
            change_tracker.ChangeTracker.compute_hash = staticmethod(original_compute_hash)


# ─── check_webhook_signature ────────────────────────────────────────────────

class TestCheckWebhookSignature:
    """check_webhook_signature() verifies HMAC computation is correct."""

    @pytest.mark.asyncio
    async def test_webhook_signature_check_ok(self):
        """Round-trip: _compute_signature matches hmac.new + sha256."""
        result = await check_webhook_signature()
        assert result.component == "Webhook HMAC"
        assert result.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_webhook_signature_check_with_broken_compute_returns_fail(self):
        """If _compute_signature is broken, the check returns FAIL."""
        from huginn import webhook
        original = webhook._compute_signature
        webhook._compute_signature = lambda body, secret: "definitely-not-correct"
        try:
            result = await check_webhook_signature()
            assert result.status == CheckStatus.FAIL
        finally:
            webhook._compute_signature = original


# ─── check_llm_credentials ──────────────────────────────────────────────────

class TestCheckLLMCredentials:
    """check_llm_credentials() warns (not fails) when no key is configured."""

    @pytest.mark.asyncio
    async def test_llm_check_ok_when_openai_key_set(self, monkeypatch):
        """When OPENAI_API_KEY is set, status is OK."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
        monkeypatch.setenv("HUGINN_LLM_PROVIDER", "openai")
        result = await check_llm_credentials()
        assert result.component == "LLM credentials"
        assert result.status == CheckStatus.OK
        assert "openai" in result.details.lower()

    @pytest.mark.asyncio
    async def test_llm_check_warn_when_no_key(self, monkeypatch):
        """When no API key for configured provider, status is WARN (not FAIL)."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("XAI_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        monkeypatch.setenv("HUGINN_LLM_PROVIDER", "openai")
        result = await check_llm_credentials()
        assert result.status == CheckStatus.WARN
        assert "openai" in result.details.lower()

    @pytest.mark.asyncio
    async def test_llm_check_ollama_no_key_required(self, monkeypatch):
        """Ollama provider doesn't require an API key (local model)."""
        for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "XAI_API_KEY", "OLLAMA_API_KEY"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("HUGINN_LLM_PROVIDER", "ollama")
        result = await check_llm_credentials()
        assert result.status == CheckStatus.OK


# ─── check_api_server ───────────────────────────────────────────────────────

class TestCheckAPIServer:
    """check_api_server() probes a local Huginn instance (default: no server = SKIP)."""

    @pytest.mark.asyncio
    async def test_api_server_check_skip_when_no_url(self):
        """Without HUGINN_API_URL, status is SKIP (no server to probe)."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HUGINN_API_URL", None)
            result = await check_api_server()
        assert result.status == CheckStatus.SKIP
        assert "HUGINN_API_URL" in result.details

    @pytest.mark.asyncio
    async def test_api_server_check_ok_when_health_endpoint_responds(self):
        """When HUGINN_API_URL is set and /health returns 200, status is OK."""
        with patch("huginn.doctor.httpx.AsyncClient") as mock_client_cls:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json = MagicMock(return_value={"status": "ok"})
            mock_client = MagicMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock()

            with patch.dict(os.environ, {"HUGINN_API_URL": "http://localhost:7432"}):
                result = await check_api_server()

        assert result.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_api_server_check_fail_on_connection_error(self):
        """When the server is unreachable, status is FAIL."""
        with patch("huginn.doctor.httpx.AsyncClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock()

            with patch.dict(os.environ, {"HUGINN_API_URL": "http://localhost:9999"}):
                result = await check_api_server()

        assert result.status == CheckStatus.FAIL
        # The error message will be in result.details — just confirm it's non-empty
        assert result.details


# ─── run_all_checks ─────────────────────────────────────────────────────────

class TestRunAllChecks:
    """run_all_checks() orchestrates all checks and returns a summary."""

    @pytest.mark.asyncio
    async def test_run_all_checks_returns_list(self):
        """run_all_checks returns a list of CheckResult."""
        results = await run_all_checks()
        assert isinstance(results, list)
        assert all(isinstance(r, CheckResult) for r in results)

    @pytest.mark.asyncio
    async def test_run_all_checks_includes_all_components(self):
        """run_all_checks runs all expected checks."""
        results = await run_all_checks()
        components = {r.component for r in results}
        # Should include: change tracking, webhook HMAC, LLM creds, API server
        assert "Change tracking" in components
        assert "Webhook HMAC" in components
        assert "LLM credentials" in components
        assert "API server" in components

    @pytest.mark.asyncio
    async def test_run_all_checks_summary_counts(self):
        """run_all_checks returns a summary with OK/WARN/FAIL counts."""
        results = await run_all_checks()
        ok_count = sum(1 for r in results if r.status == CheckStatus.OK)
        warn_count = sum(1 for r in results if r.status == CheckStatus.WARN)
        fail_count = sum(1 for r in results if r.status == CheckStatus.FAIL)
        # At minimum, change tracking + webhook HMAC should be OK
        # (they don't need any external service)
        assert ok_count >= 2
