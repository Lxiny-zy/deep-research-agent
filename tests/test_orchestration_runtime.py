from __future__ import annotations

import pytest

from deep_research.agents.base import Blackboard, RunContext
from deep_research.observability import Tracer
from deep_research.orchestration import RunStatus, StepRun, StepStatus
from deep_research.token_budget import TokenBudget
from deep_research.workflow import Step, Workflow, WorkflowEngine
from tests.fakes import FakeLLM, FakeSearch


class RecordingAgent:
    def __init__(self, name: str, calls: list[str], *, fail: bool = False) -> None:
        self.name = name
        self.calls = calls
        self.fail = fail

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        self.calls.append(self.name)
        if self.fail:
            raise RuntimeError(f"{self.name} exploded")
        return bb


def test_step_run_rejects_invalid_state_transition() -> None:
    step = StepRun(node_id="step-1", label="planner", kind="agent", agent="planner")
    with pytest.raises(ValueError, match="invalid step transition"):
        step.transition(StepStatus.SUCCEEDED)


@pytest.mark.asyncio
async def test_workflow_engine_records_failed_step_and_continues(settings) -> None:
    calls: list[str] = []
    agents = {
        "broken": RecordingAgent("broken", calls, fail=True),
        "synthesizer": RecordingAgent("synthesizer", calls),
    }
    tracer = Tracer()
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=tracer, settings=settings)
    engine = WorkflowEngine(ctx, resolver=agents.__getitem__)
    bb = Blackboard(query="test")

    await engine.run(
        Workflow(
            name="failure-isolation",
            steps=[
                Step(agent="broken"),
                Step(agent="synthesizer"),
            ],
        ),
        bb,
    )

    assert calls == ["broken", "synthesizer"]
    assert engine.runtime.run is not None
    assert engine.runtime.run.status == RunStatus.SUCCEEDED
    assert [step.status for step in engine.runtime.run.steps] == [
        StepStatus.FAILED,
        StepStatus.SUCCEEDED,
    ]
    assert bb.scratch["_orchestration_run"]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_budget_exhaustion_records_skip_but_runs_terminal(settings) -> None:
    calls: list[str] = []
    agents = {
        "researcher": RecordingAgent("researcher", calls),
        "synthesizer": RecordingAgent("synthesizer", calls),
    }
    tracer = Tracer()
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=tracer, settings=settings)
    engine = WorkflowEngine(ctx, resolver=agents.__getitem__, budget=TokenBudget(max_tokens=0))

    await engine.run(
        Workflow(
            name="budgeted",
            steps=[Step(agent="researcher"), Step(agent="synthesizer")],
        ),
        Blackboard(query="test"),
    )

    assert calls == ["synthesizer"]
    assert engine.runtime.run is not None
    assert [step.status for step in engine.runtime.run.steps] == [
        StepStatus.SKIPPED,
        StepStatus.SUCCEEDED,
    ]
    lifecycle = [
        event.data["event_name"]
        for event in tracer.events
        if event.data and "event_name" in event.data
    ]
    plan = next(
        event
        for event in tracer.events
        if event.data and event.data.get("event_name") == "workflow.plan"
    )
    assert plan.data is not None
    assert plan.data["total_steps"] == 2
    assert "step.skipped" in lifecycle
    assert lifecycle[-1] == "workflow.succeeded"
