"""链路设计回归：反思循环逐轮提交、零发现子问题留痕与去重、前驱上下文时序、
运行降级标记、全文展开保序并发。"""

from __future__ import annotations

import asyncio

import pytest

from deep_research.agents.base import Blackboard, RunContext
from deep_research.agents.reflector import _digest, _fruitless
from deep_research.agents.researcher import Researcher
from deep_research.models import Reflection, ResearchResult, Source, SubQuestion
from deep_research.observability import Tracer
from deep_research.tools.fanout import expand_in_order
from deep_research.workflow import (
    ATTEMPTED_SCRATCH_KEY,
    Step,
    Workflow,
    WorkflowEngine,
    _unseen_sub_questions,
)
from tests.fakes import FakeLLM, FakeSearch, verified_finding


class ScriptedReflector:
    """按脚本逐轮返回反思结果；脚本项为异常时抛出。"""

    name = "reflector"

    def __init__(self, script: list[Reflection | Exception]) -> None:
        self.script = list(script)
        self.calls = 0

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        bb.reflections.append(item)
        return bb


class ScriptedResearcher:
    """为每个待研究子问题产出一条合格发现，可指定某些问题零发现。"""

    name = "researcher"

    def __init__(self, *, empty: set[str] | None = None) -> None:
        self.empty = empty or set()
        self.seen: list[str] = []

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        pending = bb.scratch.pop("pending_sub_questions", None) or []
        attempted = bb.scratch.setdefault(ATTEMPTED_SCRATCH_KEY, {})
        for sq in pending:
            self.seen.append(sq.question)
            if sq.question in self.empty:
                attempted[sq.question] = 0
                continue
            attempted[sq.question] = 1
            bb.results.append(
                ResearchResult(
                    sub_question=sq.question,
                    findings=[verified_finding(statement=f"关于{sq.question}的发现")],
                )
            )
        return bb


def _gap(*questions: str) -> Reflection:
    return Reflection(is_sufficient=False, gaps=["缺口"], new_sub_questions=list(questions))


def _engine(settings, agents: dict) -> tuple[WorkflowEngine, Tracer]:
    tracer = Tracer()
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=tracer, settings=settings)
    return WorkflowEngine(ctx, resolver=agents.__getitem__), tracer


def _loop_workflow(max_rounds: int) -> Workflow:
    return Workflow(name="loop", steps=[Step(kind="reflect_loop", max_rounds=max_rounds)])


def _seed() -> Blackboard:
    bb = Blackboard(query="Q")
    bb.results.append(ResearchResult(sub_question="初始", findings=[verified_finding()]))
    return bb


@pytest.mark.asyncio
async def test_reflector_failure_keeps_evidence_from_earlier_rounds(settings) -> None:
    reflector = ScriptedReflector([_gap("补洞一"), RuntimeError("llm down")])
    researcher = ScriptedResearcher()
    engine, tracer = _engine(settings, {"reflector": reflector, "researcher": researcher})

    bb = await engine.run(_loop_workflow(3), _seed())

    # 第 2 轮反思失败，但第 1 轮补到的证据必须保留下来。
    assert [r.sub_question for r in bb.results] == ["初始", "补洞一"]
    assert len(bb.scratch["reflection_rounds"]) == 1
    assert any("反思失败" in e.message for e in tracer.events if e.type == "error")
    step = engine.runtime.run.steps[0]
    assert step.status == "succeeded"


@pytest.mark.asyncio
async def test_researcher_failure_mid_loop_keeps_committed_rounds(settings) -> None:
    class FlakyResearcher(ScriptedResearcher):
        async def step(self, bb, ctx):
            if self.seen:
                raise RuntimeError("search down")
            return await super().step(bb, ctx)

    reflector = ScriptedReflector([_gap("补洞一"), _gap("补洞二")])
    engine, _ = _engine(settings, {"reflector": reflector, "researcher": FlakyResearcher()})

    bb = await engine.run(_loop_workflow(3), _seed())

    assert [r.sub_question for r in bb.results] == ["初始", "补洞一"]
    # 失败那一轮不应留下半截的轮次记录或残留的待研究队列。
    assert len(bb.scratch["reflection_rounds"]) == 1
    assert "pending_sub_questions" not in bb.scratch


