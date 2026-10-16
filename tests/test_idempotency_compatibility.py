"""Historical retries must never relax hashing for newly accepted requests."""

import pytest
from sqlalchemy import update

from deep_research.api import CreateRunRequest, _run_request_hash
from deep_research.http.idempotency import legacy_template_update
from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.persistence.orm import ResearchRun
from deep_research.persistence.repository import IdempotencyConflictError
from deep_research.persistence.sql_repository import SqlRepository


@pytest.fixture(params=["memory", "sqlite"])
async def repo_and_mark_legacy(request, tmp_path):
    if request.param == "memory":
        repo = InMemoryRepository()

        async def mark(run_id):
            repo._runs[run_id].request_hash_version = 1

        yield repo, mark
        return
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'compat.db'}")
    await create_all(engine)
    sessions = make_sessionmaker(engine)

    async def mark(run_id):
        async with sessions() as session, session.begin():
            await session.execute(
                update(ResearchRun).where(ResearchRun.id == run_id).values(request_hash_version=1)
            )

    try:
        yield SqlRepository(sessions), mark
    finally:
        await engine.dispose()


@pytest.mark.parametrize("template,strategy", [
    ("autoResearch", "quick"), ("litReview", None), ("paperRead", "none"),
])
async def test_only_legacy_same_owner_template_retries_are_compatible(
    repo_and_mark_legacy, template, strategy,
):
    repo, mark = repo_and_mark_legacy
    request = CreateRunRequest(query="original query", template=template, strategy=strategy)
    changes = legacy_template_update(request.template, request.strategy, request.workflow)
    old_hash = _run_request_hash(request.model_copy(update=changes))
    new_hash = _run_request_hash(request)
    run_id, _ = await repo.create_run_once(
        request.query, request_hash=old_hash, idempotency_key="retry", owner_id="alice",
    )
    # A v2 record must not use legacy interpretation, even if its stored body
    # happens to equal the template-expanded shape of a different submission.
    with pytest.raises(IdempotencyConflictError):
        await repo.find_run_once("retry", new_hash, legacy_hash=old_hash, owner_id="alice")
    await mark(run_id)
    assert await repo.find_run_once(
        "retry", new_hash, legacy_hash=old_hash, owner_id="alice",
    ) == run_id
    with pytest.raises(IdempotencyConflictError):
        await repo.find_run_once("retry", new_hash, legacy_hash=old_hash, owner_id="bob")
    changed = request.model_copy(update={"query": "changed"})
    with pytest.raises(IdempotencyConflictError):
        await repo.find_run_once(
            "retry", _run_request_hash(changed),
            legacy_hash=_run_request_hash(changed.model_copy(update=changes)), owner_id="alice",
        )


def test_unsupported_or_ambiguous_workflows_do_not_get_compatibility_hash():
    assert legacy_template_update("custom", None, None) is None
    assert legacy_template_update("paperRead", "deep", None) is None
    assert legacy_template_update("autoResearch", None, "custom") is None
    assert legacy_template_update("litReview", "quick", "custom") is None
