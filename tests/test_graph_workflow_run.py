from __future__ import annotations

import asyncio

import pytest

from deep_research.agents.base import Blackboard, RunContext
from deep_research.models import Report, ResearchResult
from deep_research.observability import Tracer
from deep_research.orchestration import StepStatus
from deep_research.workflow import Workflow, WorkflowEngine
from tests.fakes import FakeLLM, FakeSearch


class GraphAgent:
    def __init__(self, name: str) -> None:
        self.name = name

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        if self.name.startswith("research"):
            await asyncio.sleep(0.01)
            bb.results.append(ResearchResult(sub_question=self.name))
        if self.name == "router":
            bb.scratch["route"] = "a"
        if self.name == "synthesizer":
            bb.report = Report(
                query=bb.query,
                markdown=",".join(result.sub_question for result in bb.results),
            )
        return bb


@pytest.mark.asyncio
async def test_graph_engine_runs_parallel_branch_then_merge(settings) -> None:
    tracer = Tracer()
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=tracer, settings=settings)
    names = ["planner", "research-a", "research-b", "synthesizer"]
    agents = {name: GraphAgent(name) for name in names}
    nodes = [{"id": name, "step": {"kind": "agent", "agent": name}} for name in names]
    edges = [
        {"id": "p-a", "source": "planner", "target": "research-a"},
        {"id": "p-b", "source": "planner", "target": "research-b"},
        {"id": "a-s", "source": "research-a", "target": "synthesizer"},
        {"id": "b-s", "source": "research-b", "target": "synthesizer"},
    ]
    engine = WorkflowEngine(ctx, resolver=agents.__getitem__)
    bb = await engine.run(
        Workflow(name="branch-merge", nodes=nodes, edges=edges), Blackboard(query="Q")
    )

    assert [result.sub_question for result in bb.results] == ["research-a", "research-b"]
    assert bb.report is not None and bb.report.markdown == "research-a,research-b"
    assert engine.runtime.run is not None
    assert [step.node_id for step in engine.runtime.run.steps] == names
    assert all(step.status == StepStatus.SUCCEEDED for step in engine.runtime.run.steps)
    graph_layers = [
        event.data for event in tracer.events if event.data and "graph_layer" in event.data
    ]
    assert [layer["nodes"] for layer in graph_layers] == [
        ["planner"],
        ["research-a", "research-b"],
        ["synthesizer"],
    ]


@pytest.mark.asyncio
async def test_graph_conditions_skip_unmatched_branch(settings) -> None:
    tracer = Tracer()
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=tracer, settings=settings)
    names = ["router", "research-a", "research-b", "synthesizer"]
    agents = {name: GraphAgent(name) for name in names}
    nodes = [{"id": name, "step": {"kind": "agent", "agent": name}} for name in names]
    edges = [
        {
            "id": "r-a",
            "source": "router",
            "target": "research-a",
            "condition": 'state.scratch.route == "a"',
        },
        {
            "id": "r-b",
            "source": "router",
            "target": "research-b",
            "condition": 'state.scratch.route == "b"',
        },
        {"id": "a-s", "source": "research-a", "target": "synthesizer"},
        {"id": "b-s", "source": "research-b", "target": "synthesizer"},
    ]
    engine = WorkflowEngine(ctx, resolver=agents.__getitem__)
    bb = await engine.run(
        Workflow(name="conditional", nodes=nodes, edges=edges), Blackboard(query="Q")
    )

    assert [result.sub_question for result in bb.results] == ["research-a"]
    assert bb.report is not None and bb.report.markdown == "research-a"
    assert engine.runtime.run is not None
    statuses = {step.node_id: step.status for step in engine.runtime.run.steps}
    assert statuses["research-a"] == StepStatus.SUCCEEDED
    assert statuses["research-b"] == StepStatus.SKIPPED
    assert statuses["synthesizer"] == StepStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_all_join_waits_for_every_active_input(settings) -> None:
    tracer = Tracer()
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=tracer, settings=settings)
    names = ["router", "research-a", "research-b", "synthesizer"]
    agents = {name: GraphAgent(name) for name in names}
    nodes = [
        {
            "id": name,
            "step": {"kind": "agent", "agent": name},
            "join_mode": "all" if name == "synthesizer" else "any",
        }
        for name in names
    ]
    edges = [
        {
            "id": "r-a",
            "source": "router",
            "target": "research-a",
            "condition": 'state.scratch.route == "a"',
        },
        {
            "id": "r-b",
            "source": "router",
            "target": "research-b",
            "condition": 'state.scratch.route == "b"',
        },
        {"id": "a-s", "source": "research-a", "target": "synthesizer"},
        {"id": "b-s", "source": "research-b", "target": "synthesizer"},
    ]
    engine = WorkflowEngine(ctx, resolver=agents.__getitem__)
    bb = await engine.run(
        Workflow(name="strict-join", nodes=nodes, edges=edges), Blackboard(query="Q")
    )

    assert bb.report is None
    assert engine.runtime.run is not None
    statuses = {step.node_id: step.status for step in engine.runtime.run.steps}
    assert statuses["synthesizer"] == StepStatus.SKIPPED
