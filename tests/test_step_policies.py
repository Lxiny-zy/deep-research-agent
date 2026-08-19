from __future__ import annotations

import asyncio

import pytest

from deep_research.agents.base import Blackboard, RunContext
from deep_research.observability import Tracer
from deep_research.orchestration import RunStatus, StepStatus
from deep_research.persistence.repository import LeaseLostError
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


class MutatingPolicyAgent:
    def __init__(self, name: str, *, failures: int) -> None:
        self.name = name
        self.failures = failures
        self.calls = 0

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        self.calls += 1
        bb.scratch.setdefault("attempt_outputs", []).append(f"{self.name}:{self.calls}")
        if self.calls <= self.failures:
            raise RuntimeError(f"{self.name} failure {self.calls}")
        return bb


class BlockingAgent:
    name = "blocking"

    def __init__(self, started: asyncio.Event, cancelled: asyncio.Event) -> None:
        self.started = started
        self.cancelled = cancelled

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return bb


class FailAfterSignalAgent:
    name = "broken"

    def __init__(self, started: asyncio.Event) -> None:
        self.started = started

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        await self.started.wait()
        raise RuntimeError("branch failure")


class ScratchAgent:
    def __init__(
        self,
        name: str,
        *,
        append_item: str | None = None,
        pop_last: bool = False,
        completed: asyncio.Event | None = None,
    ) -> None:
        self.name = name
        self.append_item = append_item
        self.pop_last = pop_last
        self.completed = completed

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        if self.append_item is not None:
            bb.scratch.setdefault("items", []).append(self.append_item)
        if self.pop_last:
            bb.scratch["items"].pop()
        bb.scratch[f"result:{self.name}"] = True
        if self.completed is not None:
            self.completed.set()
        return bb


def context(settings) -> RunContext:  # type: ignore[no-untyped-def]
    return RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings)


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
async def test_retry_discards_failed_attempt_blackboard_mutations(settings) -> None:
    flaky = MutatingPolicyAgent("flaky", failures=1)
    engine = WorkflowEngine(context(settings), resolver={"flaky": flaky}.__getitem__)
    initial = Blackboard(query="Q")

    bb = await engine.run(
        Workflow(
            name="isolated-retry",
            steps=[Step(agent="flaky", max_attempts=2, retry_backoff=0)],
        ),
        initial,
    )

    assert flaky.calls == 2
    assert bb is initial
    assert bb.scratch["attempt_outputs"] == ["flaky:2"]


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
async def test_fallback_discards_primary_attempt_blackboard_mutations(settings) -> None:
    primary = MutatingPolicyAgent("primary", failures=1)
    fallback = MutatingPolicyAgent("fallback", failures=0)
    agents = {"primary": primary, "fallback": fallback}
    engine = WorkflowEngine(context(settings), resolver=agents.__getitem__)

    bb = await engine.run(
        Workflow(
            name="isolated-fallback",
            steps=[Step(agent="primary", fallback_agent="fallback")],
        ),
        Blackboard(query="Q"),
    )

    assert primary.calls == 1 and fallback.calls == 1
    assert bb.scratch["attempt_outputs"] == ["fallback:1"]


@pytest.mark.asyncio
async def test_timeout_also_limits_fallback_agent(settings) -> None:
    slow = PolicyAgent("slow", delay=1)
    fallback = PolicyAgent("fallback", delay=1)
    agents = {"slow": slow, "fallback": fallback}
    engine = WorkflowEngine(context(settings), resolver=agents.__getitem__)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            engine.run(
                Workflow(
                    name="fallback-timeout",
                    steps=[
                        Step(
                            agent="slow",
                            timeout_seconds=0.01,
                            fallback_agent="fallback",
                            failure_policy="fail_fast",
                        )
                    ],
                ),
                Blackboard(query="Q"),
            ),
            timeout=0.2,
        )

    assert slow.calls == 1 and fallback.calls == 1
    assert engine.runtime.run is not None
    assert engine.runtime.run.status == RunStatus.FAILED
    assert engine.runtime.run.steps[0].status == StepStatus.FAILED


@pytest.mark.asyncio
async def test_graph_fail_fast_cancels_running_siblings(settings) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()
    agents = {
        "planner": PolicyAgent("planner"),
        "broken": FailAfterSignalAgent(started),
        "blocking": BlockingAgent(started, cancelled),
    }
    engine = WorkflowEngine(context(settings), resolver=agents.__getitem__)
    workflow = Workflow(
        name="parallel-fail-fast",
        nodes=[
            {"id": "planner", "step": {"agent": "planner"}},
            {
                "id": "broken",
                "step": {"agent": "broken", "failure_policy": "fail_fast"},
            },
            {"id": "blocking", "step": {"agent": "blocking"}},
        ],
        edges=[
            {"id": "planner-broken", "source": "planner", "target": "broken"},
            {"id": "planner-blocking", "source": "planner", "target": "blocking"},
        ],
    )

    with pytest.raises(RuntimeError, match="branch failure"):
        await asyncio.wait_for(engine.run(workflow, Blackboard(query="Q")), timeout=0.5)

    assert cancelled.is_set()
    assert engine.runtime.run is not None
    statuses = {step.node_id: step.status for step in engine.runtime.run.steps}
    assert statuses["broken"] == StepStatus.FAILED
    assert statuses["blocking"] == StepStatus.CANCELLED
    assert engine.runtime.run.status == RunStatus.FAILED


