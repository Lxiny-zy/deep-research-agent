from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
from deep_research.workbench.qa_context import dialogue_context
from deep_research.workbench.qa_requests import MemoryQaRequests, RequestConflict, SqlQaRequests
from deep_research.workbench.qa_store import InMemoryQaStore, SqlQaStore, message_payload


@pytest.fixture(params=["memory", "sql"])
async def stores(request, tmp_path):
    engine = None
    if request.param == "sql":
        engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'qa.db'}")
        await create_all(engine)
        sessions = make_sessionmaker(engine)
        store = SqlQaStore(sessions)
        jobs = SqlQaRequests(sessions)
        other = SqlQaRequests(sessions)
    else:
        store = InMemoryQaStore()
        jobs, other = MemoryQaRequests(store), MemoryQaRequests(store)
    cid = (await store.create("local", "test")).id
    yield store, jobs, other, cid
    if engine:
        await engine.dispose()


async def test_request_reservation_is_idempotent_and_rejects_changed_payload(stores):
    store, jobs, other, cid = stores
    rows = await asyncio.gather(
        *[
            (jobs if i % 2 else other).reserve(
                cid, "request-one", "hash", {"query": "q", "_actor": "local"}
            )
            for i in range(8)
        ]
    )
    assert len({row.id for row in rows}) == 1
    assert (await store.get(cid)).message_count == 1
    with pytest.raises(RequestConflict):
        await jobs.reserve(cid, "request-one", "changed", {"query": "different"})
    public = message_payload(rows[0])
    assert "_actor" not in public["request_payload"]
    assert "request_hash" not in public and "execution_owner" not in public


async def test_requests_are_claimed_in_order_and_old_owners_cannot_save(stores):
    store, jobs, other, cid = stores
    for rid in ("first-request", "second-request"):
        await jobs.reserve(cid, rid, rid, {"query": rid})
    assert not await other.claim(cid, "second-request", "owner-b", 90)
    assert await jobs.claim(cid, "first-request", "owner-a", 90)
    assert not await other.claim(cid, "first-request", "owner-b", 90)
    assert not await other.update(cid, "first-request", "owner-b", result={"answer": "wrong"})
    assert await jobs.update(cid, "first-request", "owner-a", seconds=90)
    assert await jobs.update(
        cid, "first-request", "owner-a", result={"answer": "saved", "tokens": 42}
    )
    assert await other.claim(cid, "second-request", "owner-b", 90)
    messages = (await store.get(cid)).messages
    assert [m.position for m in messages] == [0, 1]
    assert messages[0].answer == "saved" and messages[0].tokens == 42


async def test_expired_model_work_is_not_silently_reexecuted(stores):
    _, jobs, other, cid = stores
    now = datetime.now(UTC)
    await jobs.reserve(cid, "request-one", "hash", {"query": "q"})
    assert await jobs.claim(cid, "request-one", "old-owner", 1, now=now)
    later = now + timedelta(seconds=2)
    assert not await jobs.update(
        cid, "request-one", "old-owner", result={"answer": "late"}, now=later
    )
    assert (await other.get(cid, "request-one", now=later)).status == "error"
    assert (await jobs.reserve(cid, "request-one", "hash", {"query": "q"})).status == "error"
    assert not await jobs.claim(cid, "request-one", "new-owner", 90, now=later)


async def test_deletion_during_generation_rejects_late_updates(stores):
    store, jobs, other, cid = stores
    await jobs.reserve(cid, "request-one", "hash", {"query": "q"})
    assert await jobs.claim(cid, "request-one", "owner", 90)
    assert await store.delete(cid)
    assert await other.get(cid, "request-one") is None
    assert not await jobs.update(cid, "request-one", "owner", result={"answer": "late"})
    assert not await jobs.claim(cid, "request-one", "new-owner", 90)
    await jobs.reject_pending(cid, "request-one", "deleted")
    with pytest.raises(KeyError):
        await jobs.reserve(cid, "request-one", "hash", {"query": "q"})


