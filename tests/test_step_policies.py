from __future__ import annotations

import asyncio

import pytest

from deep_research.agents.base import Blackboard, RunContext
from deep_research.observability import Tracer
from deep_research.orchestration import RunStatus, StepStatus
from deep_research.workflow import Step, Workflow, WorkflowEngine
from tests.fakes import FakeLLM, FakeSearch


class PolicyAgent:
    def __init__(self, name: str, *, failures: int = 0, delay: float = 0) -> None:
        self.name = name
        self.failures = failures
        self.delay = delay
        self.calls = 0

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.calls <= self.failures:
            raise RuntimeError(f"{self.name} failure {self.calls}")
        bb.scratch["completed_by"] = self.name
        return bb


def context(settings) -> RunContext:  # type: ignore[no-untyped-def]
    return RunContext(
        llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings
    )


@pytest.mark.asyncio
async def test_step_retries_then_succeeds(settings) -> None:
    flaky = PolicyAgent("flaky", failures=2)
    engine = WorkflowEngine(context(settings), resolver={"flaky": flaky}.__getitem__)
    bb = await engine.run(
        Workflow(
            name="retry",
            steps=[Step(agent="flaky", max_attempts=3, retry_backoff=0)],
        ),
        Blackboard(query="Q"),
    )

    assert flaky.calls == 3
    assert bb.scratch["completed_by"] == "flaky"
    assert engine.runtime.run is not None
    step = engine.runtime.run.steps[0]
    assert step.status == StepStatus.SUCCEEDED
    assert step.attempt == 3


@pytest.mark.asyncio
async def test_timeout_uses_fallback_agent(settings) -> None:
    slow = PolicyAgent("slow", delay=0.05)
    fallback = PolicyAgent("fallback")
    agents = {"slow": slow, "fallback": fallback}
    engine = WorkflowEngine(context(settings), resolver=agents.__getitem__)
    bb = await engine.run(
        Workflow(
            name="fallback",
            steps=[Step(agent="slow", timeout_seconds=0.01, fallback_agent="fallback")],
        ),
        Blackboard(query="Q"),
    )

    assert slow.calls == 1 and fallback.calls == 1
    assert bb.scratch["completed_by"] == "fallback"
    assert engine.runtime.run is not None
    assert engine.runtime.run.steps[0].status == StepStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_fail_fast_marks_workflow_failed(settings) -> None:
    broken = PolicyAgent("broken", failures=10)
    checkpoints = []

    async def save_checkpoint(run):  # type: ignore[no-untyped-def]
        checkpoints.append(run.model_copy(deep=True))

    engine = WorkflowEngine(
        context(settings),
        resolver={"broken": broken}.__getitem__,
        checkpoint_sink=save_checkpoint,
    )

    with pytest.raises(RuntimeError, match="broken failure"):
        await engine.run(
            Workflow(
                name="fail-fast",
                steps=[Step(agent="broken", failure_policy="fail_fast")],
            ),
            Blackboard(query="Q"),
        )

    assert engine.runtime.run is not None
    assert engine.runtime.run.status == RunStatus.FAILED
    assert engine.runtime.run.steps[0].status == StepStatus.FAILED
    assert checkpoints[-1].status == RunStatus.FAILED