@pytest.mark.asyncio
async def test_graph_fail_fast_checkpoints_completed_sibling_output(settings) -> None:
    completed = asyncio.Event()
    agents = {
        "root": ScratchAgent("root"),
        "fast": ScratchAgent("fast", completed=completed),
        "broken": FailAfterSignalAgent(completed),
    }
    engine = WorkflowEngine(context(settings), resolver=agents.__getitem__)
    workflow = Workflow(
        name="parallel-failure-checkpoint",
        nodes=[
            {"id": "root", "step": {"agent": "root"}},
            {"id": "fast", "step": {"agent": "fast"}},
            {
                "id": "broken",
                "step": {"agent": "broken", "failure_policy": "fail_fast"},
            },
        ],
        edges=[
            {"id": "root-fast", "source": "root", "target": "fast"},
            {"id": "root-broken", "source": "root", "target": "broken"},
        ],
    )

    with pytest.raises(RuntimeError, match="branch failure"):
        await engine.run(workflow, Blackboard(query="Q"))

    assert engine.runtime.run is not None
    assert engine.runtime.run.checkpoint["scratch"]["result:fast"] is True


@pytest.mark.asyncio
async def test_graph_parallel_scratch_appends_are_merged(settings) -> None:
    agents = {
        "root": ScratchAgent("root", append_item="root"),
        "left": ScratchAgent("left", append_item="left"),
        "right": ScratchAgent("right", append_item="right"),
    }
    engine = WorkflowEngine(context(settings), resolver=agents.__getitem__)
    workflow = Workflow(
        name="parallel-scratch-merge",
        nodes=[
            {"id": "root", "step": {"agent": "root"}},
            {"id": "left", "step": {"agent": "left"}},
            {"id": "right", "step": {"agent": "right"}},
        ],
        edges=[
            {"id": "root-left", "source": "root", "target": "left"},
            {"id": "root-right", "source": "root", "target": "right"},
        ],
    )

    bb = await engine.run(workflow, Blackboard(query="Q"))

    assert bb.scratch["items"] == ["root", "left", "right"]
    assert bb.scratch["result:left"] is True
    assert bb.scratch["result:right"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "branch_order",
    [("append", "pop"), ("pop", "append")],
    ids=["append-first", "pop-first"],
)
async def test_graph_parallel_pop_preserves_sibling_append(
    settings, branch_order: tuple[str, str]
) -> None:
    agents = {
        "root": ScratchAgent("root", append_item="root"),
        "append": ScratchAgent("append", append_item="new"),
        "pop": ScratchAgent("pop", pop_last=True),
    }
    engine = WorkflowEngine(context(settings), resolver=agents.__getitem__)
    workflow = Workflow(
        name="parallel-destructive-list-merge",
        nodes=[
            {"id": "root", "step": {"agent": "root"}},
            *[{"id": branch, "step": {"agent": branch}} for branch in branch_order],
        ],
        edges=[
            {"id": f"root-{branch}", "source": "root", "target": branch} for branch in branch_order
        ],
    )

    bb = await engine.run(workflow, Blackboard(query="Q"))

    assert bb.scratch["items"] == ["new"]
    assert bb.scratch["result:append"] is True
    assert bb.scratch["result:pop"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("graph", [False, True], ids=["linear", "graph"])
async def test_lease_loss_during_cancel_does_not_mask_cancellation(settings, graph: bool) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()
    blocking = BlockingAgent(started, cancelled)

    async def rejected_checkpoint(run):  # type: ignore[no-untyped-def]
        raise LeaseLostError("lease moved to successor")

    engine = WorkflowEngine(
        context(settings),
        resolver={"blocking": blocking}.__getitem__,
        checkpoint_sink=rejected_checkpoint,
    )
    workflow = (
        Workflow(name="cancelled", nodes=[{"id": "a", "step": {"agent": "blocking"}}])
        if graph
        else Workflow(name="cancelled", steps=[Step(agent="blocking")])
    )
    task = asyncio.create_task(engine.run(workflow, Blackboard(query="Q")))
    await started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert cancelled.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("graph", [False, True], ids=["linear", "graph"])
async def test_lease_loss_during_failure_does_not_mask_original_error(
    settings, graph: bool
) -> None:
    """失败终态 checkpoint 遇租约丢失：吞掉 LeaseLostError，保留原始异常向上传播。"""
    broken = PolicyAgent("broken", failures=10)

    async def rejected_checkpoint(run):  # type: ignore[no-untyped-def]
        raise LeaseLostError("lease moved to successor")

    engine = WorkflowEngine(
        context(settings),
        resolver={"broken": broken}.__getitem__,
        checkpoint_sink=rejected_checkpoint,
    )
    workflow = (
        Workflow(
            name="failed",
            nodes=[{"id": "a", "step": {"agent": "broken", "failure_policy": "fail_fast"}}],
        )
        if graph
        else Workflow(name="failed", steps=[Step(agent="broken", failure_policy="fail_fast")])
    )

    with pytest.raises(RuntimeError, match="broken failure"):
        await engine.run(workflow, Blackboard(query="Q"))

    assert engine.runtime.run is not None
    assert engine.runtime.run.status == RunStatus.FAILED


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