async def test_api_turns_see_previous_completed_answers_and_duplicate_posts_reuse_result(
    stores, monkeypatch
):
    from starlette.requests import Request

    from deep_research import api
    from deep_research.config import Settings
    from deep_research.workbench import qa_api, qa_jobs

    store, _, _, cid = stores
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(state=SimpleNamespace(settings=Settings(), qa_store=store)),
        }
    )
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def answer(conversation_id, body, request, **kwargs):
        history = [
            (m.query, m.answer) for m in (await store.get(cid)).messages if m.status == "done"
        ]
        calls.append((body.query, history))
        if body.query == "first":
            entered.set()
            await release.wait()
        return {"answer": body.query + "-answer", "status": "done"}

    monkeypatch.setattr(api, "_check_rate_limit", AsyncMock())
    monkeypatch.setattr(qa_api, "_answer", answer)
    monkeypatch.setattr(qa_jobs, "POLL_SECONDS", 0.01)
    first_body = qa_api.AskRequest(query="first", request_id="first-request")
    first = asyncio.create_task(qa_api.ask(cid, first_body, request))
    await entered.wait()
    duplicate = asyncio.create_task(qa_api.ask(cid, first_body, request))
    second = asyncio.create_task(
        qa_api.ask(cid, qa_api.AskRequest(query="second", request_id="second-request"), request)
    )
    release.set()
    a, repeated, b = await asyncio.gather(first, duplicate, second)
    assert a["id"] == repeated["id"] and a["position"] == 0 and b["position"] == 1
    assert calls == [("first", []), ("second", [("first", "first-answer")])]


def test_context_keeps_full_recent_answers_and_more_than_four_turns():
    history = [
        {"query": f"问题 {i}", "answer": "正文" * 200 + f"第三点关键内容-{i}"} for i in range(7)
    ]
    context = dialogue_context(history, 10000)
    assert all(f"第三点关键内容-{i}" in context for i in range(7))
    with pytest.raises(ValueError, match="完整对话"):
        dialogue_context(history, 150)


def test_context_marks_omitted_turns_without_silently_slicing_an_answer():
    history = [
        {"query": "最初要求", "answer": "旧回答" * 1000},
        {"query": "追问", "answer": "完整的最新回答"},
    ]
    context = dialogue_context(history, 300)
    assert "完整的最新回答" in context and "最初要求" in context
    assert "未纳入" in context and len(context) <= 300


def test_context_keeps_contiguous_recent_turns():
    context = dialogue_context(
        [
            {"query": "最初要求", "answer": "旧的短回复"},
            {"query": "详细解释", "answer": "中间的长回复" * 1000},
            {"query": "确认", "answer": "最新回复"},
        ],
        350,
    )
    assert "最新回复" in context and "最初要求" in context
    assert "旧的短回复" not in context and "更早的 2 轮" in context


async def test_never_started_requests_resume_but_only_with_current_authorization(monkeypatch):
    from starlette.requests import Request

    from deep_research import api
    from deep_research.config import Settings
    from deep_research.workbench import qa_api
    from deep_research.workbench.qa_jobs import resume_pending

    store = InMemoryQaStore()
    cid = (await store.create("local", "resume")).id
    app = SimpleNamespace(
        state=SimpleNamespace(settings=Settings(api_key="", api_credentials=()), qa_store=store)
    )
    request = Request({"type": "http", "app": app})
    calls = []

    async def answer(*args, **kwargs):
        calls.append("model")
        return {"answer": "saved", "status": "done", "tokens": 5}

    monkeypatch.setattr(api, "_check_rate_limit", AsyncMock())
    monkeypatch.setattr(qa_api, "_answer", answer)
    runtime = await qa_api._prepare_turn(
        cid, qa_api.AskRequest(query="q", request_id="resume-request"), request
    )
    runtime.task.cancel()  # cancelled before the model coroutine ever starts
    await asyncio.gather(runtime.task, return_exceptions=True)
    assert calls == []
    await resume_pending(app)
    await asyncio.gather(*list(app.state.qa_tasks))
    assert calls == ["model"]
    assert (await store.get(cid)).messages[0].answer == "saved"
    repeated = await qa_api._prepare_turn(
        cid, qa_api.AskRequest(query="q", request_id="resume-request"), request
    )
    await repeated.task
    assert calls == ["model"]
    jobs = qa_api._requests(request)
    await jobs.reserve(cid, "revoked-request", "digest", {"query": "q2", "_actor": "revoked-user"})
    await resume_pending(app)
    assert (await jobs.get(cid, "revoked-request")).status == "error"
    assert calls == ["model"]
