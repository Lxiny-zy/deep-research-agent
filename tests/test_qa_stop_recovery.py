"""Durable stops, fenced stage writes and explicit recovery without replay."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from deep_research.agents.base import RunContext
from deep_research.observability import Tracer
from deep_research.workbench.qa import answer_question
from deep_research.workbench.qa_checkpoint import read_checkpoint
from deep_research.workbench.qa_jobs import start_turn
from deep_research.workbench.qa_store import message_payload
from deep_research.workbench.support import SupportDecisions
from tests.test_prose_review import CorrelationSearch
from tests.test_qa_continue_revision import ContinueModel
from tests.test_qa_requests import stores as stores


async def test_stop_pending_and_running_preserves_history_and_fences_late_work(stores):
    store, jobs, other, cid = stores
    for rid in ("finished", "running", "queued"):
        await jobs.reserve(cid, rid, rid, {"query": rid})
    assert await jobs.claim(cid, "finished", "owner", 90)
    assert await jobs.update(cid, "finished", "owner", result={"answer": "old verified answer"})
    assert await jobs.claim(cid, "running", "owner", 90)
    assert await jobs.checkpoint(cid, "running", "owner", {"stage": "test"})
    assert (await other.cancel(cid, "running")).status == "cancelled"
    assert (await other.cancel(cid, "queued")).status == "cancelled"
    assert (await jobs.cancel(cid, "finished")).status == "done"
    assert not await jobs.update(cid, "running", "owner", result={"answer": "late"})
    assert not await jobs.checkpoint(cid, "running", "owner", {"stage": "late"})
    assert not await jobs.claim(cid, "queued", "other", 90)
    assert await jobs.pending() == []
    rows = (await store.get(cid)).messages
    assert [row.status for row in rows] == ["done", "cancelled", "cancelled"]
    assert rows[0].answer == "old verified answer"
    assert rows[1].request_payload["_checkpoint"] == {"stage": "test"}
    assert "_checkpoint" not in message_payload(rows[1])["request_payload"]


async def test_cross_instance_stop_interrupts_active_call_without_reexecution(stores):
    _, jobs, other, cid = stores
    await jobs.reserve(cid, "active-request", "h", {"query": "q"})
    entered, cancelled = asyncio.Event(), asyncio.Event()
    calls = 0

    async def execute(**callbacks):
        nonlocal calls
        calls += 1
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    app = SimpleNamespace(state=SimpleNamespace())
    runtime = start_turn(app, jobs, cid, "active-request", execute, 30)
    await asyncio.wait_for(entered.wait(), 2)
    await other.cancel(cid, "active-request")
    assert (await asyncio.wait_for(runtime.task, 3))["status"] == "cancelled"
    assert cancelled.is_set()
    replay = start_turn(app, other, cid, "active-request", execute, 30)
    assert (await asyncio.wait_for(replay.task, 2))["status"] == "cancelled"
    assert calls == 1


@pytest.mark.parametrize("stage", ["evidence", "draft", "reviewed"])
async def test_explicit_recovery_skips_completed_stages_and_reuses_bound_review(
    settings, stage, stores
):
    settings.quality = {"max_revisions": 0, "qa_claim_max_revisions": 0}

    class CountingModel(ContinueModel):
        reviews = 0

        async def parse(self, system, user, schema, **kwargs):
            if schema is SupportDecisions:
                self.reviews += 1
            return await super().parse(system, user, schema, **kwargs)

    model = CountingModel()
    ctx = RunContext(llm=model, search_tool=CorrelationSearch(), tracer=Tracer(), settings=settings)
    saved = []
    _, jobs, other, cid = stores
    await jobs.reserve(cid, "checkpoint-request", "hash", {"query": "解释变量关系"})
    assert await jobs.claim(cid, "checkpoint-request", "owner", 90)

    async def crash_after_commit(raw):
        saved.append(deepcopy(raw))
        assert await jobs.checkpoint(cid, "checkpoint-request", "owner", raw)
        if raw["stage"] == stage:
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await answer_question(
            "解释变量关系", history=[], ctx=ctx, include_web=True, on_checkpoint=crash_after_commit
        )
    await jobs.cancel(cid, "checkpoint-request")
    reloaded = await other.get(cid, "checkpoint-request")
    checkpoint = read_checkpoint(reloaded.request_payload["_checkpoint"])
    assert checkpoint.sealed() == saved[-1]
    assert checkpoint.stage == stage
    extractions, checks, streams = model.extractions, model.evidence_checks, model.stream_calls
    review_calls = model.reviews
    resumed = await answer_question(
        "解释变量关系", history=[], ctx=ctx, resume_checkpoint=checkpoint.sealed()
    )
    assert resumed.answer
    assert model.extractions == extractions and model.evidence_checks == checks
    assert model.stream_calls == streams + (1 if stage == "evidence" else 0)
    if stage == "reviewed":
        assert model.reviews == review_calls
    corrupted = checkpoint.sealed()
    corrupted["material"]["draft"] = "not the committed draft"
    with pytest.raises(ValueError):
        read_checkpoint(corrupted)


async def test_finish_cancel_race_has_one_terminal_result(stores):
    _, jobs, other, cid = stores
    await jobs.reserve(cid, "race-request", "h", {"query": "q"})
    assert await jobs.claim(cid, "race-request", "owner", 90)
    completed, stopped = await asyncio.gather(
        jobs.update(cid, "race-request", "owner", result={"answer": "verified"}),
        other.cancel(cid, "race-request"),
    )
    final = await jobs.get(cid, "race-request")
    assert final.status == ("done" if completed else "cancelled")
    assert final.answer == ("verified" if completed else "")
    assert stopped.status == final.status
