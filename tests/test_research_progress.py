from __future__ import annotations

import asyncio
import json

import pytest

from deep_research.agents.base import Blackboard, RunContext
from deep_research.agents.researcher import Researcher
from deep_research.artifacts import ArtifactStore
from deep_research.config import Settings
from deep_research.models import ExtractionAudit, ResearchPlan, ResearchResult, Source, SubQuestion
from deep_research.observability import Tracer
from deep_research.persistence.repository import LeaseLostError
from deep_research.reproducibility import RecordingSearchTool
from deep_research.research_progress import ResearchProgress, ResearchProgressError
from deep_research.scheduler import research_dag
from tests.fakes import FakeLLM, FakeSearch, verified_finding


def result(question):
    return ResearchResult(
        sub_question=question,
        findings=[verified_finding(statement=question)],
        extraction_audit=ExtractionAudit(
            question=question,
            sources=[Source(url="https://a.com", content="内容A提供了可核验的原文证据")],
        ),
    )


def context(store, run_id="run-a"):
    return RunContext(
        llm=FakeLLM(),
        search_tool=RecordingSearchTool(FakeSearch()),
        tracer=Tracer(),
        settings=Settings(max_concurrency=1),
        artifact_store=store,
        run_id=run_id,
    )


async def test_completed_subquestion_survives_discarded_step_and_restores_dependency_sources(
    tmp_path,
):
    store = ArtifactStore(tmp_path)
    initial = Blackboard(
        query="q",
        plan=ResearchPlan(
            interpretation="q",
            sub_questions=[
                SubQuestion(question="first"),
                SubQuestion(question="second", depends_on=[0]),
            ],
        ),
    )
    calls = []

    class Interrupted(Researcher):
        async def run(self, question, context_findings=None, **kwargs):
            calls.append(question)
            if question == "second":
                raise asyncio.CancelledError()
            return result(question)

    with pytest.raises(asyncio.CancelledError):
        await Interrupted().step(initial.model_copy(deep=True), context(store))
    assert initial.results == []  # The enclosing transactional step was never committed.
    assert calls == ["first", "second"]
    assert store.list_artifacts("q") == []  # Progress is internal, not a user deliverable.

    class Resumed(Researcher):
        async def run(self, question, context_findings=None, **kwargs):
            calls.append(question)
            assert question == "second"
            assert [f.statement for f in context_findings] == ["first"]
            return result(question)

    restored_sources = []

    async def sink(sources):
        restored_sources.extend(sources)

    resumed_ctx = context(ArtifactStore(tmp_path))  # A new store/context, as after process restart.
    resumed_ctx.search_tool.set_sink(sink)
    completed = await Resumed().step(initial.model_copy(deep=True), resumed_ctx)
    assert calls == ["first", "second", "second"]
    assert [r.sub_question for r in completed.results] == ["first", "second"]
    assert restored_sources[0].content == "内容A提供了可核验的原文证据"
    assert any(e.data and e.data.get("reused") for e in resumed_ctx.tracer.events)


async def test_progress_does_not_cross_runs_changed_context_or_corroboration_policy(tmp_path):
    store = ArtifactStore(tmp_path)
    plan = ResearchPlan(interpretation="q", sub_questions=[SubQuestion(question="one")])
    calls = []

    class Counter(Researcher):
        async def run(self, question, **kwargs):
            calls.append(question)
            return result(question)

    initial = Blackboard(query="q", plan=plan)
    await Counter().step(initial.model_copy(deep=True), context(store))
    await Counter().step(initial.model_copy(deep=True), context(store, "run-b"))
    changed = context(store)
    changed.settings.require_corroboration = True
    await Counter().step(initial.model_copy(deep=True), changed)
    assert calls == ["one"] * 3
    progress = ResearchProgress(store, {"run_id": "run-a"})
    assert progress.key("one", [verified_finding("parent-a")]) != progress.key(
        "one", [verified_finding("parent-b")]
    )


async def test_corrupt_progress_is_not_silently_skipped_or_overwritten(tmp_path):
    store = ArtifactStore(tmp_path)
    initial = Blackboard(
        query="q",
        plan=ResearchPlan(interpretation="q", sub_questions=[SubQuestion(question="one")]),
    )

    class Completed(Researcher):
        async def run(self, question, **kwargs):
            return result(question)

    await Completed().step(initial.model_copy(deep=True), context(store))
    path = next(store.framework_root.glob("research/*.json"))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["result"]["findings"][0]["statement"] = "changed without a matching digest"
    path.write_text(json.dumps(data), encoding="utf-8")

    class Unexpected(Researcher):
        async def run(self, *args, **kwargs):
            raise AssertionError("Corrupt saved work must not silently trigger paid re-extraction")

    with pytest.raises(ResearchProgressError, match="校验失败"):
        await Unexpected().step(initial.model_copy(deep=True), context(store))


async def test_progress_write_failure_stops_queued_subquestions(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path)
    initial = Blackboard(
        query="q",
        plan=ResearchPlan(
            interpretation="q",
            sub_questions=[
                SubQuestion(question="one"),
                SubQuestion(question="two"),
                SubQuestion(question="three"),
            ],
        ),
    )
    calls = []

    def full(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(store, "write_control_json", full)

    class Counter(Researcher):
        async def run(self, question, **kwargs):
            calls.append(question)
            return result(question)

    with pytest.raises(ResearchProgressError):
        await Counter().step(initial, context(store))
    assert calls == ["one"]


@pytest.mark.parametrize("failure", [LeaseLostError, ResearchProgressError])
async def test_dag_propagates_ownership_or_progress_failure_and_cancels_other_work(failure):
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def research(question, context):
        if question == "fail":
            await entered.wait()
            raise failure("cannot continue")
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    with pytest.raises(failure):
        await asyncio.wait_for(
            research_dag(
                [SubQuestion(question="fail"), SubQuestion(question="pending")], research, Tracer()
            ),
            2,
        )
    assert cancelled.is_set()


@pytest.mark.parametrize("failure", [LeaseLostError, ResearchProgressError])
@pytest.mark.parametrize("graph", [False, True])
async def test_workflow_does_not_retry_fallback_or_continue_after_progress_failure(
    failure, graph, tmp_path
):
    from deep_research.workflow import Workflow, WorkflowEngine

    calls = []

    class Agent:
        def __init__(self, name):
            self.name = name

        async def step(self, bb, ctx):
            calls.append(self.name)
            if self.name == "fail":
                raise failure("cannot continue")
            return bb

    steps = [{"agent": "fail", "max_attempts": 2, "fallback_agent": "fallback"}, {"agent": "later"}]
    workflow = Workflow(
        name="progress-test",
        **(
            {
                "nodes": [{"id": "first", "step": steps[0]}, {"id": "next", "step": steps[1]}],
                "edges": [{"id": "edge", "source": "first", "target": "next"}],
            }
            if graph
            else {"steps": steps}
        ),
    )
    with pytest.raises(failure):
        await WorkflowEngine(context(ArtifactStore(tmp_path)), resolver=Agent).run(
            workflow, Blackboard(query="q")
        )
    assert calls == ["fail"]
