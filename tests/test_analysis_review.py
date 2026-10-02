from __future__ import annotations

import copy

import pytest

from deep_research.workbench.analysis_review import count_scope_issues, sample_size_scopes

LEDGER = {
    "describe": [{"variable": "length", "n": 149}, {"variable": "width", "n": 150}],
    "tests": [
        {
            "variable": "length",
            "group": "species",
            "group_summaries": [
                {"label": "A", "n": 49},
                {"label": "B", "n": 50},
                {"label": "C", "n": 50},
            ],
        },
        {
            "variable": "width",
            "group": "species",
            "group_summaries": [
                {"label": "A", "n": 50},
                {"label": "B", "n": 50},
                {"label": "C", "n": 50},
            ],
        },
    ],
    "correlations": [{"a": "length", "b": "width", "n": 149}],
}


@pytest.mark.parametrize(
    "body",
    [
        "其余变量及其分组均为 n=150。",
        "各组n=150。",
        "每组的样本量均为150。",
        "Each group has n=150.",
        "All groups have sample size 150.",
    ],
)
def test_aggregate_count_cannot_be_reused_for_any_recorded_group(body):
    assert count_scope_issues(body, LEDGER)


@pytest.mark.parametrize(
    "body",
    [
        "总体 n=150，width 各组 n=50。",
        "各组的总样本量为150。",
        "各组样本量之和 n=150。",
        "是否每组 n=150？",
        "每组样本量不是150。",
        "如果各组n=150，需要重新计算。",
        "Each group has n=50.",
        "The total sample size of all groups is 150.",
        "各组样本量 n=150/3=50。",
    ],
)
def test_count_scope_check_preserves_correct_aggregate_questions_and_qualifiers(body):
    assert not count_scope_issues(body, LEDGER)


def test_structured_count_evidence_keeps_variable_group_and_pair_roles_distinct():
    scopes = sample_size_scopes(LEDGER)
    assert {r["n"] for r in scopes if r["scope"] == "single_group"} == {49, 50}
    assert {r["n"] for r in scopes if r["scope"] == "variable_total"} == {149, 150}
    assert [r["n"] for r in scopes if r["scope"] == "variable_pair"] == [149]
    assert not count_scope_issues("每组 n=150。", {"describe": LEDGER["describe"]})


async def test_optimistic_model_and_forged_stored_pass_cannot_override_count_contradiction():
    from deep_research.workbench.prose_review import reviewer_for_report
    from tests.fakes import FakeLLM

    ledger = {**LEDGER, "facts": "样本总数=150；length 组内 n=49,50,50；width 组内 n=50,50,50"}
    checker = reviewer_for_report(
        FakeLLM(),
        "比较各组",
        [],
        [],
        {"workbench": {"template": "dataAnalysis"}, "analysis": ledger},
        50000,
    )
    body = "其余变量及其分组均为 n=150。"
    audit = await checker.review(body)
    assert audit["status"] == "fail" and audit["can_revise"]
    assert any("不能把总体样本量用于各分组" in issue for issue in audit["issues"])
    forged = copy.deepcopy(audit)
    forged.update(status="pass", issues=[])
    bound, issues = checker.check(body, forged)
    assert bound and any("不能把总体样本量用于各分组" in issue for issue in issues)
