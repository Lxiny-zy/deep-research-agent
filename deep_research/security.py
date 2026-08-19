"""Security policies for provider endpoints and catalog credentials."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
from dataclasses import dataclass
from typing import cast
from urllib.parse import urlsplit

import httpcore
import httpx
from cryptography.fernet import Fernet, InvalidToken


class ProviderURLPolicyError(ValueError):
    pass


def _host_allowed(host: str, allowlist: tuple[str, ...]) -> bool:
    if not allowlist:
        return True
    for entry in allowlist:
        candidate = entry.strip().lower().rstrip(".")
        if not candidate:
            continue
        if candidate.startswith("*."):
            suffix = candidate[1:]
            if host.endswith(suffix) and host != suffix[1:]:
                return True
        elif host == candidate:
            return True
    return False


def _reject_non_public_address(address: str, *, allow_private: bool) -> None:
    if allow_private:
        return
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return
    if not parsed.is_global:
        raise ProviderURLPolicyError("provider endpoint resolves to a non-public address")


def _reject_obfuscated_ip(host: str, *, allow_private: bool) -> None:
    """Reject alternate IPv4 spellings that URL clients normalize differently."""
    if allow_private or not re.fullmatch(r"[0-9a-fA-FxX.]+", host):
        return
    try:
        packed = socket.inet_aton(host)
    except OSError:
        return
    _reject_non_public_address(socket.inet_ntoa(packed), allow_private=False)


def _validate_hostname(host: str) -> None:
    """Reject ambiguous URL host spellings before any network operation."""
    if not host:
        raise ProviderURLPolicyError("provider endpoint must include a hostname")
    try:
        host.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ProviderURLPolicyError("provider endpoint hostname must use ASCII") from exc
    if any(char.isspace() or char in {"%", "\\", "/", "@"} for char in host):
        raise ProviderURLPolicyError("provider endpoint hostname contains invalid characters")
    try:
        ipaddress.ip_address(host)
        return
    except ValueError:
        pass
    if len(host) > 253:
        raise ProviderURLPolicyError("provider endpoint hostname is too long")
    labels = host.rstrip(".").split(".")
    if any(
        not label
        or len(label) > 63
        or not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?", label)
        for label in labels
    ):
        raise ProviderURLPolicyError("provider endpoint hostname is invalid")


def validate_provider_url(
    url: str | None,
    *,
    allow_private: bool = False,
    allowlist: tuple[str, ...] = (),
) -> None:
    """Validate syntax and reject obvious SSRF targets without doing I/O."""
    if not url:
        return
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ProviderURLPolicyError("provider endpoint is not a valid URL") from exc
    if parsed.scheme not in ({"https", "http"} if allow_private else {"https"}):
        raise ProviderURLPolicyError("provider endpoint must use HTTPS")
    if not parsed.hostname:
        raise ProviderURLPolicyError("provider endpoint must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ProviderURLPolicyError("provider endpoint must not include credentials")
    if parsed.fragment or parsed.query:
        raise ProviderURLPolicyError("provider endpoint must not include query or fragment data")
    if port is not None and not 1 <= port <= 65535:
        raise ProviderURLPolicyError("provider endpoint has an invalid port")

    host = parsed.hostname.lower().rstrip(".")
    _validate_hostname(host)
    _reject_obfuscated_ip(host, allow_private=allow_private)
    if not _host_allowed(host, allowlist):
        raise ProviderURLPolicyError("provider endpoint host is not in the allowlist")
    if not allow_private and (
        host == "localhost"
        or host.endswith(".localhost")
        or host.endswith(".local")
        or host.endswith(".internal")
    ):
        raise ProviderURLPolicyError("provider endpoint host is not public")
    _reject_non_public_address(host, allow_private=allow_private)


async def validate_provider_url_resolved(
    url: str | None,
    *,
    allow_private: bool = False,
    allowlist: tuple[str, ...] = (),
) -> None:
    """Resolve a provider host and reject private/reserved destinations."""
    validate_provider_url(url, allow_private=allow_private, allowlist=allowlist)
    if not url or allow_private:
        return
    parsed = urlsplit(url)
    assert parsed.hostname is not None
    try:
        records = await asyncio.to_thread(
            socket.getaddrinfo,
            parsed.hostname,
            parsed.port or 443,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise ProviderURLPolicyError("provider endpoint hostname cannot be resolved") from exc
    addresses = {record[4][0] for record in records}
    if not addresses:
        raise ProviderURLPolicyError("provider endpoint hostname has no addresses")
    for address in addresses:
        _reject_non_public_address(cast(str, address), allow_private=False)


class _ValidatedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Resolve, validate, then connect directly to one vetted address."""

    def __init__(self, *, allow_private: bool) -> None:
        self._backend = httpcore.AnyIOBackend()
        self._allow_private = allow_private

    async def connect_tcp(  # noqa: ASYNC109
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109
        local_address: str | None = None,
        socket_options: object = None,
    ) -> httpcore.AsyncNetworkStream:  # noqa: ASYNC109
        try:
            parsed = ipaddress.ip_address(host)
        except ValueError:
            parsed = None
        if parsed is not None:
            _reject_non_public_address(host, allow_private=self._allow_private)
            addresses = [host]
        else:
            records = await asyncio.to_thread(
                socket.getaddrinfo,
                host,
                port,
                type=socket.SOCK_STREAM,
            )
            addresses = list(dict.fromkeys(cast(str, record[4][0]) for record in records))
            if not addresses:
                raise ProviderURLPolicyError("provider endpoint hostname has no addresses")
            for address in addresses:
                _reject_non_public_address(address, allow_private=self._allow_private)
        last_error: Exception | None = None
        for address in addresses:
            try:
                return await self._backend.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,  # type: ignore[arg-type]
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout, OSError) as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    async def connect_unix_socket(  # noqa: ASYNC109
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109
        socket_options: object = None,  # noqa: ASYNC109
    ) -> httpcore.AsyncNetworkStream:  # noqa: ASYNC109
        raise ProviderURLPolicyError("provider endpoint cannot use a Unix socket")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


