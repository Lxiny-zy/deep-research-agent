from __future__ import annotations

import pytest

from deep_research.agents.base import Blackboard
from deep_research.models import Reflection, ResearchPlan, SubQuestion
from deep_research.orchestration import evaluate_condition


def test_condition_reads_blackboard_paths_and_literals() -> None:
    bb = Blackboard(query="Q", scratch={"route": "deep"})
    bb.reflections.append(Reflection(is_sufficient=False))

    assert evaluate_condition('state.scratch.route == "deep"', bb)
    assert evaluate_condition("state.reflections.last.is_sufficient == false", bb)
    assert evaluate_condition("state.reflections.length >= 1", bb)
    assert not evaluate_condition("state.report != null", bb)


def test_condition_path_resolution_is_total() -> None:
    """路径解析是全函数：黑板形态不匹配（dict 取 last、对象取 length）按 None 处理。"""
    bb = Blackboard(query="Q", scratch={"route": "deep"})
    bb.plan = ResearchPlan(interpretation="i", sub_questions=[SubQuestion(question="q")])

    assert not evaluate_condition("state.scratch.last", bb)  # 非空 dict 取 last 不抛 KeyError
    assert not evaluate_condition("state.plan.length >= 1", bb)  # 对象取 length 不抛 TypeError
    assert not evaluate_condition("state.plan.sub_questions.0", bb)  # 未知下标段按 None


def test_condition_rejects_code_and_unquoted_strings() -> None:
    with pytest.raises(ValueError, match="条件表达式非法|JSON 字面量"):
        evaluate_condition("__import__('os').system('x')", {})
    with pytest.raises(ValueError, match="JSON 字面量"):
        evaluate_condition("state.scratch.route == deep", {})
