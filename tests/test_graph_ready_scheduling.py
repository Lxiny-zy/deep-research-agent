"""DAG progress, deterministic merges and snapshots under concurrent completion."""

from __future__ import annotations

import asyncio

import pytest

from deep_research.agents.base import Blackboard, RunContext
from deep_research.models import Finding, Report, ResearchResult
from deep_research.observability import Tracer
from deep_research.orchestration import StepStatus
from deep_research.workflow import Workflow, WorkflowEngine
from tests.fakes import FakeLLM, FakeSearch


def graph(names, links):
    return Workflow(
        name="ready-graph",
        nodes=[{"id": name, "step": {"agent": name}} for name in names],
        edges=[{"id": f"{a}-{b}", "source": a, "target": b} for a, b in links],
    )


def engine_for(settings, action, **kwargs):
    class Role:
        def __init__(self, name):
            self.name = name

        async def step(self, bb, ctx):
            await action(self.name, bb)
            return bb

    return WorkflowEngine(
        RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings),
        resolver=Role,
        **kwargs,
    )


async def test_successor_starts_while_an_unrelated_earlier_branch_is_running(settings):
    settings.workflow_max_concurrency = 2
    release_slow, successor_started = asyncio.Event(), asyncio.Event()
    workflow = graph(
        ["root", "slow", "fast", "successor", "join"],
        [
            ("root", "slow"),
            ("root", "fast"),
            ("fast", "successor"),
            ("slow", "join"),
            ("successor", "join"),
        ],
    )

    async def action(name, bb):
        if name == "slow":
            await release_slow.wait()
            bb.scratch["conflict"] = "slow"
        if name == "fast":
            bb.scratch["conflict"] = "fast"
        if name == "successor":
            assert bb.scratch["conflict"] == "fast"
            assert [r.sub_question for r in bb.results] == ["fast"]
            bb.results[0].findings = [
                Finding(statement="edited fast", source_url="https://fast.example")
            ]
            successor_started.set()
        if name in {"slow", "fast", "successor"}:
            bb.results.append(ResearchResult(sub_question=name))
        if name == "join":
            bb.report = Report(
                query=bb.query, markdown=",".join(r.sub_question for r in bb.results)
            )

    engine = engine_for(settings, action)
    task = asyncio.create_task(engine.run(workflow, Blackboard(query="q")))
    try:
        await asyncio.wait_for(successor_started.wait(), 2)
        assert not task.done()
    finally:
        release_slow.set()
        result = await asyncio.wait_for(task, 3)
    assert result.report.markdown == "slow,fast,successor"
    assert result.scratch["conflict"] == "fast"
    assert not result.results[0].findings
    assert result.results[1].findings[0].statement == "edited fast"
    assert "_graph_state" not in engine.runtime.run.checkpoint


async def test_edits_preserve_an_unrelated_result_even_when_questions_are_identical(settings):
    workflow = graph(
        ["left", "right", "edit", "join"],
        [("right", "edit"), ("left", "join"), ("edit", "join")],
    )

    async def action(name, bb):
        if name in {"left", "right"}:
            bb.results.append(ResearchResult(sub_question="same question"))
        if name == "edit":
            assert len(bb.results) == 1
            bb.results[0].findings = [
                Finding(statement="right only", source_url="https://right.example")
            ]

    result = await engine_for(settings, action).run(workflow, Blackboard(query="q"))
    assert len(result.results) == 2
    assert not result.results[0].findings
    assert result.results[1].findings[0].statement == "right only"


