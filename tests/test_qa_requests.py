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


async def test_completed_question_keeps_citation_binding_and_support_ids(stores):
    store, jobs, other, cid = stores
    rid = "bound-citation-request"
    binding = {
        "source_body": "answer [1]",
        "binding_status": "bound",
        "occurrences": [{"id": "a" * 24, "evidence_ids": ["evidence-one"]}],
    }
    evidence = [
        {
            "source_url": "https://example.org/paper",
            "support_id": "evidence-one",
            "evidence_quote": "original",
        }
    ]
    thoughts = [{"tool": "citation_binding", "input": "", "observation": "", "binding": binding}]
    await jobs.reserve(cid, rid, "payload", {"query": "question"})
    assert await jobs.claim(cid, rid, "owner", 90)
    assert await jobs.update(
        cid,
        rid,
        "owner",
        result={"answer": "answer [1]", "evidence": evidence, "thoughts": thoughts},
    )
    reloaded = await other.get(cid, rid)
    payload = message_payload(reloaded)
    assert payload["answer"] == "answer [1]"
    assert payload["thoughts"] == thoughts and payload["evidence"] == evidence
    assert (await store.get(cid)).messages[0].thoughts == thoughts


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


async def test_stream_journal_is_ordered_fenced_and_removed_only_after_success(stores):
    store, jobs, other, cid = stores
    rid = "request-stream"
    await jobs.reserve(cid, rid, rid, {"query": "q"})
    assert await jobs.claim(cid, rid, "owner", 90)
    batch = [
        ("reasoning", {"call_id": "one", "reasoning_delta": "thinking"}),
        ("delta", {"delta": "draft"}),
    ]
    assert not await other.append_events(cid, rid, "wrong-owner", batch)
    assert await jobs.append_events(cid, rid, "owner", batch)
    assert await jobs.append_events(cid, rid, "owner", [("reset", {"type": "reset"})])
    events = await other.events(cid, rid, 0)
    assert [item[0] for item in events] == [1, 2, 3]
    assert [item[1] for item in events] == ["reasoning", "delta", "reset"]
    assert await other.events(cid, rid, 2) == [events[-1]]
    assert (await store.get(cid)).messages[0].answer == ""
    assert await jobs.update(cid, rid, "owner", result={"answer": "validated"})
    assert await other.events(cid, rid, 0) == []
    assert not await jobs.append_events(cid, rid, "owner", batch)


async def test_interrupted_stream_trace_is_readable_but_not_writable_and_deletes_with_conversation(
    stores,
):
    store, jobs, other, cid = stores
    rid = "request-stream"
    await jobs.reserve(cid, rid, rid, {"query": "q"})
    assert await jobs.claim(cid, rid, "owner", 90)
    batch = [("delta", {"delta": "unfinished draft"})]
    assert await jobs.append_events(cid, rid, "owner", batch)
    assert await jobs.update(cid, rid, "owner", error="interrupted")
    assert (await other.events(cid, rid, 0))[0][2]["delta"] == "unfinished draft"
    assert not await other.append_events(cid, rid, "owner", batch)
    await store.delete(cid)
    assert await other.events(cid, rid, 0) == []
    if isinstance(store, SqlQaStore):
        from sqlalchemy import func, select

        from deep_research.persistence.orm import QaStreamEventRow

        async with store._sm() as session:
            assert await session.scalar(select(func.count()).select_from(QaStreamEventRow)) == 0


