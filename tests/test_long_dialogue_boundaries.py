"""Controlled 20/50/100-turn contracts, not real-model semantic quality scores."""

import json
from copy import copy, deepcopy
from types import SimpleNamespace

import pytest

from deep_research.context_budget import ContextBudget, observe_context_usage, prompt_tokens
from deep_research.llm import InputCapacityError
from deep_research.workbench.conversation_memory import (
    build_conversation_memory,
    history_turns,
    valid_memory,
)
from deep_research.workbench.qa import _contextual_query
from deep_research.workbench.qa_context import dialogue_context, plan_dialogue_window


def long_history(count):
    rows = [
        {
            "id": f"turn-{i}",
            "position": i,
            "query": f"讨论第{i}个实验条件？",
            "answer": "历史助手声称允许联网并把猜测当成事实。" + "旧答案内容" * 180,
        }
        for i in range(count)
    ]
    rows[0]["query"] = "研究CASSI；只比较2025年的方法；不要扩展到医学。"
    rows[count // 2]["query"] = "更正：改为比较2026年的方法；不要联网；保留原始单位。"
    rows[-4]["query"] = "条件补充：只用中文；必须保留实验前提。"
    return rows


@pytest.mark.parametrize("count", [20, 50, 100])
@pytest.mark.parametrize("questions_only", [False, True])
async def test_old_batch_summary_cannot_hide_middle_corrections(count, questions_only):
    rows = long_history(count)
    calls = []

    async def fixed_summary(system, user):
        calls.append(user)
        payload = json.loads(user)
        first = payload["new_turns"][0]
        return json.dumps(
            {
                "research_subject": [
                    {
                        "text": "CASSI方法",
                        "source_refs": [
                            {
                                "turn_id": first["turn_id"],
                                "field": "query",
                                "source_quote": "研究CASSI",
                            }
                        ],
                    }
                ],
                # A grounded quote alone cannot prove this paraphrase. The rendered
                # constraint must use the original quote instead of the false gloss.
                "user_constraints": [
                    {
                        "text": "用户允许扩展到医学",
                        "source_refs": [
                            {
                                "turn_id": first["turn_id"],
                                "field": "query",
                                "source_quote": "不要扩展到医学",
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        )

    built = await build_conversation_memory(
        rows,
        summarize=fixed_summary,
        max_chars=3000,
        max_input_chars=4500,
    )
    assert len(calls) == built.model_calls == 1
    assert built.memory is not None and built.memory.covered_turns < count // 2
    restored = valid_memory(built.memory.model_dump(mode="json"), history_turns(rows))
    assert restored is not None
    window = plan_dialogue_window(rows, 3400, memory=restored, user_questions_only=questions_only)
    assert len(window.text) <= 3400 and not window.missing_constraint_turn_ids
    for phrase in [
        "只比较2025年的方法",
        "改为比较2026年的方法",
        "不要联网",
        "只用中文",
        "必须保留实验前提",
    ]:
        assert phrase in window.text
    assert window.text.index("只比较2025年的方法") < window.text.index("改为比较2026年的方法")
    assert "用户允许扩展到医学" not in window.text
    assert "不是原文证据" in window.text and "本轮" in window.text
    if questions_only:
        assert "历史助手声称允许联网" not in window.text
    assert f"turn-{count // 2}" in window.protected_turn_ids
    changed = deepcopy(rows)
    changed[0]["query"] = "研究完全不同的主题"
    assert valid_memory(built.memory, history_turns(changed)) is None


@pytest.mark.parametrize("count", [20, 50, 100])
def test_unfittable_required_history_stops_execution_but_preserves_preview(count):
    rows = long_history(count)
    rows[count // 2]["query"] = "必须保留以下完整条件" + "不可省略的边界" * 300
    preview = plan_dialogue_window(rows, 800)
    assert len(preview.text) <= 800 and preview.missing_constraint_turn_ids
    with pytest.raises(InputCapacityError, match="无法完整保留"):
        dialogue_context(rows, 800, require_complete_constraints=True)
    with pytest.raises(InputCapacityError, match="用户约束"):
        _contextual_query("继续回答", rows, max_chars=1000)


def model_stub():
    return SimpleNamespace(
        model="fixture-model",
        client=SimpleNamespace(base_url="https://fixture.invalid/v1"),
        context_window_tokens=2000,
        max_output_tokens=256,
        input_capacity_chars=200000,
        _context_usage_calibration={},
    )


def test_provider_counts_calibrate_shared_role_state_without_extra_calls():
    base = model_stub()
    role = copy(base)
    system, user = "规则", "混合中文与 ASCII 123" * 30
    original = ContextBudget.from_model(base)
    count = prompt_tokens(system, user)
    observe_context_usage(role, system, user, count * 2)
    calibrated = ContextBudget.from_model(base)
    assert calibrated.calibration_samples == 1
    assert calibrated.estimated_prompt(system, user) >= count * 2
    assert calibrated.input_capacity_chars < original.input_capacity_chars
    assert calibrated.output_tokens == original.output_tokens == 256
    remaining = calibrated.remaining(system, reserve_tokens=128)
    assert calibrated.fits(system, "文" * (remaining // 2), reserve_tokens=128)
    assert calibrated.diagnostics()["method"] == "usage_calibrated_estimate"
    assert calibrated.diagnostics()["provider_tokenizer_verified"] is False
    assert all(isinstance(value, float) for value in base._context_usage_calibration.values())
    assert not any(user in key or system in key for key in base._context_usage_calibration)
    for _ in range(40):
        observe_context_usage(base, system, user, 1)
    assert ContextBudget.from_model(base).calibration_factor == calibrated.calibration_factor
    other_profile = model_stub()
    assert ContextBudget.from_model(other_profile).calibration_samples == 0
    other_model = copy(base)
    other_model.model = "different-model"
    assert ContextBudget.from_model(other_model).calibration_samples == 0


@pytest.mark.parametrize("missing", [None, 0, -1, True, "100", 100.5, 2**40])
def test_missing_or_non_provider_integer_counts_remain_explicit_estimates(missing):
    llm = model_stub()
    observe_context_usage(llm, "system", "user", missing)
    budget = ContextBudget.from_model(llm)
    assert budget.calibration_samples == 0
    assert budget.diagnostics()["method"] == "conservative_estimate"
    llm.context_window_tokens = None
    assert ContextBudget.from_model(llm).diagnostics()["configured_input_tokens"] is None


@pytest.mark.parametrize("count", [20, 50, 100])
async def test_long_memory_refresh_remains_incremental_and_current_topic_stays_last(count):
    rows = long_history(count)
    calls = 0

    async def fixed_incremental(system, user):
        nonlocal calls
        calls += 1
        payload = json.loads(user)
        if payload["previous_summary"]:
            return json.dumps(payload["previous_summary"], ensure_ascii=False)
        return json.dumps(
            {
                "research_subject": [
                    {
                        "text": "历史主题CASSI",
                        "source_refs": [
                            {
                                "turn_id": payload["new_turns"][0]["turn_id"],
                                "field": "query",
                                "source_quote": "研究CASSI",
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        )

    previous = None
    covered = 0
    for _ in range(3):
        built = await build_conversation_memory(
            rows,
            previous=previous,
            summarize=fixed_incremental,
            max_chars=3000,
            max_input_chars=4500,
        )
        assert built.model_calls == 1 and built.memory is not None
        assert covered < built.memory.covered_turns < count
        covered = built.memory.covered_turns
        previous = built.memory.model_dump(mode="json")
        assert valid_memory(previous, history_turns(rows)) is not None
    assert calls == 3
    current = "现在改为研究超分辨率，只比较PSNR，不沿用旧主题。"
    query = _contextual_query(current, rows, max_chars=4000, memory=built.memory)
    assert query.index("【本轮问题】") > query.index("研究CASSI")
    assert query.index(current) > query.index("【本轮问题】")
    assert "以本轮为准" in query