@pytest.mark.asyncio
async def test_loop_skips_already_attempted_questions(settings) -> None:
    reflector = ScriptedReflector([_gap("无果方向"), _gap(" 无果方向？ ", "初始")])
    researcher = ScriptedResearcher(empty={"无果方向"})
    engine, tracer = _engine(settings, {"reflector": reflector, "researcher": researcher})

    await engine.run(_loop_workflow(3), _seed())

    # 第 1 轮零发现后收敛停止；即便没停，第 2 轮的问题也全是研究过的。
    assert researcher.seen == ["无果方向"]


@pytest.mark.asyncio
async def test_loop_stops_when_round_adds_no_eligible_evidence(settings) -> None:
    reflector = ScriptedReflector([_gap("无果方向"), _gap("新方向")])
    researcher = ScriptedResearcher(empty={"无果方向"})
    engine, tracer = _engine(settings, {"reflector": reflector, "researcher": researcher})

    await engine.run(_loop_workflow(3), _seed())

    assert reflector.calls == 1
    assert any("未新增合格证据" in e.message for e in tracer.events)


@pytest.mark.asyncio
async def test_loop_stops_early_when_budget_exhausted(settings) -> None:
    from deep_research.token_budget import TokenBudget

    reflector = ScriptedReflector([_gap("补洞一")])
    tracer = Tracer()
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=tracer, settings=settings)
    budget = TokenBudget(max_tokens=10)
    budget.charge(10)
    engine = WorkflowEngine(
        ctx,
        resolver={"reflector": reflector, "researcher": ScriptedResearcher()}.__getitem__,
        budget=budget,
    )
    # reflect_loop 不是终端步骤：直接走 _reflect_loop 验证循环内的预算检查。
    bb = _seed()
    await engine._reflect_loop(Step(kind="reflect_loop", max_rounds=3), bb)

    assert reflector.calls == 0


def test_unseen_sub_questions_dedupes_against_every_prior_source() -> None:
    bb = Blackboard(query="Q")
    from deep_research.models import ResearchPlan

    bb.plan = ResearchPlan(interpretation="i", sub_questions=[SubQuestion(question="计划内")])
    bb.results.append(ResearchResult(sub_question="有结果", findings=[verified_finding()]))
    bb.scratch[ATTEMPTED_SCRATCH_KEY] = {"零发现": 0}
    bb.scratch["reflection_rounds"] = [{"round": 1, "sub_questions": [{"question": "上一轮"}]}]

    fresh = _unseen_sub_questions(
        bb, ["计划内？", "有 结果", "零发现", "上一轮", "全新", "全新。", ""]
    )

    assert [sq.question for sq in fresh] == ["全新"]


def test_digest_groups_by_question_so_late_questions_are_not_truncated() -> None:
    results = [
        ResearchResult(
            sub_question=f"子问题{i}",
            findings=[verified_finding(statement=f"发现{i}-{j}") for j in range(20)],
        )
        for i in range(4)
    ]

    digest = _digest(results, limit=40)

    for i in range(4):
        assert f"■ 子问题{i}（合格发现 20 条）" in digest
    assert "另有" in digest


def test_fruitless_lists_attempted_questions_without_eligible_findings() -> None:
    results = [ResearchResult(sub_question="有结果", findings=[verified_finding()])]
    assert _fruitless(results, {"有结果": 1, "零发现": 0}) == ["零发现"]
    assert _fruitless(results, None) == []


