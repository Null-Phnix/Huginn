"""Network-target validation shared by every Huginn rendering backend."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse


_BLOCKED_HOSTS = {
    "localhost",
    "metadata.google.internal",
    "metadata.azure.internal",
    "instance-data.ec2.internal",
}


def _require_global_ip(value: str) -> None:
    ip = ipaddress.ip_address(value.split("%", 1)[0])
    if not ip.is_global:
        raise ValueError(f"SSRF blocked non-public address: {ip}")


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
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must be an absolute http(s) URL")
    if allow_private:
        return

    hostname = parsed.hostname.rstrip(".").lower()
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

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
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