def provider_http_client(
    *, allow_private: bool = False, timeout: float | None = None
) -> httpx.AsyncClient:
    """Create an HTTP client whose actual connection cannot rebind to a private IP."""
    transport = httpx.AsyncHTTPTransport(trust_env=False)
    transport._pool._network_backend = _ValidatedNetworkBackend(  # type: ignore[attr-defined]
        allow_private=allow_private
    )
    return httpx.AsyncClient(
        transport=transport,
        follow_redirects=False,
        trust_env=False,
        timeout=timeout,
    )


@dataclass(frozen=True)
class SecretCipher:
    """Versioned Fernet encryption with read compatibility for legacy plaintext."""

    _fernet: Fernet | None
    _PREFIX = "enc:v1:"

    @classmethod
    def from_env(cls) -> SecretCipher:
        raw = os.getenv("CATALOG_ENCRYPTION_KEY", "").strip()
        if not raw:
            return cls(None)
        try:
            return cls(Fernet(raw.encode("ascii")))
        except (ValueError, UnicodeEncodeError) as exc:
            raise RuntimeError("CATALOG_ENCRYPTION_KEY is not a valid Fernet key") from exc

    @property
    def enabled(self) -> bool:
        return self._fernet is not None

    def encrypt(self, value: str) -> str:
        if not value or self._fernet is None:
            return value
        token = self._fernet.encrypt(value.encode("utf-8")).decode("ascii")
        return f"{self._PREFIX}{token}"

    def decrypt(self, value: str) -> str:
        if not value or not value.startswith(self._PREFIX):
            return value
        if self._fernet is None:
            raise RuntimeError("catalog contains encrypted secrets but no encryption key is set")
        token = value.removeprefix(self._PREFIX)
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError) as exc:
            raise RuntimeError("catalog secret cannot be decrypted") from exc