async def test_wide_graph_limits_actual_agent_execution(settings):
    settings.workflow_max_concurrency = 2
    branches = [f"branch-{i}" for i in range(10)]
    workflow = graph(
        ["root", *branches, "join"],
        [("root", name) for name in branches] + [(name, "join") for name in branches],
    )
    started, released = asyncio.Event(), asyncio.Event()
    active = peak = 0
    calls = []

    async def action(name, bb):
        nonlocal active, peak
        if name not in branches:
            return
        calls.append(name)
        active += 1
        peak = max(peak, active)
        if active == 2:
            started.set()
        try:
            await released.wait()
        finally:
            active -= 1

    engine = engine_for(settings, action)
    task = asyncio.create_task(engine.run(workflow, Blackboard(query="q")))
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert len(calls) == 2
    finally:
        released.set()
        await asyncio.wait_for(task, 3)
    assert peak == 2 and set(calls) == set(branches)


async def test_resume_keeps_dependency_inputs_separate_from_finished_unrelated_work(settings):
    workflow = graph(
        ["root", "left", "right", "join"],
        [("root", "left"), ("root", "right"), ("left", "join"), ("right", "join")],
    )
    right_saved = asyncio.Event()
    calls = []

    async def action(name, bb):
        calls.append(name)
        if name == "left":
            assert "right_value" not in bb.scratch
            if calls.count("left") == 1:
                await asyncio.Event().wait()
            bb.scratch["left_value"] = 1
        if name == "right":
            bb.scratch["right_value"] = 2
        if name == "join":
            assert bb.scratch["left_value"] == 1 and bb.scratch["right_value"] == 2

    async def save(run):
        if any(
            step.node_id == "right" and step.status == StepStatus.SUCCEEDED for step in run.steps
        ):
            right_saved.set()

    first = engine_for(settings, action, checkpoint_sink=save)
    task = asyncio.create_task(first.run(workflow, Blackboard(query="q")))
    await asyncio.wait_for(right_saved.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    saved = first.runtime.run.model_copy(deep=True)
    saved.checkpoint["scratch"]["_resource_wait"] = {"count": 7, "not_before": 0}
    resumed = engine_for(settings, action, resume_run=saved)
    result = await resumed.run(workflow, Blackboard.model_validate(saved.checkpoint))
    assert calls.count("root") == calls.count("right") == 1
    assert calls.count("left") == 2 and calls.count("join") == 1
    assert result.scratch["_resource_wait"]["count"] == 7


async def test_checkpoint_statuses_are_frozen_with_their_committed_outputs(settings):
    workflow = graph(
        ["root", "left", "right", "join"],
        [("root", "left"), ("root", "right"), ("left", "join"), ("right", "join")],
    )
    writing, finish_write, release_right, right_finished = (asyncio.Event() for _ in range(4))
    captured = []

    async def action(name, bb):
        if name == "left":
            bb.scratch["left_value"] = "saved"
        elif name == "right":
            await release_right.wait()
            bb.scratch["right_value"] = "later"
            right_finished.set()

    async def save(run):
        if not captured and run.checkpoint.get("scratch", {}).get("left_value"):
            captured.append(run)  # Deliberately keep the sink's reference while it awaits I/O.
            writing.set()
            await finish_write.wait()

    engine = engine_for(settings, action, checkpoint_sink=save)
    task = asyncio.create_task(engine.run(workflow, Blackboard(query="q")))
    try:
        await asyncio.wait_for(writing.wait(), 2)
        release_right.set()
        await asyncio.wait_for(right_finished.wait(), 2)
        right = next(step for step in captured[0].steps if step.node_id == "right")
        assert right.status == StepStatus.RUNNING
        assert "right_value" not in captured[0].checkpoint["scratch"]
    finally:
        release_right.set()
        finish_write.set()
        await asyncio.wait_for(task, 3)
    replayed = []

    async def replay(name, bb):
        replayed.append(name)
        if name == "right":
            bb.scratch["right_value"] = "recovered"

    resumed = engine_for(settings, replay, resume_run=captured[0])
    result = await resumed.run(workflow, Blackboard.model_validate(captured[0].checkpoint))
    assert replayed == ["right", "join"]
    assert result.scratch["left_value"] == "saved"
    assert result.scratch["right_value"] == "recovered"
