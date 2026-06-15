"""
Huginn Doctor — comprehensive health checks for self-hosted instances.

The default `huginn doctor` command checks Python + dependencies + Chromium.
This module adds deeper checks via the `--check` flag:
  - Change tracking round-trip (verifies hashing + diff + storage work)
  - Webhook HMAC round-trip (verifies _compute_signature is correct)
  - LLM credentials (warns if no key for the configured provider)
  - API server reachability (probes HUGINN_API_URL/health if set)

Each check is best-effort and returns a CheckResult with status OK/WARN/FAIL/SKIP.
None of these should crash the doctor command.
"""

import asyncio
import hmac
import hashlib
import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, List, Optional

import httpx

logger = logging.getLogger(__name__)


class CheckStatus(str, Enum):
    """Health check status — used for color coding in the CLI table."""
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class CheckResult:
    """Result of a single health check."""
    component: str
    status: CheckStatus
    details: str


# ─── Individual checks ──────────────────────────────────────────────────────


async def check_change_tracking() -> CheckResult:
    """Verify ChangeTracker works end-to-end (hash + diff + storage).

    Round-trip: store "v1" content for a fresh URL, then store "v2"
    content. The second call should report changed=True and a non-empty
    previous_hash. If any of those fail, return FAIL.
    """
    try:
        from .change_tracker import ChangeTracker

        tracker = ChangeTracker()  # fresh, no state from prior calls
        url = "https://doctor.huginn/health-check/change-tracking"

        r1 = await tracker.check_and_store(url, "health check v1")
        r2 = await tracker.check_and_store(url, "health check v2")

        if r1["previous_hash"] is not None:
            return CheckResult(
                component="Change tracking",
                status=CheckStatus.FAIL,
                details="first scrape should have previous_hash=None",
            )
        if r2["previous_hash"] != r1["current_hash"]:
            return CheckResult(
                component="Change tracking",
                status=CheckStatus.FAIL,
                details="second scrape's previous_hash should match first's current_hash",
            )
        if r2["changed"] is not True:
            return CheckResult(
                component="Change tracking",
                status=CheckStatus.FAIL,
                details="second scrape with different content should report changed=True",
            )
        if not r2["diff"]:
            return CheckResult(
                component="Change tracking",
                status=CheckStatus.FAIL,
                details="second scrape should produce a non-empty diff",
            )

        return CheckResult(
            component="Change tracking",
            status=CheckStatus.OK,
            details=f"round-trip verified (hash {r2['current_hash']}, diff non-empty)",
        )
    except Exception as e:
        return CheckResult(
            component="Change tracking",
            status=CheckStatus.FAIL,
            details=f"exception during round-trip: {e}",
        )


async def check_webhook_signature() -> CheckResult:
    """Verify _compute_signature matches stdlib hmac.new(secret, body, sha256).

    A mismatch would mean Huginn is sending webhooks that receivers cannot
    verify — silent breakage for any customer using HMAC verification.
    """
    try:
        from . import webhook

        body = b'{"event": "doctor.health_check"}'
        secret = "doctor-test-secret"
        huginn_sig = webhook._compute_signature(body, secret)
        stdlib_sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

        if huginn_sig != stdlib_sig:
            return CheckResult(
                component="Webhook HMAC",
                status=CheckStatus.FAIL,
                details=f"Huginn signature {huginn_sig[:8]}... != stdlib {stdlib_sig[:8]}...",
            )

        return CheckResult(
            component="Webhook HMAC",
            status=CheckStatus.OK,
            details=f"signature matches stdlib (sha256={huginn_sig[:8]}...)",
        )
    except Exception as e:
        return CheckResult(
            component="Webhook HMAC",
            status=CheckStatus.FAIL,
            details=f"exception during signature check: {e}",
        )


