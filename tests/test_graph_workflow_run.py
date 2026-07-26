from __future__ import annotations

import asyncio

import pytest

from deep_research.agents.base import Blackboard, RunContext
from deep_research.models import (
    Report,
    ResearchPlan,
    ResearchResult,
    Source,
    SubQuestion,
)
from deep_research.observability import Tracer
from deep_research.orchestration import StepStatus
from deep_research.workflow import Workflow, WorkflowEngine, _merge_parallel_sequence
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


def test_merge_parallel_sequence_keeps_both_appends_when_both_rewrite_baseline() -> None:
    """双分支都改写基线前缀（如各自跑 claim consistency）且各自追加：追加均不得丢。"""
    base = [{"id": 1, "v": "old"}]
    current = [{"id": 1, "v": "left"}, {"id": 2, "v": "L"}]  # 先合并的分支
    branch = [{"id": 1, "v": "right"}, {"id": 3, "v": "R"}]  # 后合并的分支

    merged = _merge_parallel_sequence(base, current, branch)

    assert merged == [
        {"id": 1, "v": "right"},  # 基线段按 branch 的逐元素改写覆盖
        {"id": 2, "v": "L"},  # current 的追加保留
        {"id": 3, "v": "R"},  # branch 的追加保留
    ]


class RewriteAppendAgent:
    """模拟 claim consistency 场景：原地改写基线结果，同时追加自己的新结果。"""

    def __init__(self, name: str) -> None:
        self.name = name

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        if self.name == "seed":
            bb.results.append(ResearchResult(sub_question="base"))
            return bb
        bb.results[0] = ResearchResult(sub_question=f"base-rewritten-{self.name}")
        bb.results.append(ResearchResult(sub_question=self.name))
        return bb


@pytest.mark.asyncio
async def test_graph_parallel_branches_rewriting_baseline_keep_all_appends(settings) -> None:
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    names = ["seed", "left", "right"]
    agents = {name: RewriteAppendAgent(name) for name in names}
    nodes = [{"id": name, "step": {"kind": "agent", "agent": name}} for name in names]
    edges = [
        {"id": "s-l", "source": "seed", "target": "left"},
        {"id": "s-r", "source": "seed", "target": "right"},
    ]
    engine = WorkflowEngine(ctx, resolver=agents.__getitem__)

    bb = await engine.run(
        Workflow(name="rewrite-merge", nodes=nodes, edges=edges), Blackboard(query="Q")
    )

    questions = [result.sub_question for result in bb.results]
    assert len(questions) == 3
    assert questions[0].startswith("base-rewritten-")  # 基线被其中一个分支的改写覆盖
    assert {"left", "right"} <= set(questions)  # 双方的追加段均保留


class ConcurrencyProbeSearch(FakeSearch):
    """统计并发峰值的检索替身：检验 run 级共享限流是否被并行节点放大。"""

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0

    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.02)
            return await super().search(query, max_results=max_results)
        finally:
            self.active -= 1


class SeedPlanAgent:
    name = "seed"

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        bb.plan = ResearchPlan(
            interpretation="t",
            sub_questions=[SubQuestion(question=f"子问题{i}") for i in range(3)],
        )
        return bb


@pytest.mark.asyncio
async def test_parallel_researcher_nodes_share_run_level_concurrency_limit(settings) -> None:
    settings.max_concurrency = 2
    probe = ConcurrencyProbeSearch()
    ctx = RunContext(llm=FakeLLM(), search_tool=probe, tracer=Tracer(), settings=settings)

    def resolve(name: str):  # type: ignore[no-untyped-def]
        from deep_research.registry import create

        return SeedPlanAgent() if name == "seed" else create(name)

    # 3 个并行 researcher 节点 × 每节点 3 个子问题：若各节点各建信号量，峰值可达 6
    nodes = [
        {"id": "seed", "step": {"kind": "agent", "agent": "seed"}},
        *[
            {"id": f"research-{i}", "step": {"kind": "agent", "agent": "researcher"}}
            for i in range(3)
        ],
    ]
    edges = [
        {"id": f"s-{i}", "source": "seed", "target": f"research-{i}"} for i in range(3)
    ]
    engine = WorkflowEngine(ctx, resolver=resolve)

    await engine.run(
        Workflow(name="parallel-researchers", nodes=nodes, edges=edges),
        Blackboard(query="Q"),
    )

    assert probe.peak <= settings.max_concurrency


@pytest.mark.asyncio
async def test_graph_edge_condition_error_skips_branch_instead_of_failing_run(
    settings,
) -> None:
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
            # 运行期才暴露的非法字面量（未加引号）：求值抛 ValueError
            "condition": "state.scratch.route == deep",
        },
        {"id": "r-b", "source": "router", "target": "research-b"},
        {"id": "a-s", "source": "research-a", "target": "synthesizer"},
        {"id": "b-s", "source": "research-b", "target": "synthesizer"},
    ]
    engine = WorkflowEngine(ctx, resolver=agents.__getitem__)

    bb = await engine.run(
        Workflow(name="broken-condition", nodes=nodes, edges=edges), Blackboard(query="Q")
    )

    # 坏边按不激活处理：run 正常走完，仅该分支被跳过
    assert bb.report is not None and bb.report.markdown == "research-b"
    assert engine.runtime.run is not None
    statuses = {step.node_id: step.status for step in engine.runtime.run.steps}
    assert statuses["research-a"] == StepStatus.SKIPPED
    assert statuses["synthesizer"] == StepStatus.SUCCEEDED
    warnings = [
        event
        for event in tracer.events
        if event.data is not None and event.data.get("event_name") == "edge.condition_error"
    ]
    assert warnings and warnings[0].data is not None
    assert warnings[0].data["edge"] == "r-a"


@pytest.mark.asyncio
async def test_graph_condition_on_untyped_paths_does_not_fail_run(settings) -> None:
    """scratch（非空 dict）取 last、对象取 length：路径解析按 None 处理，不炸 run。"""
    tracer = Tracer()
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=tracer, settings=settings)
    names = ["router", "research-a", "synthesizer"]
    agents = {name: GraphAgent(name) for name in names}
    nodes = [{"id": name, "step": {"kind": "agent", "agent": name}} for name in names]
    edges = [
        {
            "id": "r-a",
            "source": "router",
            "target": "research-a",
            "condition": "state.scratch.last",
        },
        {"id": "a-s", "source": "research-a", "target": "synthesizer"},
    ]
    engine = WorkflowEngine(ctx, resolver=agents.__getitem__)

    bb = await engine.run(
        Workflow(name="untyped-condition", nodes=nodes, edges=edges), Blackboard(query="Q")
    )

    assert engine.runtime.run is not None
    statuses = {step.node_id: step.status for step in engine.runtime.run.steps}
    assert statuses["research-a"] == StepStatus.SKIPPED  # 解析为 None → falsy → 跳过
    assert bb.report is None


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
