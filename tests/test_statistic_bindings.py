"""A numeric value must belong to the variable, group and statistic being asserted."""

import copy

import pytest

LEDGER = {
    "rows": 5,
    "describe": [{"variable": "mass", "n": 5, "mean": 16, "std": 6.1644, "min": 8, "max": 23}],
    "tests": [
        {
            "variable": "mass",
            "group": "species",
            "method": "Welch t 检验",
            "p_value": 0.02,
            "statistic": 3.2,
            "n_total": 5,
            "group_summaries": [
                {"label": "A", "n": 2, "mean": 10, "std": 2.8284, "median": 10},
                {"label": "B", "n": 3, "mean": 20, "std": 3, "median": 20},
            ],
        }
    ],
    "correlations": [],
}


@pytest.mark.parametrize(
    "body",
    [
        "A 组 mass 均值为20，B 组 mass 均值为10。",
        "mass 在 A 组的样本量 n=3。",
        "mass 各组 n=3。",
        "mass 在 A、B 两组的均值分别为20、10。",
        "A 组 mass 均值为10，B 组为10。",
        "A 组 mass 标准差为10。",
        "mass各组n=3。",
        "A组mass平均为20。",
    ],
)
def test_values_cannot_be_borrowed_from_another_group_or_statistic(body):
    from deep_research.workbench.analysis_review import statistic_scope_issues

    assert statistic_scope_issues(body, LEDGER)


@pytest.mark.parametrize(
    "body",
    [
        "A 组 mass 均值为10，B 组 mass 均值为20。",
        "mass 在 A 组的样本量 n=2，B 组 n=3。",
        "mass 在 A、B 两组的均值分别为10、20。",
        "A 组 mass 均值为10，B 组为20。",
        "A 组 mass 标准差约为2.83。",
        "mass 的总体均值为16，有效样本量 n=5。",
        "是否 A 组 mass 均值为20？",
        "显著性阈值设置为0.05。",
    ],
)
def test_correct_scope_rounding_and_nonassertive_numbers_remain_valid(body):
    from deep_research.workbench.analysis_review import statistic_scope_issues

    assert not statistic_scope_issues(body, LEDGER)


async def test_model_approval_and_cached_pass_cannot_hide_swapped_group_means():
    from deep_research.workbench.prose_review import reviewer_for_report
    from tests.fakes import FakeLLM

    checker = reviewer_for_report(
        FakeLLM(),
        "比较 mass 的组间差异",
        [],
        [],
        {"workbench": {"template": "dataAnalysis"}, "analysis": LEDGER},
        50000,
    )
    body = "A 组 mass 均值为20，B 组 mass 均值为10。"
    audit = await checker.review(body)
    assert audit["status"] == "fail" and audit["can_revise"]
    assert any("均值" in issue or "mean" in issue for issue in audit["issues"])
    forged = copy.deepcopy(audit)
    forged.update(status="pass", issues=[])
    bound, issues = checker.check(body, forged)
    assert bound and issues


@pytest.mark.parametrize("kind", ["grouped", "paired", "composition"])
def test_program_generated_facts_and_fallback_preserve_all_recorded_scopes(kind):
    from deep_research.workbench.analysis import analyse, fallback_report
    from deep_research.workbench.analysis_review import statistic_scope_issues
    from deep_research.workbench.analysis_scope import AnalysisScope
    from tests.test_analysis_pairing import paired_scope

    if kind == "paired":
        result = analyse(
            "before_mm,after_mm\n10,12\n20,21\n30,34",
            scope=paired_scope().model_dump(),
        )
    else:
        csv = "group,sex,x,y\nA,F,1,2\nA,M,2,4\nA,F,3,5\nB,M,4,7\nB,F,5,9\nB,M,6,10"
        scope = AnalysisScope(measures=["x", "y"], groups=["group"], background=["sex"])
        result = analyse(csv, scope=scope.model_dump() if kind == "composition" else None)
    assert statistic_scope_issues(result.facts(), result.snapshot()) == []
    assert statistic_scope_issues(fallback_report(result), result.snapshot()) == []


@pytest.mark.parametrize(
    "body",
    [
        "A、B 两组 mass 的 Welch t 检验 p=0。",
        "A 组 mass 的 Welch t 检验 p=0.02。",
        "A 组 mass 的均值为20克。",
        "A group mass mean=20, not statistically significant.",
    ],
)
def test_scope_conflicts_are_not_hidden_by_units_or_negative_significance(body):
    from deep_research.workbench.analysis_review import statistic_scope_issues

    assert statistic_scope_issues(body, LEDGER)


