"""Analysis keeps column roles and suspicious input values explicit."""

import pytest

from deep_research.workbench.analysis import DatasetError, analyse, parse_dataset
from deep_research.workbench.analysis_scope import AnalysisScope, plan_scope, validate_scope

ROLES = (
    "species,year,sex,mass_g\n"
    "A,2020,F,3200\nA,2021,M,3400\nA,2020,F,3300\n"
    "B,2021,M,4200\nB,2020,F,4500\nB,2021,M,4300"
)


def test_year_does_not_default_to_a_continuous_measurement():
    result = analyse(ROLES, "分析体重")
    assert "year" not in result.numeric
    assert not any(row["variable"] == "year" for row in result.describe)


def test_scope_rejects_a_calendar_year_as_a_measurement():
    with pytest.raises(DatasetError, match="年份|时间|测量"):
        validate_scope(parse_dataset(ROLES), AnalysisScope(measures=["mass_g", "year"]))


@pytest.mark.parametrize("column", ["year", "sex"])
async def test_planner_cannot_promote_background_columns_to_unrequested_group_tests(column):
    class Planner:
        async def parse(self, *args, **kwargs):
            return AnalysisScope(measures=["mass_g"], groups=[column])

    with pytest.raises(DatasetError, match="分组|背景"):
        await plan_scope(Planner(), ROLES, "比较物种体重差异，年份和性别只描述背景", {})


def test_question_mark_is_reported_as_missing_without_losing_a_numeric_column():
    result = analyse(
        "mass_g\n3200\n?\n3400\nNA\n3600",
        "描述体重",
        scope=AnalysisScope(measures=["mass_g"]).model_dump(),
    )
    assert result.numeric == ["mass_g"]
    assert result.rows == 5 and result.missing["mass_g"] == 2
    assert result.describe[0]["n"] == 3 and result.describe[0]["mean"] == 3400
    assert any("?" in issue and "缺失" in issue for issue in result.issues)


@pytest.mark.parametrize("value", [-999, 38000])
def test_suspicious_values_are_identified_without_silent_deletion(value):
    csv = "mass_g\n3200\n3300\n3400\n3500\n3600\n3700\n3800\n" + str(value)
    result = analyse(csv, "描述体重", scope=AnalysisScope(measures=["mass_g"]).model_dump())
    assert result.rows == 8 and result.describe[0]["n"] == 8
    assert any(str(value) in issue and "mass_g" in issue for issue in result.issues)
    assert analyse(csv, frozen=result.snapshot()).issues == result.issues


@pytest.mark.parametrize(
    "question,column",
    [
        ("仅按性别比较体重差异", "sex"),
        ("Only compare body mass by sex", "sex"),
        ("比较年度体重差异", "year"),
        ("比较背景变量性别的影响", "sex"),
    ],
)
async def test_explicit_background_comparisons_remain_available(question, column):
    class Planner:
        async def parse(self, *args, **kwargs):
            return AnalysisScope(measures=["mass_g"], groups=[column])

    scope = await plan_scope(Planner(), ROLES, question, {})
    result = analyse(ROLES, question, scope=scope.model_dump())
    assert result.tests[0]["group"] == column


def test_default_analysis_does_not_run_unrequested_sex_comparisons():
    result = analyse(ROLES, "比较物种体重差异")
    assert result.categorical == ["species"]
    assert {test["group"] for test in result.tests} == {"species"}


def test_elapsed_time_remains_a_measurement():
    result = analyse(
        "elapsed_time_s,mass_g\n1,3200\n2,3400\n3,3600",
        scope=AnalysisScope(measures=["elapsed_time_s", "mass_g"]).model_dump(),
    )
    assert result.numeric == ["elapsed_time_s", "mass_g"]


@pytest.mark.parametrize("column", ["age_year", "duration_year", "elapsed_year"])
def test_duration_in_years_is_not_a_calendar_year(column):
    result = analyse(
        f"{column},mass_g\n20,3200\n30,3400\n40,3600",
        scope=AnalysisScope(measures=[column, "mass_g"]).model_dump(),
    )
    assert column in result.numeric


