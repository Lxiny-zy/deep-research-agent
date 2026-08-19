from __future__ import annotations

import json
import socket

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from deep_research import runtime_config
from deep_research.catalog.repository import CatalogRepository
from deep_research.config import Settings
from deep_research.llm import LLM
from deep_research.observability import Tracer
from deep_research.persistence import orm
from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
from deep_research.security import (
    ProviderURLPolicyError,
    SecretCipher,
    provider_http_client,
    validate_provider_url,
    validate_provider_url_resolved,
)


def test_provider_url_policy_rejects_ssrf_targets() -> None:
    for url in (
        "http://api.example.com/v1",
        "https://localhost/v1",
        "https://127.0.0.1/v1",
        "https://169.254.169.254/latest/meta-data",
        "https://user:pass@api.example.com/v1",
        "https://api.example.com/v1?target=internal",
    ):
        with pytest.raises(ProviderURLPolicyError):
            validate_provider_url(url)

    for obfuscated in (
        "https://2130706433/v1",
        "https://0x7f000001/v1",
        "https://127.1/v1",
        "https://0/v1",
    ):
        with pytest.raises(ProviderURLPolicyError):
            validate_provider_url(obfuscated)

    for malformed in (
        "https://127.0.0.1 /v1",
        "https://127.0.0.1%20/v1",
        "https://127。0。0。1/v1",
        "https://１２７.０.０.１/v1",
    ):
        with pytest.raises(ProviderURLPolicyError):
            validate_provider_url(malformed)

    validate_provider_url("https://api.example.com/v1", allowlist=("*.example.com",))
    with pytest.raises(ProviderURLPolicyError):
        validate_provider_url("https://api.other.test/v1", allowlist=("*.example.com",))
    validate_provider_url("http://127.0.0.1:11434/v1", allow_private=True)


@pytest.mark.asyncio
async def test_resolved_provider_url_rejects_private_dns(monkeypatch) -> None:
    def private_result(*args, **kwargs):  # type: ignore[no-untyped-def]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", private_result)
    with pytest.raises(ProviderURLPolicyError):
        await validate_provider_url_resolved("https://provider.example/v1")


@pytest.mark.asyncio
async def test_provider_transport_rechecks_dns_at_connection_time(monkeypatch) -> None:
    def private_result(*args, **kwargs):  # type: ignore[no-untyped-def]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", private_result)
    client = provider_http_client()
    try:
        with pytest.raises(ProviderURLPolicyError):
            await client.get("https://provider.example/v1")
    finally:
        await client.aclose()


def test_runtime_config_scrubs_legacy_plaintext_secrets(tmp_path, monkeypatch) -> None:
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"llm_api_key": "secret", "max_rounds": 3}), encoding="utf-8")
    monkeypatch.setenv("RUNTIME_CONFIG_PATH", str(path))

    assert runtime_config.load_overrides() == {"max_rounds": 3}
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted == {"max_rounds": 3}


def test_production_settings_fail_fast(monkeypatch) -> None:
    monkeypatch.delenv("CATALOG_ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError, match="API_KEY"):
        Settings(app_env="production").validate_deployment()

    monkeypatch.setenv("CATALOG_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(RuntimeError, match="API_KEY"):
        Settings(
            app_env="production",
            api_key="   ",
            database_url="postgresql+asyncpg://service@db/research",
        ).validate_deployment()

    settings = Settings(
        app_env="production",
        api_key="api-secret",
        database_url="postgresql+asyncpg://service@db/research",
    )
    settings.validate_deployment()


def test_secret_cipher_encrypts_plaintext_that_uses_the_storage_prefix() -> None:
    cipher = SecretCipher(Fernet(Fernet.generate_key()))
    plaintext = "enc:v1:provider-issued-secret"

    encrypted = cipher.encrypt(plaintext)

    assert encrypted != plaintext
    assert encrypted.startswith("enc:v1:")
    assert cipher.decrypt(encrypted) == plaintext


def test_secret_cipher_normalizes_non_ascii_corrupt_ciphertext() -> None:
    cipher = SecretCipher(Fernet(Fernet.generate_key()))

    with pytest.raises(RuntimeError, match="catalog secret cannot be decrypted"):
        cipher.decrypt("enc:v1:损坏")


@pytest.mark.asyncio
async def test_llm_client_does_not_follow_provider_redirects() -> None:
    settings = Settings(llm_api_key="test-key", llm_base_url="https://api.example.com/v1")
    client = LLM(settings, Tracer())
    try:
        assert client.client._client.follow_redirects is False
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_catalog_secrets_are_encrypted_at_rest(monkeypatch) -> None:
    monkeypatch.setenv("CATALOG_ENCRYPTION_KEY", Fernet.generate_key().decode())
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    sessionmaker = make_sessionmaker(engine)
    catalog = CatalogRepository(sessionmaker, secret_cipher=SecretCipher.from_env())
    try:
        profile = await catalog.create_profile(
            name="encrypted",
            base_url="https://api.example.com/v1",
            api_key="profile-secret",
            model="model",
            temperature=0.2,
            is_default=True,
        )
        await catalog.create_key(label="search", api_key="search-secret", priority=1, enabled=True)
        async with sessionmaker() as session:
            profile_raw = await session.scalar(
                select(orm.ModelProfileRow.api_key).where(orm.ModelProfileRow.id == profile.id)
            )
            search_raw = await session.scalar(select(orm.SearchKeyRow.api_key))

        assert profile_raw is not None and profile_raw.startswith("enc:v1:")
        assert search_raw is not None and search_raw.startswith("enc:v1:")
        assert "profile-secret" not in profile_raw
        assert "search-secret" not in search_raw
        full = await catalog.get_profile_full(profile.id)
        assert full is not None and full.api_key == "profile-secret"
        assert await catalog.active_keys() == ["search-secret"]
    finally:
        await engine.dispose()
