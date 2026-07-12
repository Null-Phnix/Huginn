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


def test_endpoint_identity_distinguishes_accounts_without_exposing_credentials():
    alice = ProxyEndpoint.parse("http://alice:old-secret@proxy.example:8080")
    alice_rotated = ProxyEndpoint.parse("http://alice:new-secret@proxy.example:8080")
    bob = ProxyEndpoint.parse("http://bob:bob-secret@proxy.example:8080")

    assert alice.routing_identity == alice_rotated.routing_identity
    assert alice.public_identity == alice_rotated.public_identity
    assert alice.routing_identity != bob.routing_identity
    assert alice.public_identity != bob.public_identity

    provider = StaticProxyProvider([alice, bob])
    public_output = repr(provider.status()) + repr(provider.cache_identity())
    for credential in ("alice", "bob", "old-secret", "new-secret", "bob-secret"):
        assert credential not in public_output


@pytest.mark.parametrize("url", ["ftp://proxy.example:21", "http://missing-port.example"])
def test_endpoint_rejects_invalid_proxy_urls(url):
    with pytest.raises(ProxyConfigurationError):
        ProxyEndpoint.parse(url)


def test_round_robin_and_cooldown_have_no_direct_fallback():
    first = ProxyEndpoint.parse("http://one.example:8080")
    second = ProxyEndpoint.parse("http://two.example:8080")
    provider = StaticProxyProvider([first, second], failure_threshold=1, cooldown_seconds=60)
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


def test_strict_sticky_context_selection_is_deterministic_across_restarts():
    endpoints = [
        ProxyEndpoint.parse("http://one.example:8080"),
        ProxyEndpoint.parse("http://two.example:8080"),
        ProxyEndpoint.parse("http://three.example:8080"),
    ]
    first_provider = StaticProxyProvider(endpoints, rotation="round_robin")
    restarted_provider = StaticProxyProvider(list(reversed(endpoints)), rotation="round_robin")
    first = first_provider.acquire("browser-context:hermes", strict_sticky=True).endpoint
    assert first_provider.acquire("browser-context:hermes", strict_sticky=True).endpoint == first
    assert (
        restarted_provider.acquire("browser-context:hermes", strict_sticky=True).endpoint == first
    )


def test_strict_sticky_rendezvous_distinguishes_accounts_on_same_server():
    alice = ProxyEndpoint.parse("http://alice:secret-a@shared.example:8080")
    bob = ProxyEndpoint.parse("http://bob:secret-b@shared.example:8080")
    first_provider = StaticProxyProvider([alice, bob], rotation="round_robin")
    restarted_provider = StaticProxyProvider([bob, alice], rotation="round_robin")

    first = first_provider.acquire("browser-context:hermes", strict_sticky=True).endpoint
    after_restart = restarted_provider.acquire(
        "browser-context:hermes", strict_sticky=True
    ).endpoint

    assert first == after_restart
    assert first.routing_identity in {
        alice.routing_identity,
        bob.routing_identity,
    }


def test_health_is_tracked_per_account_identity_on_shared_server():
    alice = ProxyEndpoint.parse("http://alice:secret-a@shared.example:8080")
    bob = ProxyEndpoint.parse("http://bob:secret-b@shared.example:8080")
    provider = StaticProxyProvider([alice, bob], failure_threshold=1, cooldown_seconds=60)

    provider.report_failure(alice, "proxy authentication failed")

    status = {endpoint["identity"]: endpoint for endpoint in provider.status()["endpoints"]}
    assert status[alice.public_identity]["healthy"] is False
    assert status[alice.public_identity]["failures"] == 1
    assert status[bob.public_identity]["healthy"] is True
    assert status[bob.public_identity]["failures"] == 0


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
    proxy_file.chmod(0o600)
    config.proxy.urls_file = str(proxy_file)
    static = build_proxy_provider(config)
    assert static.status()["configured"] is True
    assert static.acquire().as_browser_proxy() == {"server": "http://one.example:8080"}


def test_proxy_secret_file_rejects_broad_permissions(tmp_path):
    config = HuginnConfig()
    config.proxy.provider = "static"
    proxy_file = tmp_path / "proxies.txt"
    proxy_file.write_text("http://one.example:8080\n")
    proxy_file.chmod(0o644)
    config.proxy.urls_file = str(proxy_file)

    with pytest.raises(ProxyConfigurationError, match="0600 or stricter"):
        build_proxy_provider(config)


def test_explicit_static_mode_requires_an_endpoint():
    config = HuginnConfig()
    config.proxy.provider = "static"
    with pytest.raises(ProxyConfigurationError):
        build_proxy_provider(config)
