"""Regression checks for the three AnyIO advisories fixed in 4.14.2."""

import os
import ssl
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
import pytest
from anyio import to_process
from anyio._core import _subprocesses
from anyio.streams.stapled import StapledObjectStream
from anyio.streams.tls import TLSStream
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


@pytest.mark.parametrize(
    ("hostname", "certificate_name", "accepted"),
    [
        ("example.test", "example.test", True),
        ("faß.test", "xn--fa-hia.test", True),
        ("faß.test", "fass.test", False),
        ("example.test", "wrong.test", False),
    ],
)
def test_tls_preserves_certificate_hostname_identity(tmp_path, hostname, certificate_name, accepted):
    # Ephemeral local test certificates only; no sockets, DNS, or external services.
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, certificate_name)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(certificate_name)]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.KeyUsage(True, False, True, False, False, True, True, False, False),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    cert_path, key_path = tmp_path / "certificate.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert_pem)
    with os.fdopen(os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as key_file:
        key_file.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_path, key_path)
    client_context = ssl.create_default_context(cadata=cert_pem.decode("ascii"))

    async def exchange():
        server_send, client_receive = anyio.create_memory_object_stream[bytes](10)
        client_send, server_receive = anyio.create_memory_object_stream[bytes](10)
        server_transport = StapledObjectStream(server_send, server_receive)
        client_transport = StapledObjectStream(client_send, client_receive)

        async def serve():
            try:
                async with await TLSStream.wrap(
                    server_transport, server_side=True, ssl_context=server_context,
                    standard_compatible=False,
                ) as stream:
                    assert await stream.receive() == b"ping"
                    await stream.send(b"pong")
            except (ssl.SSLError, anyio.EndOfStream, anyio.BrokenResourceError):
                # Expected when the client rejects the peer certificate.
                pass
            finally:
                await server_transport.aclose()

        with anyio.fail_after(5):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(serve)
                try:
                    async with await TLSStream.wrap(
                        client_transport, hostname=hostname, ssl_context=client_context,
                        standard_compatible=False,
                    ) as stream:
                        await stream.send(b"ping")
                        assert await stream.receive() == b"pong"
                    return True
                except ssl.SSLCertVerificationError:
                    return False
                finally:
                    await client_transport.aclose()
                    tasks.cancel_scope.cancel()

    assert anyio.run(exchange) is accepted


@pytest.mark.parametrize(
    ("group", "extra_groups"),
    [(None, None), (None, []), (None, [101, 102]), (42, [])],
)
def test_process_supplementary_groups_reach_backend(monkeypatch, group, extra_groups):
    # Inspect forwarding without needing privilege or changing host process groups.
    backend = SimpleNamespace(open_process=AsyncMock())
    monkeypatch.setattr(_subprocesses, "get_async_backend", lambda: backend)

    async def launch():
        await anyio.open_process([sys.executable, "-c", "pass"], group=group, extra_groups=extra_groups)

    anyio.run(launch)
    forwarded = backend.open_process.call_args.kwargs
    if extra_groups is None:
        assert "extra_groups" not in forwarded
    else:
        assert forwarded["extra_groups"] == extra_groups
    if group is not None:
        assert forwarded["group"] == group


def test_process_pool_stderr_flood_completes():
    async def exercise():
        with anyio.fail_after(5):
            assert await to_process.run_sync(abs, -7, cancellable=True) == 7
            assert await to_process.run_sync(
                exec,
                "import sys; sys.stderr.write('x' * (1024 * 1024)); sys.stderr.flush()",
                cancellable=True,
            ) is None
            assert await to_process.run_sync(abs, -9, cancellable=True) == 9

    anyio.run(exercise)
