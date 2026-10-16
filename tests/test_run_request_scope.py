"""Run ownership must survive shared clients and library checkpoint recovery."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from deep_research.agents.base import Blackboard
from deep_research.artifacts import ArtifactStore
from deep_research.call_budget import ModelCallLimitExceeded
from deep_research.models import ResearchResult
from deep_research.observability import Tracer
from deep_research.orchestrator import DeepResearchAgent, create_initial_execution
from deep_research.task_call_budget import TASK_CALL_SCOPE_KEY, CallBudgetStateError, TaskCallBudget
from deep_research.tools.composite import MultiBackendSearch
from deep_research.tools.key_pool import ApiKeyPoolSearch
from deep_research.tools.model_search import ModelSearch
from deep_research.workflow import Step, Workflow
from tests.fakes import FakeLLM, FakeSearch, verified_finding


@pytest.mark.parametrize("composition", ["native", "composite", "key_pool"])
async def test_shared_search_keeps_concurrent_tasks_and_the_original_client_isolated(
    settings, tmp_path, composition,
):
    entered, release = asyncio.Event(), asyncio.Event()
    requests = []
    original = Tracer()

    async def respond(request):
        query = json.loads(request.content)["input"]
        requests.append(query)
        if query == "first":
            entered.set()
            await release.wait()
        return httpx.Response(200, json={
            "citations": ["https://source.test/paper"],
            "usage": {"total_tokens": 3 if query == "first" else 7},
        })

    native = ModelSearch(
        "offline-key", endpoint="https://scope.test/search", model="search", tracer=original,
    )
    await native._client.aclose()
    native._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    await native._pages.aclose()
    native._pages = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(
            200, headers={"content-type": "text/html"}, text="<p>Original source.</p>",
        ),
    ))
    backend = native
    if composition == "composite":
        backend = MultiBackendSearch([native], tracer=original)
    elif composition == "key_pool":
        backend = ApiKeyPoolSearch(
            "scope-test", ["offline-key"], lambda _: native,
            tracer=original, namespace=uuid4().hex,
        )
    first = DeepResearchAgent(settings, llm=FakeLLM(), search_tool=backend)
    first.tracer.call_budget = TaskCallBudget(1, ArtifactStore(tmp_path / "first"), "first")
    second = None
    pending = asyncio.create_task(first.search_tool.search("first"))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        second = DeepResearchAgent(settings, llm=FakeLLM(), search_tool=backend)
        second.tracer.call_budget = TaskCallBudget(1, ArtifactStore(tmp_path / "second"), "second")
        assert await asyncio.wait_for(second.search_tool.search("second"), 3)
        release.set()
        assert await asyncio.wait_for(pending, 3)
        assert first.tracer.total_tokens == 3 and second.tracer.total_tokens == 7
        assert first.tracer.call_budget.used == second.tracer.call_budget.used == 1
        for agent in (first, second):
            # A composite may aggregate the rejection, but must never send HTTP.
            with pytest.raises((ModelCallLimitExceeded, RuntimeError)):
                await agent.search_tool.search("exhausted")
        assert requests == ["first", "second"]
        assert native._tracer is backend._tracer is original
        assert original.total_tokens == 0 and original.call_budget is None
        assert first.search_tool.delegate.backend_name == backend.backend_name
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await first.aclose()
        if second is not None:
            await second.aclose()
        await backend.aclose()


def execution_for(settings):
    settings.intent_enabled = settings.runner_enabled = False
    settings.quality = {"task_max_model_calls": 1, "max_revisions": 0}
    workflow = Workflow(name="request_scope", steps=[Step(agent="synthesizer")])
    execution = create_initial_execution("研究结果", workflow.name, settings)
    execution.definition = workflow.model_dump(mode="json")
    execution.checkpoint = Blackboard(
        query="研究结果",
        results=[ResearchResult(sub_question="q", findings=[verified_finding()])],
    ).model_dump(mode="json")
    return execution


@pytest.mark.parametrize("mode", ["legacy", "planner-driven"])
@pytest.mark.parametrize("caller_store", [True, False])
async def test_library_recovery_keeps_one_budget_with_or_without_caller_owned_store(
    settings, tmp_path, mode, caller_store,
):
    settings.orchestration_mode = mode
    execution = execution_for(settings)
    store = ArtifactStore(tmp_path / "caller") if caller_store else None
    first = DeepResearchAgent(
        settings, llm=FakeLLM(), search_tool=FakeSearch(), artifact_store=store,
        initial_execution=execution,
    )
    second = None
    try:
        await first.run("研究结果")
        await first.tracer.call_budget.reserve_async()
        saved = first._completion_detail.orchestration.model_copy(deep=True)
        assert saved.checkpoint["scratch"][TASK_CALL_SCOPE_KEY]
        second = DeepResearchAgent(
            settings, llm=FakeLLM(), search_tool=FakeSearch(), artifact_store=store,
            resume_execution=saved,
        )
        await second.run("研究结果")
        with pytest.raises(ModelCallLimitExceeded):
            await second.tracer.call_budget.reserve_async()
        assert first.tracer.call_budget.key == second.tracer.call_budget.key
        assert second.tracer.call_budget.used == 1
    finally:
        await first.aclose()
        if second is not None:
            await second.aclose()


async def test_budget_identity_survives_interruption_before_first_checkpoint(
    settings, tmp_path, monkeypatch,
):
    execution = execution_for(settings)
    original = execution.model_copy(deep=True)
    store = ArtifactStore(tmp_path / "caller")
    first = DeepResearchAgent(
        settings, llm=FakeLLM(), search_tool=FakeSearch(), artifact_store=store,
        initial_execution=execution,
    )

    async def interrupt(bb, ctx):
        await ctx.tracer.call_budget.reserve_async()
        raise RuntimeError("interrupted before saving the new checkpoint")

    monkeypatch.setattr(first, "_apply_intent_gate", interrupt)
    second = None
    try:
        with pytest.raises(RuntimeError, match="interrupted before saving"):
            await first.run("研究结果")
        second = DeepResearchAgent(
            settings, llm=FakeLLM(), search_tool=FakeSearch(), artifact_store=store,
            resume_execution=original,
        )
        monkeypatch.setattr(second, "_apply_intent_gate", interrupt)
        with pytest.raises(ModelCallLimitExceeded):
            await second.run("研究结果")
        assert first.tracer.call_budget.key == second.tracer.call_budget.key
        assert second.tracer.call_budget.used == 1
    finally:
        await first.aclose()
        if second is not None:
            await second.aclose()


@pytest.mark.parametrize("corrupt_scope", ["", None, 17])
async def test_corrupt_saved_scope_does_not_grant_a_new_allowance(
    settings, tmp_path, corrupt_scope,
):
    execution = execution_for(settings)
    execution.checkpoint["scratch"][TASK_CALL_SCOPE_KEY] = corrupt_scope
    model = FakeLLM()
    agent = DeepResearchAgent(
        settings, llm=model, search_tool=FakeSearch(), artifact_store=ArtifactStore(tmp_path),
        resume_execution=execution,
    )
    try:
        with pytest.raises(ValueError, match="task model-call scope"):
            await agent.run("研究结果")
        assert model.parse_calls == model.stream_calls == model.complete_calls == 0
    finally:
        await agent.aclose()


async def test_legacy_unbound_counter_is_not_silently_replaced(settings, tmp_path):
    execution = execution_for(settings)
    store = ArtifactStore(tmp_path)
    lost = TaskCallBudget(1, store, "run:old-unrecorded-uuid")
    await lost.reserve_async()
    model = FakeLLM()
    agent = DeepResearchAgent(
        settings, llm=model, search_tool=FakeSearch(), artifact_store=store,
        resume_execution=execution,
    )
    try:
        with pytest.raises(CallBudgetStateError, match="历史模型请求额度"):
            await agent.run("研究结果")
        assert model.parse_calls == model.stream_calls == model.complete_calls == 0
    finally:
        await agent.aclose()


def test_new_unstarted_execution_has_a_durable_budget_identity(settings):
    settings.intent_enabled = settings.runner_enabled = False
    # A task contract can declare its allowance after this checkpoint is created.
    settings.quality = {}
    execution = create_initial_execution("q", "quick", settings)
    assert execution.checkpoint["scratch"][TASK_CALL_SCOPE_KEY] == execution.id
