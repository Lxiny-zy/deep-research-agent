"""Completion decisions preserve lease ownership and compare immutable versions."""

from __future__ import annotations

import asyncio
import os
from copy import deepcopy

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from deep_research.orchestrator import create_initial_execution
from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.persistence.repository import LeaseLostError
from deep_research.persistence.sql_repository import SqlRepository


@pytest.fixture(params=["memory", "sqlite", pytest.param("postgresql", marks=pytest.mark.pg)])
async def repo(request, tmp_path):
    if request.param == "memory":
        yield InMemoryRepository()
        return
    if request.param == "postgresql":
        from tests.test_migrations_pg import _isolated_database, _run_migration

        url = os.getenv("DATABASE_URL", "")
        if not url.startswith("postgresql+asyncpg://"):
            pytest.skip("未配置 PostgreSQL DATABASE_URL")
        async with _isolated_database(url) as database_url:
            await _run_migration(database_url, "head")
            engine = make_engine(database_url)
            try:
                yield SqlRepository(make_sessionmaker(engine))
            finally:
                await engine.dispose()
        return
    # Separate connections exercise real transaction serialization for the CAS test.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'completion.sqlite'}")
    await create_all(engine)
    try:
        yield SqlRepository(make_sessionmaker(engine))
    finally:
        await engine.dispose()


def record(status="needs_review", version="a", source="b"):
    return {
        "policy_version": 1,
        "status": status,
        "input_version": source * 64,
        "content_version": version * 64,
        "required_formats": ["md", "pdf"],
        "issues": ["PDF needs retry"] if status == "needs_review" else [],
    }


async def started(repo, settings, owner="original"):
    execution = create_initial_execution("Q", "quick", settings)
    run_id = await repo.create_run("Q", execution=execution, lease_owner=owner)
    await repo.set_status(run_id, "running", lease_owner=owner)
    return run_id


@pytest.mark.parametrize("owner", [None, "original"])
async def test_finalize_persists_completion_with_and_without_execution_lease(repo, settings, owner):
    run_id = await started(repo, settings, owner)
    completion = record()
    await repo.finalize(
        run_id, elapsed=12.5, total_tokens=37, lease_owner=owner, completion=completion
    )
    detail = await repo.get_run(run_id)
    assert detail.status == "needs_review"
    assert detail.elapsed == 12.5 and detail.total_tokens == 37
    assert detail.orchestration.checkpoint["scratch"]["_completion"] == completion


async def test_cancellation_wins_over_late_quality_completion(repo, settings):
    run_id = await started(repo, settings)
    assert await repo.request_cancel(run_id)
    await repo.finalize(
        run_id, elapsed=10, total_tokens=20, lease_owner="original", completion=record("done")
    )
    detail = await repo.get_run(run_id)
    assert detail.status == "cancelling"
    assert "_completion" not in detail.orchestration.checkpoint["scratch"]


async def test_lost_owner_cannot_publish_terminal_status_or_completion(repo, settings):
    run_id = await started(repo, settings)
    await repo.release_lease(run_id, "original")
    assert await repo.acquire_lease(run_id, "successor")
    with pytest.raises(LeaseLostError):
        await repo.finalize(
            run_id, elapsed=10, total_tokens=20, lease_owner="original", completion=record("done")
        )
    detail = await repo.get_run(run_id)
    assert detail.status == "running"
    assert detail.elapsed == 0 and detail.total_tokens == 0
    assert "_completion" not in detail.orchestration.checkpoint["scratch"]


async def test_invalid_completion_does_not_partially_change_metrics(repo, settings):
    run_id = await started(repo, settings)
    with pytest.raises(ValueError):
        await repo.finalize(
            run_id,
            elapsed=55,
            total_tokens=99,
            lease_owner="original",
            completion=record("not-a-terminal-state"),
        )
    detail = await repo.get_run(run_id)
    assert detail.status == "running"
    assert detail.elapsed == 0 and detail.total_tokens == 0
    assert "_completion" not in detail.orchestration.checkpoint["scratch"]


@pytest.mark.parametrize("operation", ["finalize", "update"])
async def test_completion_record_does_not_retain_mutable_caller_aliases(repo, settings, operation):
    run_id = await started(repo, settings)
    completion = record()
    expected = deepcopy(completion)
    await repo.finalize(
        run_id, elapsed=1, total_tokens=2, lease_owner="original", completion=completion
    )
    if operation == "update":
        completion = record("needs_review", version="c")
        expected = deepcopy(completion)
        assert await repo.update_completion(run_id, completion, expected_version="a" * 64)
    completion["issues"].append("caller mutation")
    completion["required_formats"].append("unpromised-format")
    detail = await repo.get_run(run_id)
    assert detail.orchestration.checkpoint["scratch"]["_completion"] == expected


@pytest.mark.parametrize("conflict", ["version", "source"])
async def test_retry_cas_rejects_stale_version_or_changed_source(repo, settings, conflict):
    run_id = await started(repo, settings)
    original = record()
    await repo.finalize(
        run_id, elapsed=1, total_tokens=2, lease_owner="original", completion=original
    )
    changed = record("done", version="c", source="d" if conflict == "source" else "b")
    expected = ("d" if conflict == "version" else "a") * 64
    assert not await repo.update_completion(run_id, changed, expected_version=expected)
    detail = await repo.get_run(run_id)
    assert detail.status == "needs_review"
    assert detail.orchestration.checkpoint["scratch"]["_completion"] == original


async def test_concurrent_completion_updates_have_one_version_winner(repo, settings):
    run_id = await started(repo, settings)
    await repo.finalize(
        run_id, elapsed=1, total_tokens=2, lease_owner="original", completion=record()
    )
    candidates = [record("done", version="c"), record("needs_review", version="d")]
    results = await asyncio.gather(
        *(
            repo.update_completion(run_id, candidate, expected_version="a" * 64)
            for candidate in candidates
        )
    )
    assert sum(results) == 1
    winner = candidates[results.index(True)]
    detail = await repo.get_run(run_id)
    assert detail.status == winner["status"]
    assert detail.orchestration.checkpoint["scratch"]["_completion"] == winner


@pytest.mark.parametrize("status", ["running", "cancelling", "cancelled", "error"])
async def test_completion_retry_cannot_replace_non_delivery_terminal_decisions(
    repo, settings, status
):
    run_id = await started(repo, settings)
    initial_record = record()
    await repo.finalize(
        run_id, elapsed=1, total_tokens=2, lease_owner="original", completion=initial_record
    )
    await repo.set_status(run_id, status, lease_owner="original")
    assert not await repo.update_completion(
        run_id, record("done", version="c"), expected_version="a" * 64
    )
    detail = await repo.get_run(run_id)
    assert detail.status == status
    assert detail.orchestration.checkpoint["scratch"]["_completion"] == initial_record
