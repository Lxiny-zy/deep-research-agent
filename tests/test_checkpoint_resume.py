from __future__ import annotations

import pytest

from deep_research.agents.base import Blackboard, RunContext
from deep_research.observability import Tracer
from deep_research.orchestration import OrchestrationRuntime, StepStatus
from deep_research.workflow import Workflow, WorkflowEngine
from tests.fakes import FakeLLM, FakeSearch


class ResumeAgent:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        self.calls.append(self.name)
        bb.scratch[self.name] = True
        return bb


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
