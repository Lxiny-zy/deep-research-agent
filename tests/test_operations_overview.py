from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from deep_research import api
from deep_research.access import ApiCredential, Principal
from deep_research.config import Settings
from deep_research.persistence import orm
from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
from deep_research.workbench.operations_source import sql_snapshot
from deep_research.workbench.operations_summary import summarize
from deep_research.workbench.qa_store import SqlQaStore

ADMIN = "operations-local-administrator"
ALICE = "operations-local-alice-key"
BOB = "operations-local-bob-key"


async def seed(sessionmaker):
    now = datetime.now(UTC)
    async with sessionmaker() as session:
        for rid, owner, status, old in [
            ("alice-run", "alice", "needs_review", False),
            ("alice-recovery", "alice", "done", False),
            ("bob-run", "bob", "done", False),
            ("old-run", "alice", "done", True),
        ]:
            session.add(orm.ResearchRun(
                id=rid, owner_id=owner, query="private research prompt", status=status,
                created_at=now - timedelta(days=10) if old else now, elapsed=10,
            ))
            scratch = {"workbench": {"template": "paperRead"}}
            if rid == "alice-recovery":
                scratch["content_revision"] = {"parent_run_id": "alice-run"}
            if status == "needs_review":
                scratch["_completion"] = {"gates": [{
                    "name": "prose_evidence", "status": "fail", "issues": ["private evidence"],
                }]}
            session.add(orm.WorkflowRunRow(
                id="workflow-" + rid, research_run_id=rid,
                workflow_name="test", status="done", attempt=1,
                input={}, output={}, definition={}, checkpoint={"scratch": scratch},
            ))
        await session.flush()
        for seq, status in [(1, "started"), (2, "succeeded"), (3, "started")]:
            session.add(orm.EventRow(
                run_id="alice-run", seq=seq, attempt=1, stage="LLM", type="info",
                message="private event message", elapsed=1, tokens=12, tokens_estimated=False,
                data={"model_call": {
                    "call_id": "call-a", "status": status, "duration_ms": 100,
                    "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
                }},
            ))
        session.add(orm.QaConversationRow(id="conversation-a", owner_id="alice", title="private"))
        await session.flush()
        session.add(orm.QaMessageRow(
            id="message-a", conversation_id="conversation-a", position=0,
            query="private question", answer="private answer",
            status="error", request_id="request-a",
            request_payload={"private": "not-for-statistics"}, created_at=now,
            thoughts=[
                {"tool": "model_reasoning", "observation": "private reasoning"},
                {"tool": "model_call", "call": {
                    "call_id": "call-b", "status": "failed", "retry_reason": "transport_retry",
                    "usage_state": "unavailable", "duration_ms": 200,
                }},
            ],
        ))
        await session.flush()
        session.add(orm.QaStreamEventRow(
            message_id="message-a", sequence=1, kind="model_call",
            payload={"model_call": {"call_id": "call-b", "status": "started"}},
        ))
        session.add(orm.RenderJobRow(
            run_id="alice-run", key="render-key", pool="local", kind="pdf", payload={},
            payload_hash="render-hash", status="pending", created_at=time.time(),
            queued_at=time.time() - 30, available_at=time.time(), attempts=1, stalls=0,
        ))
        await session.commit()
    return now


@pytest.fixture
async def operations_client(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'operations.db'}")
    await create_all(engine)
    sessions = make_sessionmaker(engine)
    try:
        await seed(sessions)
    except BaseException:
        await engine.dispose()
        raise
    settings = Settings(api_key=ADMIN, api_credentials=(
        ApiCredential(Principal("alice", "researcher"), ALICE),
        ApiCredential(Principal("bob", "reader"), BOB),
    ))
    monkeypatch.setattr(api.app.state, "settings", settings, raising=False)
    monkeypatch.setattr(api.app.state, "repo", SimpleNamespace(_sm=sessions), raising=False)
    monkeypatch.setattr(api.app.state, "qa_store", SqlQaStore(sessions), raising=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test",
    ) as client:
        yield client, sessions
    await engine.dispose()