def test_same_group_labels_from_different_grouping_columns_are_not_interchangeable():
    from deep_research.workbench.analysis_review import statistic_scope_issues

    ledger = copy.deepcopy(LEDGER)
    second = copy.deepcopy(ledger["tests"][0])
    second["group"] = "treatment"
    second["group_summaries"][0]["mean"] = 20
    ledger["tests"].append(second)
    assert not statistic_scope_issues("按 species 分组，A 组 mass 均值为10。", ledger)
    assert statistic_scope_issues("按 treatment 分组，A 组 mass 均值为10。", ledger)


def test_global_correlation_cannot_be_reported_as_within_group():
    from deep_research.workbench.analysis_review import statistic_scope_issues

    ledger = copy.deepcopy(LEDGER)
    ledger["describe"].append({"variable": "length", "n": 5, "mean": 12})
    ledger["correlations"] = [{"a": "mass", "b": "length", "n": 5, "r": 0.7, "p_value": 0.2}]
    ledger["correlations"][0]["p_value"] = 0.2
    assert statistic_scope_issues("A 组 mass 与 length 的相关系数 r=0.7。", ledger)


def test_statistics_policy_invalidates_delivery_fingerprint_without_changing_the_frozen_ledger(
    monkeypatch,
):
    from deep_research.models import Report
    from deep_research.orchestration import WorkflowRun
    from deep_research.persistence.repository import RunDetail
    from deep_research.workbench import analysis_review
    from deep_research.workbench.publish import delivery_fingerprint

    detail = RunDetail(
        id="statistics-run",
        query="compare",
        status="done",
        report=Report(query="compare", markdown="body"),
        orchestration=WorkflowRun(
            workflow_name="data_analysis", checkpoint={"scratch": {"analysis": LEDGER}}
        ),
    )
    before = delivery_fingerprint(detail)
    monkeypatch.setattr(
        analysis_review, "STATISTICS_POLICY_VERSION", analysis_review.STATISTICS_POLICY_VERSION + 1
    )
    assert delivery_fingerprint(detail) != before
    assert detail.orchestration.checkpoint["scratch"]["analysis"] == LEDGER


def test_percentage_and_arithmetic_are_not_accepted_as_their_first_number():
    from deep_research.workbench.analysis_review import statistic_scope_issues

    assert statistic_scope_issues("A组mass样本量n=2%。", LEDGER)
    assert statistic_scope_issues("mass 的 Welch t 检验 p=0.02%。", LEDGER)
    assert not statistic_scope_issues("mass 的 Welch t 检验 p=2%。", LEDGER)
    assert statistic_scope_issues("A组mass均值=10×100。", LEDGER)


def test_statistic_heading_binds_the_following_group_values():
    from deep_research.workbench.analysis_review import statistic_scope_issues

    assert statistic_scope_issues("## mass 均值\n\nA组：20\nB组：10", LEDGER)
    assert not statistic_scope_issues("## mass 均值\n\nA组：10\nB组：20", LEDGER)


def test_confidence_level_and_interval_bounds_belong_to_the_recorded_test():
    from deep_research.workbench.analysis import analyse
    from deep_research.workbench.analysis_review import statistic_scope_issues
    from tests.test_analysis_pairing import paired_scope

    result = analyse("before_mm,after_mm\n10,12\n20,21\n30,34", scope=paired_scope().model_dump())
    assert statistic_scope_issues(result.facts().replace("95%", "90%"), result.snapshot())
    low = str(result.tests[0]["ci_low"])
    assert statistic_scope_issues(result.facts().replace(f"[{low},", "[0,"), result.snapshot())


async def test_cached_numeric_binding_cannot_be_removed_or_reassigned():
    from deep_research.workbench.prose_review import reviewer_for_report
    from tests.fakes import FakeLLM

    checker = reviewer_for_report(
        FakeLLM(),
        "比较 mass",
        [],
        [],
        {"workbench": {"template": "dataAnalysis"}, "analysis": LEDGER},
        50000,
    )
    body = "A 组 mass 均值为10。"
    record = await checker.review(body)
    assert record["statistics_bindings"]["claims"][0]["fact_ids"]
    record["statistics_bindings"] = {"claims": [], "issues": []}
    bound, issues = checker.check(body, record)
    assert bound and any("绑定记录" in issue for issue in issues)


@pytest.mark.parametrize(
    "body",
    [
        "A组height均值为10。",
        "A组mass均值为10，B组height均值为20。",
        "Z组mass均值为16。",
        "A组mass均值为10，Z组均值为10。",
        "A组mass均值为10，B组AB均值为20。",
    ],
)
def test_unknown_variable_or_group_cannot_fall_back_to_a_known_scope(body):
    from deep_research.workbench.analysis_review import statistic_scope_issues

    assert statistic_scope_issues(body, LEDGER)
