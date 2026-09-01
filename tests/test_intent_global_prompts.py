"""辅助意图 LLM 调用也必须携带运行时全局编排规则。"""

from __future__ import annotations

import pytest

from deep_research.intent import context, readiness, slots
from deep_research.intent.context import ResolvedQuery
from deep_research.intent.readiness import ClarifyOptions, Readiness
from deep_research.intent.slots import SlotExtraction
from deep_research.intent.types import ConversationTurn, IntentSlots
from deep_research.prompting import compose_system_prompt, load_global_rules


class _CapturingLLM:
    def __init__(self) -> None:
        self.system_prompts: list[str] = []

    async def parse(self, system, user, schema, **kwargs):  # type: ignore[no-untyped-def]
        self.system_prompts.append(system)
        if schema is ResolvedQuery:
            return ResolvedQuery(resolved="Qdrant 的性能如何", needed_context=True)
        if schema is SlotExtraction:
            return SlotExtraction(entities=["Qdrant"])
        if schema is ClarifyOptions:
            return ClarifyOptions(question="想研究什么方向？", options=["性能"])
        raise AssertionError(f"unexpected schema: {schema!r}")


def _assert_global_rules(system: str, role_marker: str) -> None:
    rules = load_global_rules()
    assert role_marker in system
    assert "## 全局编排规则" in system
    assert rules in system


def test_prompt_heading_alone_cannot_suppress_global_rules() -> None:
    """A custom role prompt must not spoof the policy heading."""

    rules = load_global_rules()
    prompt = compose_system_prompt("role text\n## global orchestration rules", rules)

    assert rules in prompt
    # The caller's heading is preserved, while the canonical payload is
    # appended exactly once.
    assert prompt.count(rules) == 1


@pytest.mark.asyncio
async def test_context_resolution_uses_global_rules() -> None:
    llm = _CapturingLLM()
    history = [ConversationTurn(query="对比 Milvus 和 Qdrant", slots=IntentSlots())]

    await context.resolve_followup_detailed("那第二个呢", history, llm=llm)

    assert len(llm.system_prompts) == 1
    _assert_global_rules(llm.system_prompts[0], "多轮对话的指代消解器")


@pytest.mark.asyncio
async def test_slot_extraction_uses_global_rules() -> None:
    llm = _CapturingLLM()

    await slots.extract_slots("推荐 Kafka 和 RabbitMQ", llm=llm, use_llm=True)

    assert len(llm.system_prompts) == 1
    _assert_global_rules(llm.system_prompts[0], "研究请求的槽位抽取器")


@pytest.mark.asyncio
async def test_clarification_options_use_global_rules() -> None:
    llm = _CapturingLLM()
    verdict = Readiness(gap="direction", question="想研究什么方向？")

    await readiness.llm_options("帮我看看", verdict, IntentSlots(), llm=llm)

    assert len(llm.system_prompts) == 1
    _assert_global_rules(llm.system_prompts[0], "研究系统的澄清助手")
