from __future__ import annotations

import pytest

from deep_research.intent import rules
from deep_research.intent.cascade import IntentCascade, QueryIntentJudgment
from deep_research.intent.routing import plan_route
from deep_research.intent.types import (
    QUERY_INTENTS,
    ContextResolution,
    ConversationTurn,
    IntentDecision,
    IntentSignal,
    IntentSlots,
    execution_policy_for,
    normalize_query_intent,
)
from deep_research.workflows import WORKFLOWS


def test_legacy_labels_are_normalized_without_guessing_unknown_values() -> None:
    assert normalize_query_intent("fact") == "factual_lookup"
    assert normalize_query_intent("fact-check") == "fact_check"
    assert normalize_query_intent("deep research") == "multi_hop_research"
    assert normalize_query_intent("multi hop") == "multi_hop_research"
    assert normalize_query_intent("vendor_private_label") == "unknown"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("literature review of optical reconstruction", "literature_review"),
        ("compare the methods used by these papers", "method_comparison"),
        ("benchmark survey for CASSI", "benchmark_survey"),
        ("check reproducibility of the reported results", "reproducibility_check"),
        ("discover relevant datasets", "dataset_discovery"),
    ],
)
def test_hsi_domain_intents_route_from_stable_phrases(text: str, expected: str) -> None:
    intent, signal = rules.match_query_intent(text)
    assert intent == expected
    assert signal is not None and signal.code.startswith("hsi_")


def test_hsi_domain_intents_use_review_workflow() -> None:
    for intent in (
        "literature_review",
        "method_comparison",
        "benchmark_survey",
        "reproducibility_check",
        "dataset_discovery",
    ):
        decision = IntentDecision(intent=intent, confidence=0.95, tier="rule")
        route = plan_route(decision, available_workflows=set(WORKFLOWS))
        assert route.applied is True
        assert route.workflow == "hsi_review"
        assert route.execution_policy is not None
        assert route.execution_policy.requires_corroboration is True


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("如何部署 Kubernetes 集群", "procedural_guidance"),
        ("推荐哪个向量数据库", "recommendation"),
        ("核实这个说法是否属实", "fact_check"),
        ("总结这份研究的关键结论", "summarization"),
        ("持续跟踪 AI Agent 的最新状态", "monitoring"),
        ("跨领域关联分析芯片和供应链", "multi_hop_research"),
        ("explain the mechanism behind attention collapse", "causal_analysis"),
        ("what causes retrieval noise", "causal_analysis"),
        ("should I use PostgreSQL or MongoDB", "recommendation"),
    ],
)
def test_new_rule_intents_have_stable_signals(text: str, expected: str) -> None:
    intent, signal = rules.match_query_intent(text)
    assert intent == expected
    assert signal is not None and signal.tier == "rule"


def test_execution_policy_is_detached_and_serializable() -> None:
    first = execution_policy_for("recommend")
    first.max_sub_questions = 1
    second = execution_policy_for("recommendation")
    assert second.max_sub_questions == 5
    assert second.workflow == "deep"
    assert second.requires_corroboration is True
    assert second.model_dump(mode="json")["answer_mode"] == "recommendation"


def test_execution_policies_match_their_builtin_workflow_stages() -> None:
    for intent in QUERY_INTENTS:
        if intent == "unknown":
            continue
        policy = execution_policy_for(intent)
        assert policy.workflow in WORKFLOWS
        workflow = WORKFLOWS[policy.workflow]
        has_reflection = any(step.kind == "reflect_loop" for step in workflow.steps)
        assert policy.requires_reflection is has_reflection
        assert ((policy.max_rounds or 0) > 0) is has_reflection


def test_procedural_guidance_has_a_complete_brief_policy() -> None:
    policy = execution_policy_for("procedural_guidance")
    assert policy.workflow == "brief"
    assert policy.max_sub_questions == 3
    assert policy.max_rounds == 0
    assert policy.requires_reflection is False
    assert policy.answer_mode == "procedure"
    assert policy.source_strategy == "official_first"


