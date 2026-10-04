"""Only persisted subquestion progress survives failed transactional branches."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from deep_research.agents.base import Blackboard
from deep_research.agents.researcher import Researcher
from deep_research.artifacts import ArtifactStore
from deep_research.execution_policy import (
    COMMITTED_RESEARCH_PROGRESS_KEY,
    committed_research_progress,
    recovery_checkpoint,
)
from deep_research.models import Report, ResearchPlan, SubQuestion
from deep_research.research_progress import ResearchProgressError
from deep_research.workflow import Step, Workflow, WorkflowEngine
from tests.test_research_progress import context, result


def definition(graph):
    step = {"agent": "researcher", "retry": {"max_attempts": 1}}
    return Workflow(
        name="durable-progress",
        nodes=[{"id": "research", "step": step}] if graph else [],
        steps=[] if graph else [Step.model_validate(step)],
    )


def initial(questions=("one",)):
    return Blackboard(
        query="Q",
        plan=ResearchPlan(
            interpretation="Q",
            sub_questions=[
                SubQuestion(question=question, depends_on=[index - 1] if index else [])
                for index, question in enumerate(questions)
            ],
        ),
    )


@pytest.mark.parametrize("graph", [False, True])
@pytest.mark.parametrize("failure", [httpx.ConnectError, asyncio.CancelledError])
async def test_failed_or_cancelled_branch_preserves_only_committed_progress(
    tmp_path, graph, failure
):
    store = ArtifactStore(tmp_path)

    class Interrupted(Researcher):
        async def run(self, question, **kwargs):
            return result(question)

        async def step(self, bb, ctx):
            await super().step(bb, ctx)
            bb.report = Report(query="Q", markdown="Unreviewed draft")
            bb.scratch["unreviewed_content"] = "must be discarded"
            raise failure("interrupted after saving a subquestion")

    engine = WorkflowEngine(
        context(store), resolver=lambda _: Interrupted(), recover_transient=True
    )
    with pytest.raises(failure):
        await engine.run(definition(graph), initial())
    checkpoint = engine.runtime.run.checkpoint
    assert checkpoint["report"] is None
    assert checkpoint["results"] == []
    assert "unreviewed_content" not in checkpoint["scratch"]
    saved = json.loads(next(store.framework_root.glob("research/*.json")).read_text("utf-8"))
    assert committed_research_progress(checkpoint["scratch"]) == {saved["key"]: saved["digest"]}


@pytest.mark.parametrize("graph", [False, True])
@pytest.mark.parametrize("mode", ["no_store", "no_audit", "write_failure"])
async def test_unsaved_or_unverifiable_attempts_do_not_claim_committed_progress(
    tmp_path, monkeypatch, graph, mode
):
    store = None if mode == "no_store" else ArtifactStore(tmp_path)
    if mode == "write_failure":

        def fail_write(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(store, "write_control_json", fail_write)

    class Interrupted(Researcher):
        async def run(self, question, **kwargs):
            saved = result(question)
            if mode == "no_audit":
                saved.extraction_audit = None
            return saved

        async def step(self, bb, ctx):
            await super().step(bb, ctx)
            raise httpx.ConnectError("cannot finish the step")

    engine = WorkflowEngine(
        context(store), resolver=lambda _: Interrupted(), recover_transient=True
    )
    error = ResearchProgressError if mode == "write_failure" else httpx.ConnectError
    with pytest.raises(error):
        await engine.run(definition(graph), initial())
    assert committed_research_progress(engine.runtime.run.checkpoint["scratch"]) == {}


@pytest.mark.parametrize("graph", [False, True])
async def test_recovery_counts_new_saved_units_but_not_replaying_the_same_units(tmp_path, graph):
    store = ArtifactStore(tmp_path)
    calls = []
    completed = 1

    class Interrupted(Researcher):
        async def run(self, question, **kwargs):
            calls.append(question)
            if int(question) > completed:
                raise asyncio.CancelledError
            return result(question)

    execution = None
    for attempt, completed in enumerate((1, 2, 2)):
        ctx = context(store)
        ctx.settings = replace(ctx.settings, max_run_seconds=30, max_no_progress_attempts=1)
        engine = WorkflowEngine(
            ctx,
            resolver=lambda _: Interrupted(),
            recover_transient=True,
            resume_run=execution,
        )
        blackboard = (
            Blackboard.model_validate(execution.checkpoint)
            if execution
            else initial(("1", "2", "3"))
        )
        with pytest.raises(asyncio.CancelledError):
            await engine.run(definition(graph), blackboard)
        checkpoint = engine.runtime.run.checkpoint
        assert len(committed_research_progress(checkpoint["scratch"])) == completed
        assert checkpoint["results"] == []
        execution, reason = recovery_checkpoint(
            engine.runtime.run, ctx.settings, reason="attempt_timeout", elapsed=attempt + 1
        )
        assert reason == ("no_progress" if attempt == 2 else "scheduled")
    assert calls == ["1", "2", "2", "3", "3"]


async def test_failed_branch_marker_merges_with_successful_sibling_without_its_draft(tmp_path):
    store = ArtifactStore(tmp_path)
    succeeded = asyncio.Event()

    class Branch(Researcher):
        async def run(self, question, **kwargs):
            return result(self.name)

        async def step(self, bb, ctx):
            # Distinct questions also bind each saved result to its own branch.
            if self.name == "seed":
                return bb
            bb.scratch["pending_sub_questions"] = [SubQuestion(question=self.name)]
            await super().step(bb, ctx)
            if self.name == "good":
                succeeded.set()
                return bb
            await succeeded.wait()
            bb.report = Report(query="Q", markdown="Rejected draft")
            raise httpx.ConnectError("temporary error")

    def resolve(name):
        branch = Branch()
        branch.name = name
        return branch

    engine = WorkflowEngine(context(store), resolver=resolve, recover_transient=True)
    workflow = Workflow(
        name="parallel-progress",
        nodes=[
            {"id": name, "step": {"agent": name, "retry": {"max_attempts": 1}}}
            for name in ("seed", "bad", "good")
        ],
        edges=[
            {"id": f"seed-{name}", "source": "seed", "target": name} for name in ("bad", "good")
        ],
    )
    with pytest.raises(httpx.ConnectError):
        await engine.run(workflow, initial())
    checkpoint = engine.runtime.run.checkpoint
    assert len(checkpoint["scratch"][COMMITTED_RESEARCH_PROGRESS_KEY]) == 2
    assert [item["sub_question"] for item in checkpoint["results"]] == ["good"]
    assert checkpoint["report"] is None
