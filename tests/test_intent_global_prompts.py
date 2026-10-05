"""Leaf intent calls retain factual boundaries without executor-only directives."""

from __future__ import annotations

import pytest

from deep_research.intent import context, readiness, slots
from deep_research.intent.context import ResolvedQuery
from deep_research.intent.readiness import ClarifyOptions, Readiness
from deep_research.intent.slots import SlotExtraction
from deep_research.intent.types import ConversationTurn, IntentSlots
from deep_research.prompting import LEAF_FACT_RULES, compose_system_prompt, load_global_rules


class _CapturingLLM:
    def __init__(self, parameter_mode: str = "reasoning") -> None:
        self.system_prompts: list[str] = []
        self.options: list[dict] = []
        self.parameter_mode = parameter_mode
        self.reasoning_effort = "high"

    async def parse(self, system, user, schema, **kwargs):  # type: ignore[no-untyped-def]
        self.system_prompts.append(system)
        self.options.append(kwargs)
        if schema is ResolvedQuery:
            return ResolvedQuery(resolved="Qdrant 的性能如何", needed_context=True)
        if schema is SlotExtraction:
            return SlotExtraction(entities=["Qdrant"])
        if schema is ClarifyOptions:
            return ClarifyOptions(question="想研究什么方向？", options=["性能"])
        raise AssertionError(f"unexpected schema: {schema!r}")


def _assert_leaf_contract(llm: _CapturingLLM, role_marker: str) -> None:
    system = llm.system_prompts[0]
    assert role_marker in system
    assert system.count(LEAF_FACT_RULES) == 1
    assert "## 当前任务契约" in system
    assert load_global_rules() not in system
    assert "work/<slug>" not in system
    assert "checkpoint" not in system
    assert "XeLaTeX" not in system
    assert llm.options[0].get("reasoning_effort") == (
        "low" if llm.parameter_mode == "reasoning" else None
    )
    assert llm.reasoning_effort == "high"


def test_prompt_heading_alone_cannot_suppress_global_rules() -> None:
    """A custom role prompt must not spoof the policy heading."""

    rules = load_global_rules()
    prompt = compose_system_prompt("role text\n## global orchestration rules", rules)

    assert rules in prompt
    # The caller's heading is preserved, while the canonical payload is
    # appended exactly once.
    assert prompt.count(rules) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("parameter_mode", ["reasoning", "temperature", ""])
async def test_context_resolution_uses_leaf_contract(parameter_mode: str) -> None:
    llm = _CapturingLLM(parameter_mode)
    history = [ConversationTurn(query="对比 Milvus 和 Qdrant", slots=IntentSlots())]

    await context.resolve_followup_detailed("那第二个呢", history, llm=llm)

    assert len(llm.system_prompts) == 1
    _assert_leaf_contract(llm, "多轮对话的指代消解器")


@pytest.mark.asyncio
@pytest.mark.parametrize("parameter_mode", ["reasoning", "temperature", ""])
async def test_slot_extraction_uses_leaf_contract(parameter_mode: str) -> None:
    llm = _CapturingLLM(parameter_mode)

    await slots.extract_slots("推荐 Kafka 和 RabbitMQ", llm=llm, use_llm=True)

    assert len(llm.system_prompts) == 1
    _assert_leaf_contract(llm, "研究请求的槽位抽取器")


@pytest.mark.asyncio
@pytest.mark.parametrize("parameter_mode", ["reasoning", "temperature", ""])
async def test_clarification_options_use_leaf_contract(parameter_mode: str) -> None:
    llm = _CapturingLLM(parameter_mode)
    verdict = Readiness(gap="direction", question="想研究什么方向？")

    await readiness.llm_options("帮我看看", verdict, IntentSlots(), llm=llm)

    assert len(llm.system_prompts) == 1
    _assert_leaf_contract(llm, "研究系统的澄清助手")
