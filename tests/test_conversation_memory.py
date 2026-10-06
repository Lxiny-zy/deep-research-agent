import asyncio
import json
from copy import deepcopy

import pytest

from deep_research.workbench.conversation_memory import (
    SUMMARY_SYSTEM,
    build_conversation_memory,
    history_turns,
    plan_memory_update,
    valid_memory,
)
from deep_research.workbench.qa_context import dialogue_context, plan_dialogue_window


def history(count=6, size=500):
    return [
        {"id": f"m{i}", "position": i, "query": f"CASSI问题{i}：只比较2026年的方法",
         "answer": f"旧结论{i}未经本轮验证。" + "正文" * size}
        for i in range(count)
    ]


def grounded_reply(payload):
    turn = payload["new_turns"][0]
    return json.dumps({
        "research_subject": [{"text": "研究CASSI方法", "source_refs": [{
            "turn_id": turn["turn_id"], "field": "query", "source_quote": "CASSI",
        }]}],
        "user_constraints": [{"text": "用户限定2026年", "source_refs": [{
            "turn_id": turn["turn_id"], "field": "query", "source_quote": "只比较2026年的方法",
        }]}],
        "prior_conclusions": [], "open_questions": [],
    }, ensure_ascii=False)


async def summarize(system, user):
    assert "原文证据" in system
    return grounded_reply(json.loads(user))


async def test_short_dialogue_does_not_spend_a_summary_call():
    async def forbidden(*_args):
        raise AssertionError("must not call the model")

    result = await build_conversation_memory(history(2, 2), summarize=forbidden, max_chars=2000)
    assert result.status == "not_needed" and result.model_calls == 0 and result.memory is None


async def test_one_bounded_incremental_batch_and_valid_memory_reuse():
    rows = history()
    original = deepcopy(rows)
    calls = []

    async def capture(system, user):
        calls.append(json.loads(user))
        assert len(system) + len(user) <= 10000
        return grounded_reply(calls[-1])

    first = await build_conversation_memory(
        rows, summarize=capture, max_chars=1800, max_input_chars=10000,
    )
    assert first.memory is not None and first.memory.covered_turns == 3
    ref = first.memory.research_subject[0].source_refs[0]
    assert ref.turn_id == "m0" and ref.position == 0 and len(ref.content_hash) == 64
    assert first.memory.to_thought()["tool"] == "conversation_memory"
    assert first.memory.processed_source_refs[0].answer_ranges == [(0, len(rows[0]["answer"]))]
    again = await build_conversation_memory(
        rows, summarize=capture, previous=first.memory, max_chars=1800,
    )
    assert again.status == "reused" and again.model_calls == 0 and len(calls) == 1
    newer = await build_conversation_memory(
        history(8), summarize=capture, previous=first.memory, max_chars=1800,
    )
    assert newer.memory is not None and newer.memory.covered_turns == 5
    assert [row["turn_id"] for row in calls[1]["new_turns"]] == ["m3", "m4"]
    assert calls[1]["previous_summary"]["research_subject"]
    assert newer.memory.previous_memory_hash is not None
    assert [s.turn_id for s in newer.memory.processed_source_refs] == ["m3", "m4"]
    assert rows == original


async def test_changed_history_invalidates_even_uncited_prefix_turns():
    rows = history()
    first = await build_conversation_memory(rows, summarize=summarize, max_chars=1500)
    assert first.memory is not None
    rows[1]["answer"] += "更正了旧结论"
    assert valid_memory(first.memory, history_turns(rows)) is None
    rebuilt = await build_conversation_memory(
        rows, summarize=summarize, previous=first.memory, max_chars=1500,
    )
    assert rebuilt.previous_invalidated and rebuilt.memory is not None
    assert rebuilt.memory.prefix_hash != first.memory.prefix_hash


@pytest.mark.parametrize("bad_source", [
    {"turn_id": "m0", "field": "query", "source_quote": "原文没有这段话"},
    {"turn_id": "m5", "field": "query", "source_quote": "CASSI"},
    {"turn_id": "missing", "field": "query", "source_quote": "CASSI"},
    {"turn_id": "m0", "field": "answer", "source_quote": "旧结论0"},
])
async def test_unseen_or_fabricated_sources_and_assistant_constraints_are_rejected(bad_source):
    async def bad(_system, _user):
        return json.dumps({"user_constraints": [{"text": "一个约束", "source_refs": [bad_source]}]})

    result = await build_conversation_memory(history(), summarize=bad, max_chars=1500)
    assert result.status == "summary_failed" and result.memory is None and result.retryable


async def test_summary_failure_retains_valid_previous_memory_without_fake_replacement():
    first = await build_conversation_memory(history(), summarize=summarize, max_chars=1500)

    async def broken(_system, _user):
        raise RuntimeError("provider details must not appear in status")

    result = await build_conversation_memory(
        history(7), previous=first.memory, summarize=broken, max_chars=1500,
    )
    assert result.memory == first.memory and result.retryable and result.model_calls == 1
    assert result.status == "summary_failed" and result.failure_type == "RuntimeError"
    assert "provider details" not in json.dumps(result.to_thought())


async def test_cancellation_propagates_without_persisting_a_failure_summary():
    async def cancelled(_system, _user):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await build_conversation_memory(history(), summarize=cancelled, max_chars=1500)


