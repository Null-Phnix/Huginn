"""Proxy provider rotation, stickiness, health, and redaction tests."""

import pytest

from huginn.config import HuginnConfig
from huginn.proxy import (
    ProxyConfigurationError,
    ProxyEndpoint,
    ProxyUnavailable,
    StaticProxyProvider,
    build_proxy_provider,
)


def test_endpoint_extracts_credentials_without_exposing_them_in_server_or_label():
    endpoint = ProxyEndpoint.parse("http://user:p%40ss@proxy.example:8080")
    assert endpoint.server == "http://proxy.example:8080"
    assert endpoint.username == "user"
    assert endpoint.password == "p@ss"
    assert "user" not in endpoint.label
    assert "p@ss" not in endpoint.label


@pytest.mark.parametrize("url", ["ftp://proxy.example:21", "http://missing-port.example"])
def test_endpoint_rejects_invalid_proxy_urls(url):
    with pytest.raises(ProxyConfigurationError):
        ProxyEndpoint.parse(url)


def test_round_robin_and_cooldown_have_no_direct_fallback():
    first = ProxyEndpoint.parse("http://one.example:8080")
    second = ProxyEndpoint.parse("http://two.example:8080")
    provider = StaticProxyProvider(
        [first, second], failure_threshold=1, cooldown_seconds=60
    )
    lease_one = provider.acquire()
    lease_two = provider.acquire()
    assert lease_one.endpoint == first
    assert lease_two.endpoint == second

    lease_one.report_failure("connection reset")
    lease_two.report_failure("proxy auth failed")
    with pytest.raises(ProxyUnavailable):
        provider.acquire()
    assert provider.status()["direct_egress"] is False


def test_sticky_rotation_reuses_endpoint_and_reassigns_after_failure():
    endpoints = [
        ProxyEndpoint.parse("http://one.example:8080"),
        ProxyEndpoint.parse("http://two.example:8080"),
    ]
    provider = StaticProxyProvider(
        endpoints, rotation="sticky", failure_threshold=1, cooldown_seconds=60
    )
    first = provider.acquire("session-a")
    assert provider.acquire("session-a").endpoint == first.endpoint
    first.report_failure("dead")
    assert provider.acquire("session-a").endpoint != first.endpoint


def test_builder_is_direct_without_configuration_and_static_when_configured(tmp_path):
    config = HuginnConfig()
    direct = build_proxy_provider(config)
    assert direct.status() == {
        "mode": "direct",
        "configured": False,
        "direct_egress": True,
        "endpoints": 0,
    }

    proxy_file = tmp_path / "proxies.txt"
    proxy_file.write_text("# managed outside git\nhttp://one.example:8080\n")
    config.proxy.urls_file = str(proxy_file)
    static = build_proxy_provider(config)
    assert static.status()["configured"] is True
    assert static.acquire().as_browser_proxy() == {"server": "http://one.example:8080"}


def test_explicit_static_mode_requires_an_endpoint():
    config = HuginnConfig()
    config.proxy.provider = "static"
    with pytest.raises(ProxyConfigurationError):
        build_proxy_provider(config)
