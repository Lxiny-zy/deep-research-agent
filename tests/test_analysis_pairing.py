"""Pairing follows an explicit, compatible measurement plan, not question keywords."""

import copy
import hashlib

import pytest

from deep_research.workbench.analysis import DatasetError, analyse, parse_dataset
from deep_research.workbench.analysis_scope import AnalysisScope, plan_scope, validate_scope

PENGUINS = (
    "species,bill_length_mm,body_mass_g\n"
    "A,35,3200\nA,37,3400\nA,38,3300\n"
    "B,45,4200\nB,47,4500\nB,46,4300"
)


def paired_scope(left="before_mm", right="after_mm", *, right_unit="mm", right_quantity="length"):
    return AnalysisScope.model_validate(
        {
            "measures": [left, right],
            "comparison": "paired",
            "pairing": {
                "left": {"column": left, "quantity": "length", "unit": "mm"},
                "right": {"column": right, "quantity": right_quantity, "unit": right_unit},
            },
        }
    )


@pytest.mark.parametrize("explicit_scope", [False, True])
@pytest.mark.parametrize(
    "question",
    [
        "按物种比较喙长与体重，相关分析采用成对删除",
        "比较同一批样本的喙长与体重在不同物种间的差异",
    ],
)
def test_missing_value_policy_and_common_sample_do_not_change_the_comparison(
    question, explicit_scope
):
    scope = (
        AnalysisScope(measures=["bill_length_mm", "body_mass_g"], groups=["species"]).model_dump()
        if explicit_scope else None
    )
    result = analyse(PENGUINS, question, scope=scope)
    assert not any(test.get("paired") for test in result.tests)
    assert {(test["group"], test["variable"]) for test in result.tests} == {
        ("species", "bill_length_mm"),
        ("species", "body_mass_g"),
    }


@pytest.mark.parametrize("right_unit,right_quantity", [("g", "length"), ("mm", "mass")])
def test_pairing_rejects_different_units_or_measurement_roles(right_unit, right_quantity):
    scope = paired_scope(right_unit=right_unit, right_quantity=right_quantity)
    with pytest.raises(DatasetError, match="配对"):
        validate_scope(parse_dataset("before_mm,after_mm\n1,3\n2,5\n4,7"), scope)


def test_pairing_cannot_hide_conflicting_units_in_column_headers():
    scope = paired_scope(left="bill_length_mm", right="body_mass_g")
    with pytest.raises(DatasetError, match="单位"):
        validate_scope(parse_dataset(PENGUINS), scope)


def test_explicit_pair_selects_its_columns_even_with_other_measures():
    scope = paired_scope().model_dump()
    scope["measures"] = ["unrelated", "after_mm", "before_mm"]
    result = analyse(
        "before_mm,after_mm,unrelated\n10,12,90\n20,21,80\n30,34,60\n40,,70\n,99,20",
        "比较测量差异",
        scope=scope,
    )
    test = next(test for test in result.tests if test.get("paired"))
    assert (test["left"], test["right"]) == ("before_mm", "after_mm")
    assert test["n_pairs"] == 3 and test["excluded_pairs"] == 2
    assert test["mean_difference"] == pytest.approx(7 / 3, abs=0.0001)
    assert test["p_value"] == pytest.approx(0.1181, abs=0.0001)


def test_paired_design_without_columns_cannot_fall_back_to_independent_tests():
    scope = AnalysisScope.model_validate(
        {
            "measures": ["bill_length_mm", "body_mass_g"],
            "groups": ["species"], "comparison": "paired",
        }
    )
    with pytest.raises(DatasetError, match="配对"):
        validate_scope(parse_dataset(PENGUINS), scope)


@pytest.mark.parametrize("column", ["id", "group"])
def test_pairing_cannot_use_identifiers_or_group_roles(column):
    frame = parse_dataset("id,group,before_mm,after_mm\n1,A,2,3\n2,A,4,5\n3,B,5,7")
    scope = paired_scope()
    scope.background = ["id"]
    scope.groups = ["group"]
    scope.pairing.left.column = column
    with pytest.raises(DatasetError, match="配对列"):
        validate_scope(frame, scope)