async def test_another_api_instance_receives_live_reasoning_resets_and_text_before_completion(
    stores, monkeypatch
):
    from starlette.requests import Request

    from deep_research import api
    from deep_research.config import Settings
    from deep_research.workbench import qa_api, qa_jobs

    store, jobs, other, cid = stores
    other_store = SqlQaStore(store._sm) if isinstance(store, SqlQaStore) else store
    applications = [
        SimpleNamespace(state=SimpleNamespace(settings=Settings(), qa_store=s))
        for s in (store, other_store)
    ]
    requests = [Request({"type": "http", "app": app}) for app in applications]
    first, revise, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = 0

    async def answer(*args, on_delta, on_event, on_checkpoint):
        nonlocal calls
        calls += 1
        on_event({"type": "reasoning", "call_id": "one", "reasoning_delta": "thinking first"})
        on_delta("draft first")
        first.set()
        await revise.wait()
        on_event({"type": "reset", "message": "revise"})
        on_event({"type": "reasoning", "call_id": "two", "reasoning_delta": "thinking second"})
        on_delta("draft second")
        await finish.wait()
        return {"answer": "validated final", "status": "done"}

    monkeypatch.setattr(api, "_check_rate_limit", AsyncMock())
    monkeypatch.setattr(qa_api, "_answer", answer)
    monkeypatch.setattr(qa_jobs, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(qa_jobs, "FLUSH_SECONDS", 0.01)
    body = qa_api.AskRequest(query="q", request_id="shared-stream-request")
    owner = await qa_api._prepare_turn(cid, body, requests[0])
    stream = None
    observed = []
    try:
        await asyncio.wait_for(first.wait(), 3)
        response = await qa_api.ask_stream(cid, body, requests[1])
        stream = response.body_iterator

        async def until(marker):
            while True:
                event = await asyncio.wait_for(anext(stream), 3)
                observed.append(event)
                if marker in event:
                    return

        await until("draft first")
        assert not owner.task.done() and calls == 1
        assert any("thinking first" in event for event in observed)
        revise.set()
        await until("draft second")
        assert any("event: reset" in event for event in observed)
        assert any("thinking second" in event for event in observed)
        assert not owner.task.done() and calls == 1
        finish.set()
        await until("validated final")
        assert (await other.get(cid, body.request_id)).answer == "validated final"
        assert await jobs.events(cid, body.request_id, 0) == []
    finally:
        revise.set()
        finish.set()
        if stream is not None:
            await stream.aclose()
        await asyncio.gather(
            *(t for app in applications for t in app.state.qa_tasks), return_exceptions=True
        )


async def test_stream_storage_failure_stops_inference_and_does_not_retry_it(stores, monkeypatch):
    from deep_research.workbench import qa_jobs

    _, jobs, _, cid = stores
    rid = "failed-stream-request"
    await jobs.reserve(cid, rid, rid, {"query": "q"})
    cancelled = asyncio.Event()
    calls = 0

    async def execute(*, on_delta, on_event):
        nonlocal calls
        calls += 1
        on_delta("draft")
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def fail(*args):
        raise OSError("simulated persistence failure")

    monkeypatch.setattr(jobs, "append_events", fail)
    monkeypatch.setattr(qa_jobs, "FLUSH_SECONDS", 0.01)
    app = SimpleNamespace(state=SimpleNamespace())
    runtime = qa_jobs.start_turn(app, jobs, cid, rid, execute, 5)
    message = await asyncio.wait_for(runtime.task, 3)
    assert message["status"] == "error" and cancelled.is_set()
    assert "OSError" in message["error"]
    again = qa_jobs.start_turn(app, jobs, cid, rid, execute, 5)
    assert (await asyncio.wait_for(again.task, 3))["status"] == "error"
    assert calls == 1


async def test_stream_fragments_are_batched_without_losing_content(stores, monkeypatch):
    from deep_research.workbench.qa_jobs import start_turn

    _, jobs, _, cid = stores
    rid = "batched-stream-request"
    await jobs.reserve(cid, rid, rid, {"query": "q"})
    batches = []
    append = jobs.append_events

    async def record(*args):
        batches.append(args[-1])
        return await append(*args)

    async def execute(*, on_delta, on_event):
        for _ in range(500):
            on_event({"type": "reasoning", "call_id": "one", "reasoning_delta": "x"})
        for _ in range(500):
            on_delta("y")
        return {"answer": "verified"}

    monkeypatch.setattr(jobs, "append_events", record)
    app = SimpleNamespace(state=SimpleNamespace())
    runtime = start_turn(app, jobs, cid, rid, execute, 5)
    assert (await asyncio.wait_for(runtime.task, 3))["status"] == "done"
    assert len(batches) == 1 and len(batches[0]) == 2
    assert batches[0][0][1]["reasoning_delta"] == "x" * 500
    assert batches[0][1][1]["delta"] == "y" * 500


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
    narrow = dialogue_context(history, 150)
    assert "摘录" in narrow and "摘要" in narrow and len(narrow) <= 150


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
    assert "旧的短回复" not in context and "第 1—2 轮未纳入" in context


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