@pytest.mark.asyncio
async def test_prior_findings_reach_dependent_layer_with_corroboration_on(settings) -> None:
    """开启 corroboration 时，未印证的前驱发现仍须作为后层背景。"""
    settings.require_corroboration = True
    prompts: list[str] = []

    class RecordingLLM(FakeLLM):
        async def parse(self, system, user, schema, **kwargs):
            prompts.append(user)
            return await super().parse(system, user, schema, **kwargs)

    researcher = Researcher(
        llm=RecordingLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings
    )
    # verified + supported 但尚未经过交叉印证：正是后层调度时前驱发现的真实状态。
    prior = verified_finding(statement="前驱结论")
    prior.verification.corroboration_status = "not_checked"

    await researcher.run("后层问题", context_findings=[prior], require_corroboration=True)

    assert any("前驱结论" in prompt for prompt in prompts)


@pytest.mark.asyncio
async def test_researcher_step_records_zero_finding_questions(settings) -> None:
    class EmptySearch(FakeSearch):
        async def search(self, query, *, max_results=5):
            return [] if query == "空" else await super().search(query, max_results=max_results)

    tracer = Tracer()
    ctx = RunContext(llm=FakeLLM(), search_tool=EmptySearch(), tracer=tracer, settings=settings)
    bb = Blackboard(query="Q")
    bb.scratch["pending_sub_questions"] = [SubQuestion(question="空"), SubQuestion(question="有")]

    bb = await Researcher().step(bb, ctx)

    assert [r.sub_question for r in bb.results] == ["有"]
    assert bb.scratch[ATTEMPTED_SCRATCH_KEY] == {"空": 0, "有": 1}


@pytest.mark.asyncio
async def test_failed_step_marks_run_degraded(settings) -> None:
    class Broken:
        name = "planner"

        async def step(self, bb, ctx):
            raise RuntimeError("planner down")

    class Writer:
        name = "synthesizer"

        async def step(self, bb, ctx):
            from deep_research.models import Report

            bb.report = Report(query=bb.query, markdown="（无可用素材）")
            return bb

    engine, tracer = _engine(settings, {"planner": Broken(), "synthesizer": Writer()})
    await engine.run(
        Workflow(name="w", steps=[Step(agent="planner"), Step(agent="synthesizer")]),
        Blackboard(query="Q"),
    )

    output = engine.runtime.run.output
    assert output["degraded"] is True
    assert output["failed_steps"] == ["planner"]
    assert any(
        isinstance(e.data, dict) and e.data.get("event_name") == "run.degraded"
        for e in tracer.events
    )


@pytest.mark.asyncio
async def test_expand_in_order_is_concurrent_ordered_and_stops_early() -> None:
    started: list[int] = []
    in_flight = 0
    peak = 0

    async def expand(item: int) -> list[str]:
        nonlocal in_flight, peak
        started.append(item)
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01 * (3 - item % 3))  # 乱序完成
        in_flight -= 1
        return [f"{item}a", f"{item}b"]

    out = await expand_in_order(list(range(10)), expand, limit=5, window=3)

    assert out == ["0a", "0b", "1a", "1b", "2a"]
    assert peak == 3
    assert started == [0, 1, 2]  # 凑够 limit 后不再展开后续窗口


@pytest.mark.asyncio
async def test_source_intent_screening_runs_concurrently(settings, monkeypatch) -> None:
    from deep_research.agents import researcher as researcher_module

    settings.intent_source_screening = True
    in_flight = 0
    peak = 0

    async def slow_screen(source, decision):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return decision

    monkeypatch.setattr(researcher_module, "screen_source_intent", slow_screen)

    class ManySearch(FakeSearch):
        async def search(self, query, *, max_results=5):
            return [
                Source(title=str(i), url=f"https://s{i}.com", content="内容A提供了可核验的原文证据")
                for i in range(4)
            ]

    researcher = Researcher(
        llm=FakeLLM(), search_tool=ManySearch(), tracer=Tracer(), settings=settings
    )
    await researcher.run("问题")

    assert peak == 4
