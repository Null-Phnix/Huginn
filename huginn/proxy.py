"""Explicit proxy provider, rotation, stickiness, and health boundary.

StarSearch supplies browser identity controls, not network egress.  This module
keeps the two concerns separate and never falls back to direct traffic when a
configured proxy provider has no healthy endpoint.
"""

from __future__ import annotations

import hashlib
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import HuginnConfig, read_secret_file


class ProxyConfigurationError(ValueError):
    pass


class ProxyUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ProxyEndpoint:
    server: str
    username: Optional[str] = None
    password: Optional[str] = None

    @classmethod
    def parse(
        cls,
        value: str,
        *,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> "ProxyEndpoint":
        raw = value.strip()
        try:
            parsed = urllib.parse.urlsplit(raw)
            port = parsed.port
        except (ValueError, TypeError) as exc:
            raise ProxyConfigurationError("Invalid proxy URL") from exc
        if parsed.scheme not in {"http", "https", "socks5"}:
            raise ProxyConfigurationError("Proxy URL scheme must be http, https, or socks5")
        if not parsed.hostname or port is None:
            raise ProxyConfigurationError("Proxy URL must include a host and port")
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        server = f"{parsed.scheme}://{host}:{port}"
        return cls(
            server=server,
            username=username
            or (urllib.parse.unquote(parsed.username) if parsed.username else None),
            password=password
            or (urllib.parse.unquote(parsed.password) if parsed.password else None),
        )

    @property
    def label(self) -> str:
        parsed = urllib.parse.urlsplit(self.server)
        return f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"

    @property
    def routing_identity(self) -> str:
        """Return the stable, credential-safe identity of an egress account.

        A proxy host can expose multiple independently routed accounts through
        different usernames.  Health, stickiness, and rendezvous selection must
        therefore distinguish those accounts.  Passwords are deliberately not
        part of the identity so credential rotation does not silently rebind a
        named browser context, and the username itself is never exposed.
        """
        material = f"proxy-endpoint-v1\0{self.server}\0{self.username or ''}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @property
    def public_identity(self) -> str:
        """Opaque identity safe for health output and logs."""
        return f"proxy-{self.routing_identity[:16]}"

    def as_browser_proxy(self) -> dict[str, str]:
        proxy = {"server": self.server}
        if self.username:
            proxy["username"] = self.username
        if self.password:
            proxy["password"] = self.password
        return proxy


@dataclass
class _EndpointHealth:
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    last_error: Optional[str] = None


class ProxyLease:
    def __init__(self, provider: "ProxyProvider", endpoint: Optional[ProxyEndpoint]):
        self._provider = provider
        self.endpoint = endpoint

    @property
    def configured(self) -> bool:
        return self.endpoint is not None

    def as_browser_proxy(self) -> Optional[dict[str, str]]:
        return self.endpoint.as_browser_proxy() if self.endpoint else None

    def report_success(self) -> None:
        if self.endpoint:
            self._provider.report_success(self.endpoint)

    def report_failure(self, error: str) -> None:
        if self.endpoint:
            self._provider.report_failure(self.endpoint, error)


class ProxyProvider:
    mode = "direct"

    def acquire(
        self,
        session_key: Optional[str] = None,
        *,
        strict_sticky: bool = False,
    ) -> ProxyLease:
        del session_key, strict_sticky
        return ProxyLease(self, None)

    def report_success(self, endpoint: ProxyEndpoint) -> None:
        del endpoint

    def report_failure(self, endpoint: ProxyEndpoint, error: str) -> None:
        del endpoint, error

    def status(self) -> dict:
        return {
            "mode": self.mode,
            "configured": False,
            "direct_egress": True,
            "endpoints": 0,
        }

    def cache_identity(self) -> dict:
        return {"mode": self.mode}


class StaticProxyProvider(ProxyProvider):
    mode = "static"

    def __init__(
        self,
        endpoints: list[ProxyEndpoint],
        *,
        rotation: str = "round_robin",
        failure_threshold: int = 3,
        cooldown_seconds: int = 60,
    ):
        if not endpoints:
            raise ProxyConfigurationError("Static proxy provider requires at least one endpoint")
        if rotation not in {"round_robin", "sticky"}:
            raise ProxyConfigurationError("Proxy rotation must be round_robin or sticky")
        self.endpoints = endpoints
        self.rotation = rotation
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(1, cooldown_seconds)
        self._health = {endpoint.routing_identity: _EndpointHealth() for endpoint in endpoints}
        self._sticky: dict[str, str] = {}
        self._cursor = 0
        self._lock = threading.Lock()

    def acquire(
        self,
        session_key: Optional[str] = None,
        *,
        strict_sticky: bool = False,
    ) -> ProxyLease:
        now = time.monotonic()
        with self._lock:
            if strict_sticky:
                if not session_key:
                    raise ProxyConfigurationError(
                        "Strict sticky proxy acquisition requires a session key"
                    )
                selected = max(
                    self.endpoints,
                    key=lambda endpoint: hashlib.sha256(
                        f"{session_key}\0{endpoint.routing_identity}".encode()
                    ).digest(),
                )
                health = self._health[selected.routing_identity]
                if health.cooldown_until > now:
                    retry_in = max(0.0, health.cooldown_until - now)
                    raise ProxyUnavailable(
                        "The proxy endpoint bound to this persistent context is "
                        f"cooling down; retry in {retry_in:.1f}s"
                    )
                self._sticky[session_key] = selected.routing_identity
                return ProxyLease(self, selected)
            eligible = [
                endpoint
                for endpoint in self.endpoints
                if self._health[endpoint.routing_identity].cooldown_until <= now
            ]
            if not eligible:
                retry_in = min(
                    max(0.0, health.cooldown_until - now) for health in self._health.values()
                )
                raise ProxyUnavailable(
                    f"All configured proxy endpoints are cooling down; retry in {retry_in:.1f}s"
                )
            if self.rotation == "sticky" and session_key:
                sticky_identity = self._sticky.get(session_key)
                selected = next(
                    (
                        endpoint
                        for endpoint in eligible
                        if endpoint.routing_identity == sticky_identity
                    ),
                    None,
                )
                if selected is None:
                    selected = eligible[self._cursor % len(eligible)]
                    self._cursor += 1
                    self._sticky[session_key] = selected.routing_identity
            else:
                selected = eligible[self._cursor % len(eligible)]
                self._cursor += 1
            return ProxyLease(self, selected)

    def report_success(self, endpoint: ProxyEndpoint) -> None:
        with self._lock:
            health = self._health[endpoint.routing_identity]
            health.successes += 1
            health.consecutive_failures = 0
            health.cooldown_until = 0.0
            health.last_error = None

    def report_failure(self, endpoint: ProxyEndpoint, error: str) -> None:
        with self._lock:
            health = self._health[endpoint.routing_identity]
            health.failures += 1
            health.consecutive_failures += 1
            health.last_error = error[:240]
            if health.consecutive_failures >= self.failure_threshold:
                health.cooldown_until = time.monotonic() + self.cooldown_seconds
                for key, identity in list(self._sticky.items()):
                    if identity == endpoint.routing_identity:
                        self._sticky.pop(key, None)

    def status(self) -> dict:
        now = time.monotonic()
        with self._lock:
            endpoints = []
            for endpoint in self.endpoints:
                health = self._health[endpoint.routing_identity]
                endpoints.append(
                    {
                        "endpoint": endpoint.label,
                        "identity": endpoint.public_identity,
                        "healthy": health.cooldown_until <= now,
                        "successes": health.successes,
                        "failures": health.failures,
                        "consecutive_failures": health.consecutive_failures,
                        "cooldown_remaining_s": round(max(0.0, health.cooldown_until - now), 1),
                    }
                )
            return {
                "mode": self.mode,
                "configured": True,
                "direct_egress": False,
                "rotation": self.rotation,
                "endpoints": endpoints,
            }

    def cache_identity(self) -> dict:
        return {
            "mode": self.mode,
            "rotation": self.rotation,
            "endpoints": sorted(
                (
                    {
                        "endpoint": endpoint.label,
                        "identity": endpoint.public_identity,
                    }
                    for endpoint in self.endpoints
                ),
                key=lambda item: (item["endpoint"], item["identity"]),
            ),
        }


def _configured_proxy_values(config: HuginnConfig) -> list[str]:
    values: list[str] = []
    if config.proxy.urls_file:
        path = Path(config.proxy.urls_file)
        try:
            lines = read_secret_file(
                str(path),
                "HUGINN_PROXY_URLS_FILE",
                "HUGINN_SECRET_OWNER_UID",
            ).splitlines()
        except RuntimeError as exc:
            raise ProxyConfigurationError(f"Cannot read proxy URL file {path}: {exc}") from exc
        values.extend(
            line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")
        )
    if config.proxy.urls:
        values.extend(
            item.strip()
            for line in config.proxy.urls.splitlines()
            for item in line.split(",")
            if item.strip()
        )
    return values


def build_proxy_provider(config: HuginnConfig) -> ProxyProvider:
    mode = config.proxy.provider.lower()
    if mode == "direct":
        return ProxyProvider()
    values = _configured_proxy_values(config)
    if config.proxy.server:
        values.append(config.proxy.server)
    if mode == "auto":
        mode = "static" if values else "direct"
    if mode == "direct":
        return ProxyProvider()
    if mode != "static":
        raise ProxyConfigurationError(f"Unsupported proxy provider: {config.proxy.provider}")

    endpoints = []
    for index, value in enumerate(values):
        endpoints.append(
            ProxyEndpoint.parse(
                value,
                username=config.proxy.username
                if index == len(values) - 1 and value == config.proxy.server
                else None,
                password=config.proxy.password
                if index == len(values) - 1 and value == config.proxy.server
                else None,
            )
        )
    return StaticProxyProvider(
        endpoints,
        rotation=config.proxy.rotation,
        failure_threshold=config.proxy.failure_threshold,
        cooldown_seconds=config.proxy.cooldown_seconds,
    )
