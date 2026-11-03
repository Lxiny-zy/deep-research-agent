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


def test_method_plan_naming_a_confidence_level_is_not_an_unbound_statistic():
    from deep_research.workbench.analysis_review import statistic_scope_issues

    # 线上数据分析第 17 行：方法说明列出要报告的统计量，95 是置信水平
    plan = (
        "采用的方法：以配对 t 检验比较该差值的均值是否为 0，报告统计量、p 值、自由度、平均差、"
        "差值标准差与 95% 均值差置信区间；以 Shapiro-Wilk 检验检查配对差值的分布前提。"
    )
    assert not statistic_scope_issues(plan, LEDGER)
    assert statistic_scope_issues("报告统计量、p 值与 3.7 的差值。", LEDGER)


PAIRED = {
    "rows": 12,
    "describe": [
        {"variable": "baseline_psnr", "n": 12, "mean": 34.1933, "std": 1.008},
        {"variable": "proposed_psnr", "n": 12, "mean": 34.9583, "std": 0.9727},
    ],
    "composition": [{"column": "scene", "n": 12, "missing": 0, "distinct": 12, "levels": []}],
    "tests": [
        {
            "paired": True,
            "left": "baseline_psnr",
            "right": "proposed_psnr",
            "variable": "proposed_psnr − baseline_psnr",
            "group": "同一行配对",
            "method": "配对 t 检验",
            "statistic": 17.4981,
            "p_value": 2.2300848e-09,
            "n_pairs": 12,
            "excluded_pairs": 0,
            "mean_difference": 0.765,
            "difference_std": 0.1514,
            "df": 11,
            "ci_low": 0.6688,
            "ci_high": 0.8612,
            "assumptions": [
                {
                    "kind": "normality",
                    "n": 12,
                    "variable": "proposed_psnr − baseline_psnr",
                    "group_column": "同一行配对",
                    "method": "Shapiro-Wilk",
                    "statistic": 0.9234,
                    "p_value": 0.31516107,
                    "status": "not_rejected",
                }
            ],
        }
    ],
    "correlations": [
        {
            "a": "baseline_psnr",
            "b": "proposed_psnr",
            "r": 0.9889,
            "p_value": 1.2801014e-09,
            "n": 12,
            "method": "Pearson",
        }
    ],
}


@pytest.mark.parametrize(
    "body",
    [
        # 线上数据分析 53629b7c 的原句：数字全对，曾被判为无法绑定
        "scene 是观测对象标识列，仅作样本背景，其计数为有效 n=12、缺失=0、不同取值数=12；"
        "测量列 baseline_psnr 与 proposed_psnr 各自的有效样本量均为 n=12。",
        "配对检验的完整配对数=12，排除不完整配对=0，配对差值方向为 proposed_psnr − baseline_psnr。",
        "这里的平均差与差值标准差描述的是 12 个配对差的中心位置与离散程度。",
        "平均差与差值标准差见表 2。",
        "baseline_psnr 与 proposed_psnr 的均值分别为 34.1933 与 34.9583。",
        "proposed_psnr 与 baseline_psnr 的均值分别为 34.9583 与 34.1933。",
        "baseline_psnr 与 proposed_psnr 的均值分别为 34.1933 和 34.9583。"
        "相关性 r=0.9889（p=1.2801014e-09，n=12）表明两列在同一场景上取值高度同向。",
        "相关性 r=0.9889（p=1.2801014e-09，n=12）。",
        "局限：配对差值的 Shapiro-Wilk 检验（统计量=0.9234，p=0.31516107，n=12）未拒绝分布前提。",
        "配对 t 检验 p=2.2300848e-09，相关性 r=0.9889，p=1.2801014e-09。",
        # 线上数据分析 503a6d89 的原句
        "样本量按三种口径分别陈述：总样本量为 12 行；单个测量的有效样本量分别为 "
        "`baseline_psnr` n=12、`proposed_psnr` n=12；变量对样本量即 `baseline_psnr` 与 "
        "`proposed_psnr` 的完整配对数为 12，台账配对 t 检验记录中的排除不完整配对为 0。",
        "三种离散量含义不同，需分别读：`baseline_psnr` 的标准差为 1.008，"
        "`proposed_psnr` 的标准差为 0.9727，配对差值（`proposed_psnr` − `baseline_psnr`）"
        "的标准差为 0.1514，配对平均差为 0.765。",
        "两列的相关结果为 Pearson r=0.9889，p=1.2801014e-09，有效样本量 n=12，"
        "描述的是两列测量之间的线性关联；配对差异的结论来自上述配对 t 检验。",
    ],
)
def test_correct_paired_report_sentences_bind_to_their_records(body):
    from deep_research.workbench.analysis_review import statistic_scope_issues

    assert statistic_scope_issues(body, PAIRED) == []


