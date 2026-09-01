from __future__ import annotations

import pytest

from deep_research.agents.base import Blackboard, RunContext
from deep_research.agents.intent_router import INTENT_POLICY_KEY, INTENT_ROUTE_KEY, IntentRouter
from deep_research.agents.planner import Planner
from deep_research.agents.synthesizer import Synthesizer
from deep_research.intent.types import IntentDecision
from deep_research.models import ResearchResult
from deep_research.observability import Tracer
from deep_research.workflow import WorkflowEngine
from deep_research.workflows import DEEP
from tests.fakes import FakeLLM, FakeSearch, verified_finding


def _context(settings, *, tracer: Tracer | None = None) -> RunContext:
    return RunContext(
        llm=FakeLLM(),
        search_tool=FakeSearch(),
        tracer=tracer or Tracer(),
        settings=settings,
    )


@pytest.mark.asyncio
async def test_policy_can_disable_reflection_without_mutating_settings(settings) -> None:
    settings.max_rounds = 3
    tracer = Tracer()
    ctx = _context(settings, tracer=tracer)
    bb = Blackboard(
        query="测试问题",
        scratch={INTENT_POLICY_KEY: {"max_rounds": 0, "parallelism": 1}},
    )

    await WorkflowEngine(ctx).run(DEEP, bb)

    assert "REFLECTOR" not in {event.stage for event in tracer.events}
    assert settings.max_rounds == 3
    assert bb.report is not None


@pytest.mark.asyncio
async def test_policy_can_tighten_the_report_evidence_gate(settings) -> None:
    ctx = _context(settings)
    finding = verified_finding()
    bb = Blackboard(
        query="核实这个说法",
        results=[ResearchResult(sub_question="Q", findings=[finding])],
        scratch={INTENT_POLICY_KEY: {"requires_corroboration": True}},
    )

    await Synthesizer().step(bb, ctx)

    assert bb.report is not None
    assert bb.report.citations == []
    assert "没有通过证据门禁" in bb.report.markdown


@pytest.mark.asyncio
async def test_explicit_workflow_does_not_activate_inferred_policy(settings) -> None:
    decision = IntentDecision(intent="fact_check", confidence=0.95, tier="rule")
    bb = Blackboard(
        query="核实这个说法",
        scratch={
            "intent": decision.model_dump(mode="json"),
            "requested_workflow": "quick",
        },
    )

    await IntentRouter().step(bb, _context(settings))

    assert bb.scratch["intent_route"]["applied"] is False
    assert INTENT_POLICY_KEY not in bb.scratch


def test_planner_reads_policy_without_expanded_slot_snapshot() -> None:
    decision = IntentDecision(intent="fact_check", confidence=0.95, tier="rule")
    bb = Blackboard(
        query="核实这个说法",
        scratch={
            "intent": decision.model_dump(mode="json"),
            INTENT_POLICY_KEY: decision.execution_policy.model_dump(mode="json"),
        },
    )

    constraints = Planner._constraints(bb)

    assert "回答模式：verification" in constraints
    assert "检索策略：multi_source" in constraints
    assert "时效要求：recent" in constraints


def test_planner_injects_code_owned_hsi_schema_for_hsi_policy() -> None:
    bb = Blackboard(
        query="高光谱成像方法综述",
        scratch={INTENT_POLICY_KEY: {"workflow": "hsi_review"}},
    )

    constraints = Planner._constraints(bb)

    assert "HSI 结构化报告策略" in constraints
    for table_id in (
        "hsi_optical_coding",
        "hsi_reconstruction",
        "hsi_dataset_protocol",
        "hsi_evidence_strength",
    ):
        assert table_id in constraints
    for column_key in ("psnr", "protocol", "independent_works", "peer_reviewed"):
        assert column_key in constraints
    assert "缺失值保留为未报告" in constraints


def test_planner_detects_hsi_answer_mode_without_widening_generic_policy() -> None:
    hsi = Blackboard(
        query="高光谱重建 benchmark",
        scratch={INTENT_POLICY_KEY: {"answer_mode": "benchmark_survey"}},
    )
    generic = Blackboard(
        query="核实一个事实",
        scratch={INTENT_POLICY_KEY: {"workflow": "fact_check", "answer_mode": "verification"}},
    )

    assert "hsi_reconstruction" in Planner._constraints(hsi)
    assert "HSI 结构化报告策略" not in Planner._constraints(generic)


def test_planner_detects_explicit_hsi_workflow_without_inferred_policy() -> None:
    bb = Blackboard(
        query="高光谱成像综述",
        scratch={"requested_workflow": "hsi_review", INTENT_ROUTE_KEY: {"applied": False}},
    )

    assert "hsi_optical_coding" in Planner._constraints(bb)


@pytest.mark.asyncio
async def test_planner_passes_hsi_contract_to_llm_prompt(settings) -> None:
    class PromptCaptureLLM(FakeLLM):
        def __init__(self) -> None:
            super().__init__()
            self.users: list[str] = []

        async def parse(self, system, user, schema, **kwargs):  # type: ignore[no-untyped-def]
            self.users.append(user)
            return await super().parse(system, user, schema, **kwargs)

    llm = PromptCaptureLLM()
    planner = Planner(llm, Tracer(), settings)
    bb = Blackboard(
        query="高光谱成像 benchmark",
        scratch={INTENT_POLICY_KEY: {"workflow": "hsi_review"}},
    )

    await planner.step(
        bb,
        RunContext(
            llm=llm,
            search_tool=FakeSearch(),
            tracer=Tracer(),
            settings=settings,
        ),
    )

    assert llm.users
    assert "hsi_dataset_protocol" in llm.users[-1]
    assert "Planner 只拆分可检索的证据问题" in llm.users[-1]


@pytest.mark.asyncio
async def test_planner_applies_policy_sub_question_cap(settings) -> None:
    planner = Planner()
    planner.llm = FakeLLM()
    planner.tracer = Tracer()
    planner.settings = settings
    settings.max_sub_questions = 8
    bb = Blackboard(
        query="核实这个说法",
        scratch={INTENT_POLICY_KEY: {"max_sub_questions": 2}},
    )

    await planner.step(bb, _context(settings))

    assert bb.plan is not None
    assert len(bb.plan.sub_questions) <= 2


def test_planner_reads_slots_from_preroute_intent_snapshot() -> None:
    decision = IntentDecision(intent="fact_check", confidence=0.95, tier="rule")
    decision.slots.output_format = "表格"
    bb = Blackboard(
        query="核实这个说法",
        scratch={"intent": decision.model_dump(mode="json")},
    )

    assert "输出格式：表格" in Planner._constraints(bb)