def test_slots_accept_legacy_aliases_and_preserve_new_constraints() -> None:
    slots = IntentSlots.model_validate(
        {
            "subjects": ["Kafka", "RabbitMQ"],
            "time": "近三年",
            "format": "表格",
            "region": "中国",
            "sources": ["官方", "学术"],
            "recency": "latest",
            "evidence": "strict",
        }
    )
    assert slots.entities == ["Kafka", "RabbitMQ"]
    assert slots.time_range == "近三年"
    assert slots.output_format == "表格"
    assert slots.geography == "中国"
    assert slots.source_types == ["官方", "学术"]
    assert "输出格式：表格" in slots.describe()
    assert not slots.is_empty()


def test_decision_derives_policy_but_keeps_legacy_fields() -> None:
    decision = IntentDecision(intent="fact", confidence=0.9, tier="rule")
    assert decision.intent == "factual_lookup"
    assert decision.execution_policy.workflow == "quick"
    assert decision.policy is decision.execution_policy
    encoded = decision.model_dump(mode="json")
    assert encoded["intent"] == "factual_lookup"
    assert encoded["execution_policy"]["max_rounds"] == 0


def test_old_checkpoint_without_policy_is_upgraded_to_intent_policy() -> None:
    restored = IntentDecision.model_validate(
        {"intent": "fact_check", "confidence": 0.9, "tier": "rule"}
    )
    assert restored.execution_policy.workflow == "fact_check"
    assert restored.execution_policy.requires_corroboration is True


def test_explicit_policy_snapshot_wins_over_current_defaults() -> None:
    restored = IntentDecision.model_validate(
        {
            "intent": "fact_check",
            "confidence": 0.9,
            "tier": "rule",
            "execution_policy": {"workflow": "brief", "max_rounds": 0},
        }
    )
    assert restored.execution_policy.workflow == "brief"
    assert restored.execution_policy.max_rounds == 0


def test_decision_preserves_an_explicit_default_policy() -> None:
    from deep_research.intent.types import ExecutionPolicy

    explicit = ExecutionPolicy()
    decision = IntentDecision(intent="recommendation", execution_policy=explicit)

    assert decision.execution_policy.workflow is None
    assert decision.execution_policy.answer_mode == "report"


def test_route_contains_policy_metadata_and_respects_explicit_workflow() -> None:
    decision = IntentDecision(intent="fact_check", confidence=0.95, tier="rule")
    route = plan_route(decision, available_workflows={"fact_check", "deep"})
    assert route.applied is True
    assert route.workflow == "fact_check"
    assert route.intent == "fact_check"
    assert route.reason_code == "intent_policy"
    assert route.execution_policy is not None
    assert route.to_dict()["execution_policy"]["requires_corroboration"] is True

    explicit = plan_route(decision, requested_workflow="deep")
    assert explicit.applied is False
    assert explicit.reason_code == "explicit_workflow"
    assert explicit.execution_policy is not None


@pytest.mark.asyncio
async def test_llm_legacy_label_is_canonicalized() -> None:
    class LegacyLLM:
        async def parse(self, _system, _user, schema, **_kwargs):
            assert schema is QueryIntentJudgment
            return QueryIntentJudgment(intent="verification", confidence=0.88)

    decision = await IntentCascade(classifier=None, llm=LegacyLLM()).classify_query(
        "请判断这条新闻是否属实"
    )
    assert decision.intent == "fact_check"
    assert decision.execution_policy.workflow == "fact_check"


def test_context_resolution_is_optional_and_round_trips() -> None:
    resolution = ContextResolution(
        raw_query="那第二个呢",
        history_used=[ConversationTurn(query="Kafka 和 RabbitMQ 的区别")],
        dependency_signal=IntentSignal(tier="rule", code="anaphoric_reference"),
        resolved_query="RabbitMQ 的性能如何",
        context_resolved=True,
        resolver_tier="llm",
    )
    decision = IntentDecision(
        intent="factual_lookup",
        context_resolution=resolution,
        context_resolved=True,
        resolved_query=resolution.resolved_query,
    )
    restored = IntentDecision.model_validate(decision.model_dump(mode="json"))
    assert restored.context_resolution is not None
    assert restored.context_resolution.resolver_tier == "llm"
    assert restored.effective_query("原问题") == "RabbitMQ 的性能如何"