@pytest.mark.parametrize(
    "body",
    [
        "baseline_psnr 与 proposed_psnr 的均值分别为 34.9583 与 34.1933。",
        "proposed_psnr 与 baseline_psnr 的均值分别为 34.1933 与 34.9583。",
        "baseline_psnr 与 proposed_psnr 的均值分别为 34.1933 与 34.9。",
        "scene 是观测对象标识列，其计数为有效 n=13。",
        "配对检验的完整配对数=13。",
        "配对差值的 Shapiro-Wilk 检验（统计量=0.95）。",
        "相关性 r=0.9。",
        "配对 t 检验 p=2.2300848e-09，相关性 r=0.9889，p=2.2300848e-09。",
        "有效样本量共 13 个。",
        "height 的相关系数 r=0.9889。",
        "单个测量的有效样本量分别为 `baseline_psnr` n=13、`proposed_psnr` n=12。",
        "台账配对 t 检验记录中的排除不完整配对为 1。",
        "配对差值（`proposed_psnr` − `baseline_psnr`）的标准差为 1.008。",
        "两列的相关结果为 Pearson r=0.95，p=1.2801014e-09，有效样本量 n=12。",
        "两列的相关结果为 Pearson r=0.9889，p=1.2801014e-09，有效样本量 n=13。",
    ],
)
def test_wrong_values_stay_rejected_when_the_scope_is_inferred(body):
    from deep_research.workbench.analysis_review import statistic_scope_issues

    assert statistic_scope_issues(body, PAIRED)


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


PAIRED_WITH_MEDIANS = {
    **PAIRED,
    "describe": [
        {"variable": "baseline_psnr", "n": 12, "mean": 34.1933, "std": 1.008, "median": 34.07},
        {"variable": "proposed_psnr", "n": 12, "mean": 34.9583, "std": 0.9727, "median": 34.99},
    ],
}


@pytest.mark.parametrize(
    "body",
    [
        "baseline_psnr 的 n=12，proposed_psnr 的 n=12。"
        "同一行配对检验的完整配对数=12，排除不完整配对=0。",
        "baseline_psnr 标准差=1.008、proposed_psnr 标准差=0.9727 分别是两个测量列的离散；"
        "配对差值标准差=0.1514 是配对差值的离散。",
        "baseline_psnr 与 proposed_psnr 的描述统计分别为均值 34.1933、34.9583，"
        "中位数 34.07、34.99。",
    ],
)
def test_paired_statistics_follow_the_test_when_one_column_is_carried_over(body):
    from deep_research.workbench.analysis_review import statistic_scope_issues

    assert not statistic_scope_issues(body, PAIRED_WITH_MEDIANS)


@pytest.mark.parametrize(
    "body",
    [
        "baseline_psnr 的 n=12，proposed_psnr 的 n=12。同一行配对检验的完整配对数=13。",
        "baseline_psnr 标准差=1.008、proposed_psnr 标准差=0.9727 分别是两个测量列的离散；"
        "配对差值标准差=0.25 是配对差值的离散。",
        "baseline_psnr 与 proposed_psnr 的描述统计分别为均值 34.1933、34.9583，"
        "中位数 34.99、34.07。",
    ],
)
def test_wrong_paired_values_are_still_rejected_after_the_carry_over(body):
    from deep_research.workbench.analysis_review import statistic_scope_issues

    assert statistic_scope_issues(body, PAIRED_WITH_MEDIANS)