async def test_oversized_old_turn_is_explicitly_partial_but_can_make_progress():
    rows = history(6, 6000)
    plan = plan_memory_update(rows, max_chars=1600, max_input_chars=2300)
    assert plan.user_prompt is not None
    assert len(SUMMARY_SYSTEM) + len(plan.user_prompt) <= 2300
    assert plan.excerpted_ids == frozenset({"m0"}) and len(plan.selected_turns) == 1
    assert json.loads(plan.user_prompt)["new_turns"][0]["excerpted"] is True
    result = await build_conversation_memory(
        rows, summarize=summarize, max_chars=1600, max_input_chars=2300,
    )
    assert result.memory is not None and result.memory.excerpted_turns == 1
    assert result.memory.research_subject[0].source_refs[0].excerpted
    source = result.memory.processed_source_refs[0]
    assert source.turn_id == "m0" and len(source.answer_ranges) == 2
    assert source.answer_ranges[0][1] < source.answer_ranges[1][0]
    assert source.answer_ranges[1][1] == len(rows[0]["answer"])
    context = dialogue_context(rows, 2200, memory=result.memory)
    assert "部分摘录" in context and "不能视为完整覆盖" in context
    assert "未纳入当前窗口" in context


def test_too_small_summary_budget_defers_without_building_an_unbounded_prompt():
    plan = plan_memory_update(history(), max_chars=1500, max_input_chars=100)
    assert plan.user_prompt is None and plan.status == "deferred_input_budget"


async def test_persisted_source_boundaries_are_validated_before_memory_reuse():
    rows = history()
    result = await build_conversation_memory(rows, summarize=summarize, max_chars=1500)
    assert result.memory is not None
    malformed = result.memory.model_dump(mode="json")
    malformed["processed_source_refs"][0]["query_ranges"] = [[0, 999999]]
    assert valid_memory(malformed, history_turns(rows)) is None


async def test_memory_rendering_preserves_source_quotes_and_non_evidence_boundary():
    result = await build_conversation_memory(history(), summarize=summarize, max_chars=1500)
    assert result.memory is not None
    window = plan_dialogue_window(history(), 2200, memory=result.memory)
    assert window.memory_used and "可追溯语义摘要" in window.text
    assert "不是原文证据" in window.text and "未在本轮重新核验" in window.text
    assert "第1轮问#" in window.text and "CASSI" in window.text
    assert "第 6 轮 问" in window.text and len(window.text) <= 2200


async def test_search_projection_excludes_assistant_conclusions_and_keeps_user_context():
    async def with_conclusion(_system, user):
        payload = json.loads(user)
        reply = json.loads(grounded_reply(payload))
        reply["prior_conclusions"] = [{"text": "尚未验证的旧论断", "source_refs": [{
            "turn_id": "m0", "field": "answer", "source_quote": "旧结论0未经本轮验证。",
        }]}]
        return json.dumps(reply, ensure_ascii=False)

    rows = history()
    result = await build_conversation_memory(rows, summarize=with_conclusion, max_chars=1500)
    assert result.memory is not None
    context = dialogue_context(rows, 400, memory=result.memory, user_questions_only=True)
    assert "旧结论" not in context and "尚未验证的旧论断" not in context
    assert "CASSI" in context and "2026" in context


async def test_crowded_memory_prioritizes_new_user_constraint_over_more_topics():
    rows = history()

    async def crowded(_system, user):
        reply = json.loads(grounded_reply(json.loads(user)))
        first_topic = reply["research_subject"][0]
        first_topic["text"] = "研究主题细节" * 40
        reply["research_subject"] = [first_topic] + [
            {**first_topic, "text": f"额外研究主题{i}" * 10} for i in range(4)
        ]
        reply["user_constraints"] = [{
            "text": "最新用户明确要求只比较2026年的方法",
            "source_refs": [{
                "turn_id": "m2", "field": "query", "source_quote": "只比较2026年的方法",
            }],
        }]
        return json.dumps(reply, ensure_ascii=False)

    result = await build_conversation_memory(rows, summarize=crowded, max_chars=1500)
    assert result.memory is not None
    context = dialogue_context(rows, 1500, memory=result.memory)
    assert "用户明确约束：只比较2026年的方法" in context
    assert "第3轮问#" in context and "部分摘要条目未纳入" in context
    assert len(context) <= 1500


@pytest.mark.parametrize("limit", [0, 1, 30, 80, 150, 250, 500, 1500])
def test_oversized_recent_turn_never_raises_or_exceeds_window(limit):
    rows = [{"query": "请研究CASSI。" + "问题细节" * 2000 + "最后约束：只要中文",
             "answer": "很长的旧答案" * 2000}]
    context = dialogue_context(rows, limit)
    assert len(context) <= limit
    if limit >= 150:
        assert "摘录" in context and "摘要" in context
        assert "最后约束" in context


def test_window_keeps_contiguous_complete_recent_turns_and_initial_question():
    rows = [
        {"id": "first", "query": "原始研究对象CASSI", "answer": "不要跳过中间轮次取这个短答案"},
        {"id": "middle", "query": "详细条件", "answer": "超长中间答案" * 2000},
        {"id": "latest", "query": "最新约束只用中文", "answer": "完整最新回答"},
    ]
    window = plan_dialogue_window(rows, 400)
    assert window.recent_turn_ids == ("latest",)
    assert window.omitted_turn_ids == ("first", "middle")
    assert "原始研究对象CASSI" in window.text and "完整最新回答" in window.text
    assert "这个短答案" not in window.text and "第 1—2 轮未纳入" in window.text
