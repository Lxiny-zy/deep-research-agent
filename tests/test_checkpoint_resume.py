from __future__ import annotations

import json

import pytest

from deep_research.agents.base import Blackboard, RunContext
from deep_research.observability import Tracer
from deep_research.orchestration import OrchestrationRuntime, RunStatus, StepStatus
from deep_research.orchestrator import DeepResearchAgent
from deep_research.workflow import Step, Workflow, WorkflowEngine
from tests.fakes import FakeLLM, FakeSearch


class ResumeAgent:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        self.calls.append(self.name)
        bb.scratch[self.name] = True
        return bb


def test_agent_restores_metrics_before_resumed_run_starts(settings) -> None:
    runtime = OrchestrationRuntime()
    execution = runtime.start("resume", {"query": "Q"})
    runtime.save_checkpoint(
        {
            "query": "Q",
            "scratch": {
                "_runtime_metrics": {
                    "total_tokens": 77,
                    "estimated_tokens": 12,
                    "elapsed": 9.5,
                }
            },
        },
        {"name": "resume", "steps": []},
    )

    agent = DeepResearchAgent(
        settings,
        llm=FakeLLM(),
        search_tool=FakeSearch(),
        resume_execution=execution,
    )

    assert agent.tracer.total_tokens == 77
    assert agent.tracer.estimated_tokens == 12
    assert agent.tracer.elapsed >= 9.5


@pytest.mark.asyncio
async def test_graph_resume_skips_completed_nodes(settings) -> None:
    definition = Workflow(
        name="resume-graph",
        nodes=[
            {"id": "a", "step": {"kind": "agent", "agent": "a"}},
            {"id": "b", "step": {"kind": "agent", "agent": "b"}},
        ],
        edges=[{"id": "ab", "source": "a", "target": "b"}],
    )
    runtime = OrchestrationRuntime()
    execution = runtime.start(definition.name, {"query": "Q"})
    completed = runtime.create_step(node_id="a", label="a", kind="agent", agent="a")
    runtime.start_step(completed)
    runtime.complete_step(completed)
    runtime.save_checkpoint(
        Blackboard(query="Q", scratch={"a": True}).model_dump(mode="json"),
        definition.model_dump(mode="json"),
    )

    calls: list[str] = []
    agents = {name: ResumeAgent(name, calls) for name in ("a", "b")}
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    engine = WorkflowEngine(ctx, resolver=agents.__getitem__, resume_run=execution)
    bb = await engine.run(
        definition,
        Blackboard.model_validate(execution.checkpoint),
    )

    assert calls == ["b"]
    assert bb.scratch["a"] is True
    assert bb.scratch["b"] is True
    assert engine.runtime.run is not None
    statuses = {step.node_id: step.status for step in engine.runtime.run.steps}
    assert statuses == {"a": StepStatus.SUCCEEDED, "b": StepStatus.SUCCEEDED}


