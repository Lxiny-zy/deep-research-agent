"""上下文消解的可回放性。

线上投诉是「系统把刚才那个数据库理解错了」。这份记录要能把责任定位到具体一层：

1. ``raw_query`` / ``history_used`` 不对 → 输入或客户端会话的问题；
2. ``dependency_signal`` 为空但本轮确实依赖上文 → L1 检测漏了；
3. 信号对但 ``resolved_query`` 错 → L3 改写错了。

只记录「消解后是什么」是不够的——那三种情况看起来完全一样。
"""

from __future__ import annotations

import pytest

from deep_research.intent import context
from deep_research.intent.cascade import IntentCascade
from deep_research.intent.types import ConversationTurn, IntentSlots


class _Rewriter:
    """按需返回改写结果的假 LLM。"""

    def __init__(self, resolved: str, reason: str = "把「第二个」替换为 Qdrant") -> None:
        self._resolved = resolved
        self._reason = reason
        self.prompts: list[str] = []

    async def parse(self, system, user, schema, **kwargs):  # type: ignore[no-untyped-def]
        self.prompts.append(user)
        return schema(resolved=self._resolved, needed_context=True, reason=self._reason)


class _Failing:
    async def parse(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("upstream 503")


def _history(turns: int = 1) -> list[ConversationTurn]:
    return [
        ConversationTurn(
            query=f"对比 Milvus 和 Qdrant（第{i}轮）",
            intent="comparative_analysis",
            slots=IntentSlots(),
        )
        for i in range(turns)
    ]


@pytest.mark.asyncio
async def test_successful_resolution_records_every_stage() -> None:
    llm = _Rewriter("Qdrant 在 RAG 场景的表现如何")

    outcome = await context.resolve_followup_detailed("那第二个呢", _history(), llm=llm)

    assert outcome.resolved is True
    assert outcome.query == "Qdrant 在 RAG 场景的表现如何"
    assert outcome.tier == "llm"
    assert outcome.signal is not None
    assert outcome.signal.code == "anaphoric_reference"
    assert outcome.reason == "把「第二个」替换为 Qdrant", "模型给的理由必须透传，不能被固定文案覆盖"
    assert [turn.query for turn in outcome.history_used] == ["对比 Milvus 和 Qdrant（第0轮）"]


@pytest.mark.asyncio
async def test_history_used_matches_what_the_rewriter_actually_saw() -> None:
    """回放里的历史窗口必须等于真正喂进改写器的那几轮，而不是客户端传了几轮。"""
    llm = _Rewriter("完整问题")
    history = _history(turns=6)

    outcome = await context.resolve_followup_detailed("那第二个呢", history, llm=llm)

    assert len(outcome.history_used) == context.MAX_HISTORY_TURNS
    rendered = context.render_history(history)
    for turn in outcome.history_used:
        assert turn.query in rendered
    # 窗口外的轮次不得出现在记录里，否则会误导排查方向
    assert history[0].query not in [turn.query for turn in outcome.history_used]


@pytest.mark.asyncio
async def test_self_contained_query_is_distinguishable_from_a_failed_rewrite() -> None:
    """两者都「没消解」，但一个是本来就不用消解，一个是想消解没成。"""
    llm = _Rewriter("不会被用到")

    outcome = await context.resolve_followup_detailed("向量数据库的原理是什么", _history(), llm=llm)

    assert outcome.resolved is False
    assert outcome.signal is None
    assert outcome.tier == "none"
    assert "自足" in outcome.reason
    assert llm.prompts == [], "自足的问题不该花一次 LLM 调用"


@pytest.mark.asyncio
async def test_detected_dependency_without_a_rewriter_is_marked_fallback() -> None:
    outcome = await context.resolve_followup_detailed("那第二个呢", _history(), llm=None)

    assert outcome.resolved is False
    assert outcome.tier == "fallback"
    assert outcome.signal is not None, "检测到了依赖就必须留下信号，哪怕没能改写"
    assert "不可用" in outcome.reason


@pytest.mark.asyncio
async def test_rewriter_failure_records_the_exception_type() -> None:
    outcome = await context.resolve_followup_detailed("那第二个呢", _history(), llm=_Failing())

    assert outcome.resolved is False
    assert outcome.tier == "fallback"
    assert "RuntimeError" in outcome.reason, "排查时要能区分调用失败与模型判定无需改写"


@pytest.mark.asyncio
async def test_no_history_needs_no_resolution() -> None:
    outcome = await context.resolve_followup_detailed("那第二个呢", [], llm=_Rewriter("x"))

    assert outcome.resolved is False
    assert outcome.history_used == []
    assert outcome.tier == "none"


@pytest.mark.asyncio
async def test_legacy_tuple_api_still_works() -> None:
    """旧调用方（含既有测试）不应因为新增回放结构而改写。"""
    resolved, signal, did = await context.resolve_followup(
        "那第二个呢", _history(), llm=_Rewriter("Qdrant 怎么样")
    )

    assert (resolved, did) == ("Qdrant 怎么样", True)
    assert signal is not None


@pytest.mark.asyncio
async def test_cascade_persists_the_replay_record() -> None:
    """判定结果里必须带着这份记录——它要跟着 checkpoint 一起落库。"""
    cascade = IntentCascade(llm=_Rewriter("Qdrant 在 RAG 场景的表现如何"), enable_llm=True)

    decision = await cascade.classify("那第二个呢", history=_history())

    record = decision.context_resolution
    assert record is not None
    assert record.raw_query == "那第二个呢"
    assert record.resolved_query == "Qdrant 在 RAG 场景的表现如何"
    assert record.context_resolved is True
    assert record.resolver_tier == "llm"
    assert record.dependency_signal is not None
    assert record.reason == "把「第二个」替换为 Qdrant"
    assert len(record.history_used) == 1


@pytest.mark.asyncio
async def test_replay_record_survives_serialization() -> None:
    """记录要能进 checkpoint 再原样读回来，否则崩溃恢复后就无法回放。"""
    cascade = IntentCascade(llm=_Rewriter("完整问题"), enable_llm=True)
    decision = await cascade.classify("那第二个呢", history=_history())

    payload = decision.model_dump(mode="json")
    restored = type(decision).model_validate(payload)

    assert restored.context_resolution is not None
    assert restored.context_resolution.raw_query == "那第二个呢"
    assert restored.context_resolution.resolver_tier == "llm"
    assert restored.context_resolution.history_used[0].query.startswith("对比 Milvus")


@pytest.mark.asyncio
async def test_cascade_records_a_missed_rewrite_as_fallback() -> None:
    """L3 挂掉时判定仍然产生，但记录必须说明这是在信息不全下做出的。"""
    cascade = IntentCascade(llm=_Failing(), enable_llm=True)

    decision = await cascade.classify("那第二个呢", history=_history())

    record = decision.context_resolution
    assert record is not None
    assert record.context_resolved is False
    assert record.resolver_tier == "fallback"
    assert record.dependency_signal is not None
