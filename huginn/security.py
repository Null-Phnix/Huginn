"""Network-target validation shared by every Huginn rendering backend."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

_BLOCKED_HOSTS = {
    "localhost",
    "metadata.google.internal",
    "metadata.azure.internal",
    "instance-data.ec2.internal",
}
_NAT64_WELL_KNOWN_PREFIX = ipaddress.ip_network("64:ff9b::/96")


@dataclass(frozen=True)
class ResolvedPublicTarget:
    """An HTTP target and the exact public addresses approved for connection."""

    hostname: str
    port: int
    addresses: tuple[tuple[int, str], ...]


def _require_global_ip(value: str) -> None:
    ip = ipaddress.ip_address(value.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip in _NAT64_WELL_KNOWN_PREFIX:
        embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if not embedded.is_global:
            raise ValueError(f"SSRF blocked NAT64-encoded non-public address: {embedded}")
    if not ip.is_global:
        raise ValueError(f"SSRF blocked non-public address: {ip}")


def _parse_http_target(url: str) -> tuple[str, int]:
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("URL must be an absolute http(s) URL") from exc

    if parsed.scheme not in {"http", "https"} or not hostname:
        raise ValueError("URL must be an absolute http(s) URL")

    hostname = hostname.rstrip(".").encode("idna").decode("ascii").lower()
    if not hostname:
        raise ValueError("URL must be an absolute http(s) URL")
    return hostname, port


async def validate_public_url(
    url: str,
    *,
    allow_private: bool = False,
    resolve_dns: bool = True,
) -> None:
    """Reject non-HTTP and private-network targets before backend selection.

    Hostnames are resolved once here and again by StarSearch. This prevents a
    rejected StarSearch request from becoming an unrestricted Playwright
    fallback. Set the explicit service-level allow-private option only for a
    trusted local deployment that intentionally scrapes intranet services.
    """
    hostname, port = _parse_http_target(url)
    if allow_private:
        return

    if hostname in _BLOCKED_HOSTS or hostname.endswith(".localhost"):
        raise ValueError(f"SSRF blocked hostname: {hostname}")

    try:
        _require_global_ip(hostname)
        return
    except ValueError as exc:
        # A syntactically valid IP was rejected; a hostname should continue to
        # DNS resolution instead.
        try:
            ipaddress.ip_address(hostname.split("%", 1)[0])
        except ValueError:
            pass
        else:
            raise exc

    if not resolve_dns:
        return

    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo,
            hostname,
            port,
            0,
            socket.SOCK_STREAM,
        )
    except socket.gaierror:
        # Let the selected backend report normal DNS/connection failure.
        return
    for address in addresses:
        _require_global_ip(address[4][0])


async def resolve_public_url_target(
    url: str,
    *,
    allow_private: bool = False,
) -> ResolvedPublicTarget:
    """Resolve once, validate every answer, and return addresses to pin."""
    hostname, port = _parse_http_target(url)
    await validate_public_url(url, allow_private=allow_private, resolve_dns=False)

    try:
        literal = ipaddress.ip_address(hostname.split("%", 1)[0])
    except ValueError:
        literal = None

    if literal is not None:
        family = socket.AF_INET6 if literal.version == 6 else socket.AF_INET
        return ResolvedPublicTarget(hostname, port, ((family, str(literal)),))

    try:
        resolved = await asyncio.to_thread(
            socket.getaddrinfo,
            hostname,
            port,
            0,
            socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise ValueError(f"Webhook hostname could not be resolved: {hostname}") from exc

    addresses: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for family, _, _, _, address in resolved:
        value = address[0]
        if not allow_private:
            _require_global_ip(value)
        candidate = (family, value)
        if candidate not in seen:
            seen.add(candidate)
            addresses.append(candidate)

    if not addresses:
        raise ValueError(f"Webhook hostname returned no usable addresses: {hostname}")
    return ResolvedPublicTarget(hostname, port, tuple(addresses))
