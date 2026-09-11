"""Shared SQLite transactions exercise the same repository contract as PostgreSQL."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import replace

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, update

from deep_research import artifact_lifecycle
from deep_research.config import Settings
from deep_research.config_service import ConfigConflictError, ConfigStore
from deep_research.orchestration import OrchestrationRuntime
from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
from deep_research.persistence.orm import ProviderLeaseRow, RuntimeConfigRow
from deep_research.persistence.repository import RunQueueFullError
from deep_research.persistence.sql_repository import SqlRepository
from deep_research.provider_limits import ProviderBusyError, ProviderCoordinator, fingerprint
from deep_research.security import SecretCipher


@pytest.fixture(params=["sqlite", pytest.param("postgresql", marks=pytest.mark.pg)])
async def sessions(tmp_path, request):
    if request.param == "postgresql":
        from tests.test_migrations_pg import _isolated_database, _run_migration

        url = os.getenv("DATABASE_URL", "")
        if not url.startswith("postgresql+asyncpg://"):
            pytest.skip("未配置 PostgreSQL DATABASE_URL")
        async with _isolated_database(url) as database_url:
            await _run_migration(database_url, "head")
            engine = make_engine(database_url)
            try:
                yield make_sessionmaker(engine)
            finally:
                await engine.dispose()
        return
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'service.sqlite'}")
    await create_all(engine)
    yield make_sessionmaker(engine)
    await engine.dispose()


def _execution():
    execution = OrchestrationRuntime().start("deep", {"query": "same topic"})
    execution.checkpoint = {
        "scratch": {"_artifact_run_scoped": True, "_artifact_slug": "same-topic"}
    }
    return execution


@pytest.mark.asyncio
async def test_concurrent_repositories_enforce_shared_queue_and_execution_limits(sessions):
    async def enqueue(index):
        return await SqlRepository(sessions).create_run_once(
            f"query {index}",
            request_hash=str(index),
            idempotency_key=str(index),
            execution=_execution(),
            claimable=True,
            max_inflight=3,
        )

    results = await asyncio.gather(*(enqueue(i) for i in range(9)), return_exceptions=True)
    assert len([item for item in results if isinstance(item, tuple)]) == 3
    assert len([item for item in results if isinstance(item, RunQueueFullError)]) == 6
    claims = await asyncio.gather(
        *(
            SqlRepository(sessions).claim_next_run(f"worker-{i}", max_active_runs=2)
            for i in range(6)
        )
    )
    assert len([claim for claim in claims if claim is not None]) == 2
    assert len({claim.run_id for claim in claims if claim is not None}) == 2


@pytest.mark.asyncio
async def test_same_idempotency_key_wins_once_even_at_capacity(sessions):
    async def submit():
        return await SqlRepository(sessions).create_run_once(
            "query", request_hash="body", idempotency_key="same", max_inflight=1
        )

    results = await asyncio.gather(*(submit() for _ in range(6)))
    assert len({run_id for run_id, _ in results}) == 1
    assert sum(created for _, created in results) == 1


async def test_concurrent_default_models_keep_one_default_from_empty_catalog(sessions):
    from deep_research.catalog.repository import CatalogRepository

    async def create(index):
        return await CatalogRepository(sessions).create_profile(
            name=f"model-{index}",
            base_url=None,
            api_key="synthetic",
            model="test",
            temperature=0.3,
            is_default=True,
        )

    profiles = await asyncio.gather(*(create(index) for index in range(4)))
    assert len({profile.id for profile in profiles}) == 4
    saved = await CatalogRepository(sessions).list_profiles()
    assert sum(profile.is_default for profile in saved) == 1


@pytest.mark.asyncio
async def test_configuration_is_encrypted_shared_and_compare_and_swap(sessions):
    cipher = SecretCipher(Fernet(Fernet.generate_key()))
    api_store, worker_store = ConfigStore(sessions, cipher), ConfigStore(sessions, cipher)
    base = Settings(llm_api_key="", llm_model="old-model")
    configured = replace(base, llm_api_key="synthetic-provider-secret", llm_model="shared-model")
    saved = await api_store.save(configured, expected_version=0, secrets_changed=True)
    worker = await worker_store.load(base)
    assert worker.llm_api_key == configured.llm_api_key
    assert worker.llm_model == "shared-model" and worker.runtime_config_version == 1
    async with sessions() as session:
        raw = (await session.scalars(select(RuntimeConfigRow))).one().values
        assert "synthetic-provider-secret" not in str(raw)
        assert raw["llm_api_key"].startswith("enc:v1:")
    updates = await asyncio.gather(
        *(
            ConfigStore(sessions, cipher).save(
                replace(saved, max_rounds=rounds), expected_version=1
            )
            for rounds in (2, 3)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(item, Settings) for item in updates) == 1
    assert sum(isinstance(item, ConfigConflictError) for item in updates) == 1
    restored = await ConfigStore(sessions, cipher).load(base)
    assert restored.runtime_config_version == 2
    assert restored.llm_api_key == configured.llm_api_key
    with pytest.raises(ValueError, match="CATALOG_ENCRYPTION_KEY"):
        await ConfigStore(sessions, SecretCipher(None)).save(
            configured, expected_version=2, secrets_changed=True
        )


@pytest.mark.asyncio
async def test_artifact_delete_is_run_scoped_durable_and_retryable(sessions, tmp_path, monkeypatch):
    repo = SqlRepository(sessions)
    settings = Settings(artifact_root=str(tmp_path / "artifacts"))
    roots = []
    ids = []
    for _ in range(2):
        run_id = await repo.create_run("same topic", execution=_execution())
        await repo.set_status(run_id, "error")
        root = tmp_path / "artifacts" / "runs" / run_id
        root.mkdir(parents=True)
        (root / "result.md").write_text("research content", encoding="utf-8")
        roots.append(root)
        ids.append(run_id)
    await repo.delete_run(ids[0])
    assert await repo.pending_artifact_cleanup() == [(ids[0], f"runs/{ids[0]}")]
    remove = artifact_lifecycle._remove_run

    def fail(*args):
        raise PermissionError("temporary file lock")

    monkeypatch.setattr(artifact_lifecycle, "_remove_run", fail)
    await artifact_lifecycle.cleanup_artifacts(repo, settings)
    assert roots[0].exists() and await repo.pending_artifact_cleanup()
    monkeypatch.setattr(artifact_lifecycle, "_remove_run", remove)
    await artifact_lifecycle.cleanup_artifacts(SqlRepository(sessions), settings)
    assert not roots[0].exists() and roots[1].exists()
    assert await repo.pending_artifact_cleanup() == []


@pytest.mark.asyncio
async def test_worker_health_expires_and_queue_metrics_are_shared(sessions):
    first, second = SqlRepository(sessions), SqlRepository(sessions)
    await first.heartbeat_worker("worker-one", 0)
    await first.create_run_once("queued", request_hash="q", execution=_execution(), claimable=True)
    status = await second.service_status()
    assert status["workers"] == 1 and status["queued"] == 1
    assert status["oldest_queued_seconds"] >= 0
    assert (await second.service_status(heartbeat_seconds=0))["workers"] == 0
    await first.remove_worker("worker-one")
    assert (await second.service_status())["workers"] == 0


@pytest.mark.asyncio
async def test_provider_concurrency_and_expired_leases_are_shared(sessions):
    first = ProviderCoordinator(sessions, concurrency=1, wait_seconds=1)
    second = ProviderCoordinator(sessions, concurrency=1, wait_seconds=1)
    key = fingerprint("https://provider.example/v1", "synthetic-key")
    assert key == fingerprint("https://provider.example/v2/model", "synthetic-key")
    lease = await first.acquire(key)
    waiting = asyncio.create_task(second.acquire(key))
    await asyncio.sleep(0.05)
    assert not waiting.done()
    await first.release(lease)
    next_lease = await waiting
    assert next_lease != lease
    async with sessions() as session, session.begin():
        await session.execute(update(ProviderLeaseRow).values(expires_at=time.time() - 1))
    recovered = await first.acquire(key)
    assert recovered != next_lease and not await second.renew(next_lease)
    await first.release(recovered)


@pytest.mark.asyncio
async def test_provider_cooldown_request_quota_and_cancellation(sessions):
    first = ProviderCoordinator(sessions, concurrency=1, requests_per_minute=1)
    second = ProviderCoordinator(sessions, concurrency=1, requests_per_minute=1)
    key = fingerprint("https://provider.example", "synthetic-key")
    response = httpx.Response(
        429,
        headers={"Retry-After": "90"},
        request=httpx.Request("POST", "https://provider.example"),
    )
    with pytest.raises(httpx.HTTPStatusError):
        async with first.slot(key):
            response.raise_for_status()
    with pytest.raises(ProviderBusyError) as cooling:
        await second.acquire(key)
    assert cooling.value.retry_after >= 89
    status = await SqlRepository(sessions).service_status()
    assert status["provider_requests"] == status["provider_limited"] == 1
    quota_key = fingerprint("https://provider.example", "another-key")
    lease = await first.acquire(quota_key)
    await first.release(lease)
    with pytest.raises(ProviderBusyError):
        await second.acquire(quota_key)
    cancellation_key = fingerprint("https://provider.example", "cancelled-key")
    started = asyncio.Event()

    async def cancelled_request():
        async with first.slot(cancellation_key):
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(cancelled_request())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with sessions() as session:
        assert not list(await session.scalars(select(ProviderLeaseRow)))


@pytest.mark.asyncio
async def test_http_admission_cannot_be_bypassed_by_another_api_process(sessions):
    accepted = await asyncio.gather(
        *(
            ProviderCoordinator(sessions).admit_request("create:alice", limit=3, window=60)
            for _ in range(8)
        )
    )
    assert sum(accepted) == 3