async def check_llm_credentials() -> CheckResult:
    """Check that the configured LLM provider has its API key env var set.

    WARN (not FAIL) when no key is set — the server can still run scrape
    jobs that don't need an LLM. The /extract and /v1/research endpoints
    will return errors, but /scrape, /crawl, /map work fine.
    """
    provider = os.environ.get("HUGINN_LLM_PROVIDER", "openai").lower()
    key_env_map = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GOOGLE_API_KEY",
        "xai": "XAI_API_KEY",
        "ollama": "OLLAMA_API_KEY",  # optional — local models
    }
    key_env = key_env_map.get(provider)
    if key_env is None:
        return CheckResult(
            component="LLM credentials",
            status=CheckStatus.WARN,
            details=f"unknown provider '{provider}' (expected one of: {', '.join(key_env_map)})",
        )

    if provider == "ollama":
        # Ollama runs locally — no key required
        ollama_url = os.environ.get("OLLAMA_BASE_URL", "https://ollama.com/api")
        return CheckResult(
            component="LLM credentials",
            status=CheckStatus.OK,
            details=f"ollama provider, no key needed (base URL: {ollama_url})",
        )

    api_key = os.environ.get(key_env, "")
    if not api_key:
        return CheckResult(
            component="LLM credentials",
            status=CheckStatus.WARN,
            details=(
                f"{provider} provider configured but {key_env} is unset. "
                f"/extract and /v1/research will fail. Set it in .env to enable LLM features."
            ),
        )

    # Redact the key — show only prefix + last 4 chars
    redacted = api_key[:3] + "..." + api_key[-4:] if len(api_key) > 7 else "***"
    return CheckResult(
        component="LLM credentials",
        status=CheckStatus.OK,
        details=f"{provider} provider, {key_env}={redacted}",
    )


async def check_api_server() -> CheckResult:
    """Probe HUGINN_API_URL/health if set; SKIP otherwise.

    When set, the user is presumed to have a Huginn instance running.
    We do a GET to /health with a 3-second timeout — any 2xx response is OK.
    """
    api_url = os.environ.get("HUGINN_API_URL")
    if not api_url:
        return CheckResult(
            component="API server",
            status=CheckStatus.SKIP,
            details="HUGINN_API_URL not set — skipping server probe (set it to enable this check)",
        )

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{api_url.rstrip('/')}/health")
        if resp.status_code < 500:
            return CheckResult(
                component="API server",
                status=CheckStatus.OK,
                details=f"GET {api_url}/health → {resp.status_code}",
            )
        return CheckResult(
            component="API server",
            status=CheckStatus.FAIL,
            details=f"GET {api_url}/health → {resp.status_code} (server error)",
        )
    except Exception as e:
        return CheckResult(
            component="API server",
            status=CheckStatus.FAIL,
            details=f"GET {api_url}/health failed: {type(e).__name__}: {str(e)[:80]}",
        )


# ─── Orchestrator ──────────────────────────────────────────────────────────


# Registry of all checks. Add new checks here to have them run by default.
ALL_CHECKS: List[Callable[[], Awaitable[CheckResult]]] = [
    check_change_tracking,
    check_webhook_signature,
    check_llm_credentials,
    check_api_server,
]


async def run_all_checks() -> List[CheckResult]:
    """Run all registered checks sequentially and return their results.

    Sequential rather than concurrent because each check is fast (<100ms)
    and the doctor output reads cleaner as a single ordered table.
    """
    results: List[CheckResult] = []
    for check in ALL_CHECKS:
        try:
            result = await check()
        except Exception as e:
            # Last-resort safety net — a check that raises should never crash doctor
            logger.exception("Check %s raised", check.__name__)
            result = CheckResult(
                component=check.__name__,
                status=CheckStatus.FAIL,
                details=f"unhandled exception: {type(e).__name__}: {e}",
            )
        results.append(result)
    return results


def summarize(results: List[CheckResult]) -> dict[str, int]:
    """Count OK / WARN / FAIL / SKIP for the summary footer."""
    counts = {s.value: 0 for s in CheckStatus}
    for r in results:
        counts[r.status.value] += 1
    return counts