THREE_GROUPS = {
    "rows": 15,
    "describe": [{"variable": "ssim", "n": 15, "mean": 0.93, "std": 0.016}],
    "composition": [
        {
            "column": "method",
            "n": 15,
            "missing": 0,
            "distinct": 3,
            "levels_status": "complete",
            "levels": [{"label": "A", "n": 5}, {"label": "B", "n": 5}, {"label": "C", "n": 5}],
        },
        {"column": "scene", "n": 15, "missing": 0, "distinct": 15, "levels": []},
    ],
    "tests": [
        {
            "variable": "ssim",
            "group": "method",
            "method": "单因素方差分析",
            "statistic": 32.3489,
            "p_value": 1.4668693e-05,
            "n_total": 15,
            "group_summaries": [
                {"label": "A", "n": 5, "mean": 0.9102},
                {"label": "B", "n": 5, "mean": 0.9318},
                {"label": "C", "n": 5, "mean": 0.9478},
            ],
            "robust_method": "Kruskal–Wallis",
            "robust_p": 0.0024540882,
            "posthoc": [
                {
                    "left_group": "A",
                    "right_group": "B",
                    "method": "Tukey HSD",
                    "mean_difference": 0.0216,
                    "p_value": 0.0016238187,
                },
                {
                    "left_group": "B",
                    "right_group": "C",
                    "method": "Tukey HSD",
                    "mean_difference": 0.016,
                    "p_value": 0.013274368,
                },
            ],
        }
    ],
    "correlations": [],
}


@pytest.mark.parametrize(
    "body",
    [
        "method 列有效 n=15，构成为 method=A 组 n=5、method=B 组 n=5、method=C 组 n=5；"
        "scene 列有效 n=15。",
        "按 method 每组 n=5 与 scene 不同取值数=15，可推出每个 method 组对应 5 个不同 scene 标签。",
        "稳健性复核：ssim 按 method 的 Kruskal–Wallis 检验 p=0.0024540882。"
        "样本效应量描述样本关联，有效总样本量=15（变量 ssim，比较分组 method）。",
        "三对比较的 ssim 平均差方向与 ssim 按 method 的样本均值顺序 C > B > A 一致，"
        "全部 p 值均小于 0.05。",
        "ssim 按 method 的样本均值顺序为 C (0.9478) > B (0.9318) > A (0.9102)，"
        "每组有效样本量 n=5。",
    ],
)
def test_report_sentences_about_groups_and_composition_bind_to_their_records(body):
    from deep_research.workbench.analysis_review import statistic_scope_issues

    assert not statistic_scope_issues(body, THREE_GROUPS)


@pytest.mark.parametrize(
    "body",
    [
        "method=C 组 n=5；scene 列有效 n=12。",
        "按 method 每组 n=4 与 scene 不同取值数=15。",
        "ssim 按 method 的 Kruskal–Wallis 检验有效总样本量=15。",
        "三对比较的 ssim 平均差方向与 ssim 按 method 的样本均值顺序 C > B > A 一致，"
        "全部 p 值均小于 0.01。",
        "ssim 按 method 的样本均值顺序为 C (0.9318) > B (0.9478) > A (0.9102)。",
    ],
)
def test_wrong_group_and_composition_values_stay_rejected(body):
    from deep_research.workbench.analysis_review import statistic_scope_issues

    assert statistic_scope_issues(body, THREE_GROUPS)


@pytest.mark.parametrize(
    ("body", "bound"),
    [
        ("mass 的 p 值小于 0.05。", True),
        ("mass 的 p 值低于 0.05。", True),
        ("mass 的 p 值不超过 0.02。", True),
        ("mass 的 p 值小于 0.01。", False),
        ("mass 的 p 值大于 0.05。", False),
    ],
)
def test_threshold_words_compare_against_the_recorded_value(body, bound):
    from deep_research.workbench.analysis_review import statistic_scope_issues

    assert bool(statistic_scope_issues(body, LEDGER)) is not bound