@pytest.mark.asyncio
async def test_linear_resume_skips_completed_prefix(settings) -> None:
    definition = Workflow(
        name="resume-linear",
        steps=[
            {"kind": "agent", "agent": "a"},
            {"kind": "agent", "agent": "b"},
        ],
    )
    runtime = OrchestrationRuntime()
    execution = runtime.start(definition.name, {"query": "Q"})
    completed = runtime.create_step(label="a", kind="agent", agent="a")
    runtime.start_step(completed)
    runtime.complete_step(completed)
    runtime.save_checkpoint(
        Blackboard(query="Q", scratch={"a": True}).model_dump(mode="json"),
        definition.model_dump(mode="json"),
    )
    calls: list[str] = []
    agents = {name: ResumeAgent(name, calls) for name in ("a", "b")}
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    engine = WorkflowEngine(ctx, resolver=agents.__getitem__, resume_run=execution)

    bb = await engine.run(definition, Blackboard.model_validate(execution.checkpoint))

    assert calls == ["b"]
    assert bb.scratch["a"] is True and bb.scratch["b"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "interrupted_status",
    [StepStatus.RUNNING, StepStatus.CANCELLED, StepStatus.FAILED],
)
async def test_graph_resume_handles_interrupted_and_failed_nodes(
    settings, interrupted_status: StepStatus
) -> None:
    definition = Workflow(
        name="resume-interrupted",
        nodes=[
            {"id": "a", "step": {"kind": "agent", "agent": "a"}},
            {
                "id": "b",
                "step": {"kind": "agent", "agent": "b"},
                "join_mode": "success_all",
            },
        ],
        edges=[{"id": "ab", "source": "a", "target": "b"}],
    )
    runtime = OrchestrationRuntime()
    execution = runtime.start(definition.name, {"query": "Q"})
    interrupted = runtime.create_step(node_id="a", label="a", kind="agent", agent="a")
    runtime.start_step(interrupted)
    if interrupted_status == StepStatus.CANCELLED:
        runtime.cancel_step(interrupted)
    elif interrupted_status == StepStatus.FAILED:
        runtime.fail_step(interrupted, RuntimeError("previous failure"))
    runtime.save_checkpoint(
        Blackboard(query="Q").model_dump(mode="json"),
        definition.model_dump(mode="json"),
    )

    calls: list[str] = []
    agents = {name: ResumeAgent(name, calls) for name in ("a", "b")}
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    engine = WorkflowEngine(ctx, resolver=agents.__getitem__, resume_run=execution)

    await engine.run(definition, Blackboard.model_validate(execution.checkpoint))

    assert engine.runtime.run is not None
    latest = {step.node_id: step.status for step in engine.runtime.run.steps}
    assert calls == ["a", "b"]
    assert latest == {"a": StepStatus.SUCCEEDED, "b": StepStatus.SUCCEEDED}


@pytest.mark.asyncio
async def test_graph_resume_reevaluates_condition_skips_after_upstream_recovers(settings) -> None:
    definition = Workflow(
        name="resume-condition-skip",
        nodes=[
            {"id": "a", "step": {"kind": "agent", "agent": "a"}},
            {
                "id": "b",
                "step": {"kind": "agent", "agent": "b"},
                "join_mode": "success_all",
            },
        ],
        edges=[{"id": "ab", "source": "a", "target": "b"}],
    )
    runtime = OrchestrationRuntime()
    execution = runtime.start(definition.name, {"query": "Q"})
    failed = runtime.create_step(node_id="a", label="a", kind="agent", agent="a")
    runtime.start_step(failed)
    runtime.fail_step(failed, RuntimeError("previous failure"))
    skipped = runtime.create_step(node_id="b", label="b", kind="agent", agent="b")
    runtime.skip_step(skipped, "incoming conditions not matched")
    runtime.save_checkpoint(
        Blackboard(query="Q").model_dump(mode="json"),
        definition.model_dump(mode="json"),
    )

    calls: list[str] = []
    agents = {name: ResumeAgent(name, calls) for name in ("a", "b")}
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    engine = WorkflowEngine(ctx, resolver=agents.__getitem__, resume_run=execution)

    await engine.run(definition, Blackboard.model_validate(execution.checkpoint))

    assert calls == ["a", "b"]
    assert engine.runtime.run is not None
    latest = {step.node_id: step.status for step in engine.runtime.run.steps}
    assert latest == {"a": StepStatus.SUCCEEDED, "b": StepStatus.SUCCEEDED}