def test_missing_marker_normalization_does_not_truncate_rows_after_blank_lines(monkeypatch):
    from deep_research.workbench import analysis

    monkeypatch.setattr(analysis, "MAX_ROWS", 3)
    frame = parse_dataset("mass_g\n\n\n\n3200\n ? \n3400")
    assert len(frame) == 3 and frame.mass_g.count() == 2
    with pytest.raises(DatasetError, match="超过 3 行"):
        parse_dataset("mass_g\n\n\n\n3200\n ? \n3400\n3600")


def test_quoted_multiline_text_is_not_reinterpreted_as_missing():
    frame = parse_dataset('note,mass_g\n"Why?\nKeep this, too",3200\n" ? ",3400')
    assert len(frame) == 2
    assert frame.note.iloc[0] == "Why?\nKeep this, too"
    assert frame.note.isna().sum() == 1
    assert frame.mass_g.sum() == 6600


def test_anomaly_notes_survive_reports_and_keep_the_original_data_hash():
    import hashlib

    from deep_research.workbench.analysis import fallback_report

    csv = "mass_g\n3200\n3300\n3400\n3500\n3600\n3700\n3800\n38000"
    result = analyse(csv, scope=AnalysisScope(measures=["mass_g"]).model_dump())
    assert result.input_sha256 == hashlib.sha256(csv.encode()).hexdigest()
    assert "38000" in result.facts() and "未自动删除" in result.facts()
    assert "38000" in fallback_report(result)
    assert result.describe[0]["max"] == 38000


def test_legacy_frozen_role_and_input_notes_are_not_rewritten():
    # A pre-policy report's saved numerical design still owns its download.
    from deep_research.workbench.analysis import AnalysisResult

    result = analyse(ROLES, scope=AnalysisScope(measures=["mass_g"]).model_dump())
    frozen = result.snapshot()
    frozen["scope"]["measures"] = ["year"]
    frozen["numeric"] = ["year"]
    frozen["describe"] = [{
        "variable": "year", "n": 6, "mean": 2020.5, "std": 0.5477,
        "median": 2020.5, "min": 2020, "max": 2021,
    }]
    frozen["tests"] = []
    frozen["correlations"] = []
    frozen["issues"] = ["Original input note"]
    frozen.pop("facts")
    restored = analyse(ROLES, "按新问题分析", frozen=frozen)
    assert isinstance(restored, AnalysisResult)
    assert restored.numeric == ["year"] and restored.describe == frozen["describe"]
    assert restored.issues == ["Original input note"]


def test_typical_measurements_are_not_labelled_as_suspicious():
    result = analyse("mass_g\n3200\n3300\n3400\n3500\n3600\n3700\n3800\n3900")
    assert not result.issues


@pytest.mark.parametrize(
    "question",
    ["不按性别分组", "不要按性别检验", "Don't compare by sex", "不检验性别差异"],
)
async def test_explicitly_excluded_background_comparison_is_rejected(question):
    class Planner:
        async def parse(self, *args, **kwargs):
            return AnalysisScope(measures=["mass_g"], groups=["sex"])

    with pytest.raises(DatasetError, match="背景列"):
        await plan_scope(Planner(), ROLES, question, {})


def test_explicit_background_exclusion_applies_to_other_column_names():
    from deep_research.workbench.analysis_inputs import explicitly_grouped

    assert not explicitly_grouped("batch", "batch 仅作背景，不做显著性检验")
    assert explicitly_grouped("species", "compare species and do not group by sex")
    assert not explicitly_grouped("sex", "compare species and do not group by sex")


def test_missing_normalization_preserves_previously_supported_large_text_cells():
    note = "long note " * 16000
    frame = parse_dataset(f'note,mass_g\n"{note}",3200\nnormal,3400')
    assert frame.note.iloc[0] == note
    assert frame.mass_g.sum() == 6600


def test_missing_normalization_does_not_repair_unclosed_csv_quotes():
    with pytest.raises(DatasetError, match="CSV 解析失败"):
        parse_dataset('note,mass_g\n"unclosed,3200\nnext,3400')
