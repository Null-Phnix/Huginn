"""Backend-independent SSRF protection tests."""

import socket
from unittest.mock import AsyncMock, patch

import pytest

from huginn.scraper import Scraper
from huginn.security import resolve_public_url_target, validate_public_url
from huginn.webhook import _deliver_webhook, _PinnedResolver


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin",
    "http://10.0.0.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://[64:ff9b::7f00:1]/",
    "http://[64:ff9b::a00:1]/",
    "http://[64:ff9b::a9fe:a9fe]/",
    "http://localhost:8080/",
])
async def test_private_targets_are_rejected(url):
    with pytest.raises(ValueError, match="SSRF blocked"):
        await validate_public_url(url, resolve_dns=False)


@pytest.mark.asyncio
async def test_hostname_resolving_to_private_address_is_rejected(monkeypatch):
    monkeypatch.setattr(
        "huginn.security.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("192.168.1.10", 443))],
    )
    with pytest.raises(ValueError, match="non-public address"):
        await validate_public_url("https://rebinding.example")


@pytest.mark.asyncio
async def test_explicit_private_network_opt_in_is_honored():
    await validate_public_url("http://127.0.0.1:8080", allow_private=True)


@pytest.mark.asyncio
async def test_nat64_encoded_public_ipv4_is_allowed():
    await validate_public_url("https://[64:ff9b::5db8:d822]/", resolve_dns=False)


@pytest.mark.asyncio
async def test_scraper_rejects_private_target_before_any_backend_fallback():
    browser = AsyncMock()
    scraper = Scraper(browser)
    with pytest.raises(ValueError, match="SSRF blocked"):
        await scraper.scrape("http://127.0.0.1/private", render_mode="full")
    browser.new_context.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolved_target_rejects_mixed_public_and_private_dns(monkeypatch):
    """Reject the whole DNS answer set if any address is non-public."""
    monkeypatch.setattr(
        "huginn.security.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443)),
        ],
    )
    with pytest.raises(ValueError, match="non-public address"):
        await resolve_public_url_target("https://callback.example/hook")


@pytest.mark.asyncio
async def test_resolved_target_returns_only_validated_addresses(monkeypatch):
    """Delivery receives the exact addresses that passed validation."""
    monkeypatch.setattr(
        "huginn.security.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 8443)),
            (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("2606:2800:220:1:248:1893:25c8:1946", 8443, 0, 0)),
        ],
    )
    target = await resolve_public_url_target("https://callback.example:8443/hook")
    assert target.hostname == "callback.example"
    assert target.port == 8443
    assert target.addresses == (
        (socket.AF_INET, "93.184.216.34"),
        (socket.AF_INET6, "2606:2800:220:1:248:1893:25c8:1946"),
    )


@pytest.mark.asyncio
async def test_pinned_resolver_refuses_unvalidated_host_and_port(monkeypatch):
    monkeypatch.setattr(
        "huginn.security.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
        ],
    )
    target = await resolve_public_url_target("https://callback.example/hook")
    resolver = _PinnedResolver(target)
    resolved = await resolver.resolve("callback.example", 443, socket.AF_UNSPEC)
    assert [entry["host"] for entry in resolved] == ["93.184.216.34"]
    with pytest.raises(OSError, match="unvalidated"):
        await resolver.resolve("other.example", 443, socket.AF_UNSPEC)
    with pytest.raises(OSError, match="unvalidated"):
        await resolver.resolve("callback.example", 444, socket.AF_UNSPEC)


@pytest.mark.asyncio
async def test_private_webhook_is_rejected_before_client_creation():
    with patch("huginn.webhook.aiohttp.ClientSession") as session:
        with pytest.raises(ValueError, match="SSRF blocked"):
            await _deliver_webhook(
                url="http://127.0.0.1/admin",
                body=b"{}",
                headers={"Content-Type": "application/json"},
                timeout=1.0,
            )
    session.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://2130706433/",
        "http://0x7f000001/",
        "http://0177.0.0.1/",
    ],
)
async def test_obscure_numeric_loopback_webhooks_are_rejected(url):
    with pytest.raises(ValueError, match="non-public address"):
        await resolve_public_url_target(url)