@pytest.mark.asyncio
async def test_graph_resume_uses_latest_step_run_for_same_node(settings) -> None:
    definition = Workflow(
        name="resume-latest",
        nodes=[
            {"id": "a", "step": {"kind": "agent", "agent": "a"}},
            {
                "id": "b",
                "step": {"kind": "agent", "agent": "b"},
                "join_mode": "success_all",
            },
        ],
        edges=[{"id": "ab", "source": "a", "target": "b"}],
    )
    runtime = OrchestrationRuntime()
    execution = runtime.start(definition.name, {"query": "Q"})
    failed = runtime.create_step(node_id="a", label="a", kind="agent", agent="a")
    runtime.start_step(failed)
    runtime.fail_step(failed, RuntimeError("old failure"))
    succeeded = runtime.create_step(node_id="a", label="a", kind="agent", agent="a")
    runtime.start_step(succeeded)
    runtime.complete_step(succeeded)
    runtime.save_checkpoint(
        Blackboard(query="Q", scratch={"a": True}).model_dump(mode="json"),
        definition.model_dump(mode="json"),
    )

    calls: list[str] = []
    agents = {name: ResumeAgent(name, calls) for name in ("a", "b")}
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    engine = WorkflowEngine(ctx, resolver=agents.__getitem__, resume_run=execution)

    await engine.run(definition, Blackboard.model_validate(execution.checkpoint))

    assert calls == ["b"]
    assert engine.runtime.run is not None
    latest = {step.node_id: step.status for step in engine.runtime.run.steps}
    assert latest == {"a": StepStatus.SUCCEEDED, "b": StepStatus.SUCCEEDED}


@pytest.mark.asyncio
async def test_linear_resume_retries_failed_step(settings) -> None:
    definition = Workflow(
        name="resume-linear-failed",
        steps=[Step(agent="a"), Step(agent="b")],
    )
    runtime = OrchestrationRuntime()
    execution = runtime.start(definition.name, {"query": "Q"})
    failed = runtime.create_step(node_id="step-1", label="a", kind="agent", agent="a")
    runtime.start_step(failed)
    runtime.fail_step(failed, RuntimeError("previous failure"))
    runtime.save_checkpoint(
        Blackboard(query="Q").model_dump(mode="json"),
        definition.model_dump(mode="json"),
    )

    calls: list[str] = []
    agents = {name: ResumeAgent(name, calls) for name in ("a", "b")}
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    engine = WorkflowEngine(ctx, resolver=agents.__getitem__, resume_run=execution)

    await engine.run(definition, Blackboard.model_validate(execution.checkpoint))

    assert calls == ["a", "b"]
    assert engine.runtime.run is not None
    latest = {step.node_id: step.status for step in engine.runtime.run.steps}
    assert latest == {"step-1": StepStatus.SUCCEEDED, "step-2": StepStatus.SUCCEEDED}


@pytest.mark.asyncio
async def test_linear_engine_can_be_reused_after_success(settings) -> None:
    calls: list[str] = []
    agent = ResumeAgent("a", calls)
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    engine = WorkflowEngine(ctx, resolver={"a": agent}.__getitem__)
    definition = Workflow(name="reusable", steps=[Step(agent="a")])

    await engine.run(definition, Blackboard(query="first"))
    assert engine.runtime.run is not None
    first_run_id = engine.runtime.run.id
    await engine.run(definition, Blackboard(query="second"))

    assert calls == ["a", "a"]
    assert engine.runtime.run is not None
    assert engine.runtime.run.id != first_run_id
    assert engine.runtime.run.status == RunStatus.SUCCEEDED


@pytest.mark.asyncio
@pytest.mark.parametrize("graph", [False, True], ids=["linear", "graph"])
async def test_success_checkpoint_contains_terminal_run_state(settings, graph: bool) -> None:
    checkpoints = []

    async def save_checkpoint(run):  # type: ignore[no-untyped-def]
        checkpoints.append(run.model_copy(deep=True))

    calls: list[str] = []
    agent = ResumeAgent("a", calls)
    tracer = Tracer()
    tracer.restore_metrics(total_tokens=41, estimated_tokens=7, elapsed=3.5)
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=tracer, settings=settings)
    engine = WorkflowEngine(
        ctx,
        resolver={"a": agent}.__getitem__,
        checkpoint_sink=save_checkpoint,
    )
    definition = (
        Workflow(name="terminal-checkpoint", nodes=[{"id": "a", "step": {"agent": "a"}}])
        if graph
        else Workflow(name="terminal-checkpoint", steps=[Step(agent="a")])
    )

    await engine.run(definition, Blackboard(query="Q"))

    assert checkpoints[-1].status == RunStatus.SUCCEEDED
    orchestration = checkpoints[-1].checkpoint["scratch"]["_orchestration_run"]
    assert orchestration["status"] == RunStatus.SUCCEEDED
    assert {"checkpoint", "definition", "steps"}.isdisjoint(orchestration)
    metrics = checkpoints[-1].checkpoint["scratch"]["_runtime_metrics"]
    assert metrics["total_tokens"] == 41
    assert metrics["estimated_tokens"] == 7
    assert metrics["elapsed"] >= 3.5