@pytest.mark.parametrize("right_unit", ["cm", "unknown", ""])
def test_pairing_does_not_assume_scaling_or_missing_units(right_unit):
    frame = parse_dataset("before,after\n1,3\n2,5\n4,7")
    with pytest.raises(DatasetError, match="单位"):
        validate_scope(frame, paired_scope("before", "after", right_unit=right_unit))


@pytest.mark.parametrize("placeholder", ["-", "?", "未提供"])
@pytest.mark.parametrize("field", ["unit", "quantity"])
def test_matching_unknown_markers_do_not_establish_compatible_measurements(placeholder, field):
    scope = paired_scope("before", "after")
    setattr(scope.pairing.left, field, placeholder)
    setattr(scope.pairing.right, field, placeholder)
    with pytest.raises(DatasetError, match="未知"):
        validate_scope(parse_dataset("before,after\n1,3\n2,5\n4,7"), scope)


def test_equivalent_unit_spelling_is_allowed_but_bracketed_conflicts_are_not():
    frame = parse_dataset("before_mm,after（毫米）\n1,3\n2,5\n4,7")
    validate_scope(frame, paired_scope("before_mm", "after（毫米）", right_unit="毫米"))
    conflicting = parse_dataset("before_mm,after [cm]\n1,3\n2,5\n4,7")
    with pytest.raises(DatasetError, match="列名"):
        validate_scope(conflicting, paired_scope("before_mm", "after [cm]"))


def test_frozen_pairs_keep_direction_statistics_and_plots_when_question_changes():
    csv = "before_mm,after_mm\n10,12\n20,21\n30,34"
    result = analyse(csv, "比较差异", scope=paired_scope().model_dump())
    frozen = result.snapshot()
    restored = analyse(csv, "按独立样本比较", frozen=frozen)
    assert restored.snapshot() == frozen
    assert restored.figures == result.figures
    # Historical frozen ledgers lack the new scope; downloads must still use
    # their saved statistical design rather than rerunning keyword inference.
    legacy = copy.deepcopy(frozen)
    legacy.pop("scope")
    legacy.pop("composition")
    legacy["version"] = 4
    restored = analyse(csv, "任意新问题", frozen=legacy)
    assert restored.tests == frozen["tests"]
    assert restored.figures == result.figures


async def test_planner_corrects_ambiguous_pairs_and_reuses_the_validated_plan():
    csv = "before_mm,after_mm\n10,12\n20,21\n30,34"

    class Planner:
        calls = 0

        async def parse(self, system, user, schema, **kwargs):
            self.calls += 1
            assert "成对删除" in system and "right-left" in system
            if self.calls == 1:
                return AnalysisScope(measures=["before_mm", "after_mm"], comparison="paired")
            assert "配对列不明确" in str(user)
            return paired_scope()

    planner = Planner()
    scratch = {}
    scope = await plan_scope(planner, csv, "比较同一对象前后长度的变化", scratch)
    assert await plan_scope(planner, csv, "比较同一对象前后长度的变化", scratch) == scope
    assert planner.calls == 2
    result = analyse(csv, scope=scope.model_dump())
    assert result.tests[0]["paired"] is True
    assert result.scope == scratch["analysis_scope"]["scope"]


async def test_scope_from_old_keyword_policy_is_replanned_before_new_analysis():
    question = "相关分析使用成对删除"
    old = AnalysisScope(measures=["bill_length_mm", "body_mass_g"], groups=["species"])
    scratch = {"analysis_scope": {
        "signature": hashlib.sha256((PENGUINS.strip() + "\0" + question).encode()).hexdigest(),
        "scope": old.model_dump(),
    }}

    class Planner:
        calls = 0

        async def parse(self, *args, **kwargs):
            self.calls += 1
            return old

    planner = Planner()
    assert await plan_scope(planner, PENGUINS, question, scratch) == old
    assert planner.calls == 1
