import asyncio
import hashlib
import threading

import httpx
import pytest
from openai import InternalServerError

from deep_research.agents.base import RunContext
from deep_research.artifacts import ArtifactStore
from deep_research.call_budget import ModelCallLimitExceeded
from deep_research.observability import Tracer
from deep_research.task_call_budget import CallBudgetStateError, TaskCallBudget
from tests.fakes import FakeSearch
from tests.test_model_call_lifecycle import Stream, client


async def test_parallel_roles_share_one_durable_budget_and_recovery_cannot_reset_it(tmp_path):
    store = ArtifactStore(tmp_path)
    budget = TaskCallBudget(3, store, "run:one")
    results = await asyncio.gather(
        *(budget.reserve_async() for _ in range(6)), return_exceptions=True
    )
    assert results.count(None) == 3
    assert sum(isinstance(item, ModelCallLimitExceeded) for item in results) == 3
    restored = TaskCallBudget(3, store, "run:one")
    with pytest.raises(ModelCallLimitExceeded):
        await restored.reserve_async()
    assert restored.used == 3
    independent = TaskCallBudget(3, store, "run:two")
    await independent.reserve_async()
    assert independent.used == 1


async def test_corrupt_budget_record_stops_work_instead_of_granting_a_new_allowance(tmp_path):
    store = ArtifactStore(tmp_path)
    budget = TaskCallBudget(2, store, "run:one")
    await budget.reserve_async()
    store.write_control_json(budget.path, {"used": -1})
    with pytest.raises(CallBudgetStateError):
        await budget.reserve_async()
    assert budget.used == 1 and budget.rejected


async def test_cancellation_keeps_reservation_locked_until_its_write_finishes(
    tmp_path, monkeypatch,
):
    store = ArtifactStore(tmp_path)
    budget = TaskCallBudget(1, store, "run:cancel")
    entered, release = threading.Event(), threading.Event()
    original = store.write_control_json

    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "write_control_json", delayed)
    first = asyncio.create_task(budget.reserve_async())
    assert await asyncio.to_thread(entered.wait, 3)
    first.cancel()
    second = asyncio.create_task(budget.reserve_async())
    try:
        await asyncio.sleep(0.02)
        assert not second.done()
    finally:
        release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert isinstance(results[0], asyncio.CancelledError)
    assert isinstance(results[1], ModelCallLimitExceeded) and budget.used == 1


async def test_model_transport_retries_use_the_same_budget_as_later_roles(settings, tmp_path):
    failure = InternalServerError(
        "controlled failure", response=httpx.Response(
            500, request=httpx.Request("POST", "https://model.invalid")
        ), body=None,
    )
    llm, tracer, requests = await client(settings, failure, Stream())
    store = ArtifactStore(tmp_path)
    tracer.call_budget = TaskCallBudget(2, store, "run:retry")
    assert await llm.complete("system", "question") == "done"
    assert len(requests.requests) == 2 and tracer.call_budget.used == 2
    other, other_tracer, other_requests = await client(settings, Stream())
    other._call_role = "evidence_verifier"
    other_tracer.call_budget = TaskCallBudget(2, store, "run:retry")
    with pytest.raises(ModelCallLimitExceeded):
        await other.complete("system", "verify")
    assert not other_requests.requests


async def test_injected_model_role_copies_use_current_run_budget(settings, tmp_path):
    model, original_tracer, requests = await client(settings, Stream(), Stream())
    tracer = Tracer()
    tracer.call_budget = TaskCallBudget(1, ArtifactStore(tmp_path), "run:injected")
    ctx = RunContext(llm=model, search_tool=FakeSearch(), tracer=tracer, settings=settings)
    researcher, verifier = ctx.llm_for("researcher"), ctx.llm_for("evidence_verifier")
    assert await researcher.complete("s", "u") == "done"
    with pytest.raises(ModelCallLimitExceeded):
        await verifier.complete("s", "u")
    assert len(requests.requests) == 1 and tracer.call_budget.used == 1
    assert model.tracer is original_tracer and original_tracer.call_budget.used == 0


async def test_task_entrypoint_wires_the_frozen_request_limit(settings):
    from tests.test_completion_auto_repair import run_fixture

    _, run_id, _, agent = await run_fixture(settings, broken=False, model_call_limit=3)
    budget = agent.tracer.call_budget
    assert isinstance(budget, TaskCallBudget) and budget.limit == 3
    assert budget.key == hashlib.sha256(f"run:{run_id}".encode()).hexdigest()