@pytest.mark.asyncio
@pytest.mark.parametrize("graph", [False, True], ids=["linear", "graph"])
async def test_terminal_snapshot_does_not_nest_across_resumes(settings, graph: bool) -> None:
    calls: list[str] = []
    agent = ResumeAgent("a", calls)
    definition = (
        Workflow(name="stable-checkpoint", nodes=[{"id": "a", "step": {"agent": "a"}}])
        if graph
        else Workflow(name="stable-checkpoint", steps=[Step(agent="a")])
    )
    execution = None

    for _ in range(3):
        ctx = RunContext(
            llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings
        )
        engine = WorkflowEngine(
            ctx,
            resolver={"a": agent}.__getitem__,
            resume_run=execution,
        )
        blackboard = (
            Blackboard.model_validate(execution.checkpoint)
            if execution is not None
            else Blackboard(query="Q")
        )

        await engine.run(definition, blackboard)

        assert engine.runtime.run is not None
        execution = engine.runtime.run.model_copy(deep=True)
        checkpoint = execution.checkpoint
        snapshot = checkpoint["scratch"]["_orchestration_run"]
        assert {"checkpoint", "definition", "steps"}.isdisjoint(snapshot)
        assert json.dumps(checkpoint).count('"_orchestration_run"') == 1

    assert calls == ["a"]


@pytest.mark.asyncio
async def test_nested_graph_steps_use_unique_runtime_node_ids(settings) -> None:
    calls: list[str] = []
    leaf = ResumeAgent("leaf", calls)
    nested = Workflow(
        name="nested-graph",
        nodes=[{"id": "shared", "step": {"agent": "leaf"}}],
    )
    engine: WorkflowEngine

    class NestedGraphAgent:
        async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
            await engine.run(nested, bb)
            return bb

    ctx = RunContext(
        llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings
    )
    agents = {"delegate": NestedGraphAgent(), "leaf": leaf}
    engine = WorkflowEngine(ctx, resolver=agents.__getitem__)
    root = Workflow(
        name="root",
        steps=[Step(agent="delegate"), Step(agent="delegate")],
    )

    await engine.run(root, Blackboard(query="Q"))

    assert engine.runtime.run is not None
    nested_ids = [
        step.node_id
        for step in engine.runtime.run.steps
        if step.agent == "leaf"
    ]
    assert calls == ["leaf", "leaf"]
    assert len(nested_ids) == 2
    assert len(set(nested_ids)) == 2
    assert all(node_id.startswith("nested-") for node_id in nested_ids)


@pytest.mark.asyncio
async def test_initial_persisted_run_keeps_its_runtime_id(settings) -> None:
    definition = Workflow(name="initial", steps=[Step(agent="a")])
    runtime = OrchestrationRuntime()
    execution = runtime.start(definition.name, {"query": "Q"})
    runtime.save_checkpoint(
        Blackboard(query="Q").model_dump(mode="json"),
        definition.model_dump(mode="json"),
    )
    calls: list[str] = []
    tracer = Tracer()
    engine = WorkflowEngine(
        RunContext(
            llm=FakeLLM(), search_tool=FakeSearch(), tracer=tracer, settings=settings
        ),
        resolver={"a": ResumeAgent("a", calls)}.__getitem__,
        initial_run=execution,
    )

    await engine.run(definition, Blackboard.model_validate(execution.checkpoint))

    assert engine.runtime.run is not None
    assert engine.runtime.run.id == execution.id
    assert calls == ["a"]
    lifecycle_names = [event.data.get("event_name") for event in tracer.events]
    assert "workflow.started" in lifecycle_names
    assert "workflow.resumed" not in lifecycle_names