async def test_real_overview_is_owned_and_keeps_unknown_usage_and_recovery_separate(
    operations_client,
):
    client, _ = operations_client
    response = await client.get("/api/operations", headers={"X-API-Key": ALICE})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["coverage"]["research_records"] == 2
    assert value["coverage"]["qa_records"] == 1
    assert not value["coverage"]["truncated"]
    calls = value["model_calls"]
    assert calls["recorded_attempts"] == 2 and calls["retries"] == 1
    assert calls["statuses"] == {"succeeded": 1, "failed": 1}
    assert calls["usage"]["total_tokens"] == {
        "known_total": 12, "reported_calls": 1, "unknown_calls": 1,
    }
    assert calls["usage"]["reasoning_tokens"]["known_total"] is None
    assert calls["cost"] is None
    group = next(group for group in value["scenarios"] if group["scenario"] == "paperRead")
    assert group["initial_done"] == 0
    assert group["origins"] == {"initial": 1, "continuation": 1}
    assert value["needs_review_reasons"] == {"prose_evidence": 1}
    assert value["rendering"]["statuses"] == {"pending": 1}
    assert value["rendering"]["oldest_pending_seconds"] >= 30
    assert "private" not in response.text
    assert response.headers["cache-control"] == "private, no-store"


async def test_workspace_scope_requires_admin_and_anonymous_requests_fail(operations_client):
    client, _ = operations_client
    assert (await client.get("/api/operations")).status_code == 401
    forbidden = await client.get("/api/operations?scope=workspace", headers={"X-API-Key": ALICE})
    assert forbidden.status_code == 403
    bob = await client.get("/api/operations", headers={"X-API-Key": BOB})
    assert bob.json()["coverage"]["research_records"] == 1
    assert bob.json()["model_calls"]["recorded_attempts"] == 0
    admin = await client.get("/api/operations?scope=workspace", headers={"X-API-Key": ADMIN})
    assert admin.status_code == 200 and admin.json()["coverage"]["research_records"] == 3


async def test_large_windows_are_explicitly_partial_instead_of_claimed_complete(
    operations_client, monkeypatch,
):
    from deep_research.workbench import operations_source

    _, sessions = operations_client
    monkeypatch.setattr(operations_source, "MAX_ITEMS", 1)
    until = datetime.now(UTC)
    data = await sql_snapshot(sessions, owner="alice", since=until - timedelta(days=7), until=until)
    result = summarize(data, since=until - timedelta(days=7), until=until)
    assert result["coverage"]["truncated"]
    assert result["coverage"]["research_records"] == 1


async def test_memory_adapter_uses_real_creation_dates_and_does_not_fabricate_usage():
    from deep_research.persistence.memory_repository import InMemoryRepository
    from deep_research.workbench.operations_source import memory_snapshot
    from deep_research.workbench.qa_store import InMemoryQaStore

    repo = InMemoryRepository()
    run_id, _ = await repo.create_run_once("private", owner_id="alice", request_hash="")
    await repo.set_status(run_id, "done")
    until = datetime.now(UTC)
    data = await memory_snapshot(
        repo, InMemoryQaStore(), owner="alice", since=until - timedelta(days=1), until=until,
    )
    result = summarize(data, since=until - timedelta(days=1), until=until)
    assert result["coverage"]["research_records"] == 1
    assert result["coverage"]["unknown_date_records"] == 0
    assert result["model_calls"]["usage"]["total_tokens"]["known_total"] is None
    assert result["rendering"] is None
    assert result["scenarios"][0]["origins"] == {"unknown": 1}


def test_later_done_is_not_counted_as_first_success_without_matching_original_result():
    now = datetime.now(UTC)
    row = {
        "id": "run", "scenario": "paperRead", "status": "done", "attempt": 1,
        "created_at": now, "completion": {"first_result": {
            "version": 1, "run_id": "run", "status": "needs_review",
        }},
    }
    data = {
        "backend": "memory", "runs": [row], "qa": [], "calls": [], "rendering": None,
        "truncated": False, "unknown_dates": 0,
    }
    result = summarize(data, since=now - timedelta(days=1), until=now)
    assert result["scenarios"][0]["initial_done"] == 0
    assert result["coverage"]["unknown_research_first_results"] == 0
    row["completion"] = {"first_result": {"version": 1, "run_id": "other", "status": "done"}}
    result = summarize(data, since=now - timedelta(days=1), until=now)
    assert result["scenarios"][0]["initial_done"] == 0
    assert result["coverage"]["unknown_research_first_results"] == 1
