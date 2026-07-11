"""Backend-independent SSRF protection tests."""

from unittest.mock import AsyncMock

import pytest

from huginn.scraper import Scraper
from huginn.security import validate_public_url


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin",
    "http://10.0.0.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
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
async def test_scraper_rejects_private_target_before_any_backend_fallback():
    browser = AsyncMock()
    scraper = Scraper(browser)
    with pytest.raises(ValueError, match="SSRF blocked"):
        await scraper.scrape("http://127.0.0.1/private", render_mode="full")
    browser.new_context.assert_not_awaited()
