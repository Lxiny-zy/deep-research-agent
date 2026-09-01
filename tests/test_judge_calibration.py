"""校准指标的数学正确性。

这些断言看着简单，但校准报告的全部价值就建立在它们之上：κ 算错会把一个
「和随机猜测差不多」的判定器包装成可信度量，而这正是本模块要防的事。
"""

from __future__ import annotations

import json

import pytest

from eval.judge_calibration import (
    CalibrationCase,
    cohens_kappa,
    evaluate,
    format_report,
    load_cases,
    stratified_sample,
    write_cases,
)


def _case(judge: str, human: str = "", claim: str = "c") -> CalibrationCase:
    return CalibrationCase(
        run_id="r",
        claim=claim,
        evidence_quote="q",
        source_url="https://example.com",
        source_excerpt="excerpt",
        judge_label=judge,
        human_label=human,
    )


def test_perfect_agreement_gives_kappa_one() -> None:
    pairs = [("supported", "supported")] * 5 + [("unsupported", "unsupported")] * 5
    assert cohens_kappa(pairs) == pytest.approx(1.0)


def test_random_agreement_gives_kappa_near_zero() -> None:
    """判对一半但完全靠碰运气时，准确率 50% 而 κ 应接近 0。"""
    pairs = [
        ("supported", "supported"),
        ("supported", "unsupported"),
        ("unsupported", "supported"),
        ("unsupported", "unsupported"),
    ]
    assert cohens_kappa(pairs) == pytest.approx(0.0)


def test_systematic_disagreement_gives_negative_kappa() -> None:
    pairs = [("supported", "unsupported")] * 4 + [("unsupported", "supported")] * 4
    assert cohens_kappa(pairs) < 0


def test_degenerate_single_class_is_not_reported_as_perfect() -> None:
    """双方都只会说 supported 时一致率 100%，但没有任何区分能力。"""
    pairs = [("supported", "supported")] * 10
    assert cohens_kappa(pairs) == 0.0


def test_kappa_matches_a_hand_computed_value() -> None:
    # 观察一致 = 7/10；期望一致 = (6/10)(7/10) + (4/10)(3/10) = 0.42+0.12 = 0.54
    # κ = (0.7-0.54)/(1-0.54) = 0.3478...
    pairs = (
        [("supported", "supported")] * 5
        + [("supported", "unsupported")] * 1
        + [("unsupported", "supported")] * 2
        + [("unsupported", "unsupported")] * 2
    )
    assert cohens_kappa(pairs) == pytest.approx(0.34782608, rel=1e-6)


def test_empty_input_is_not_an_error() -> None:
    assert cohens_kappa([]) == 0.0
    report = evaluate([])
    assert report.total == 0
    assert "无结论" in report.verdict


def test_unlabeled_cases_are_excluded_not_counted_as_correct() -> None:
    """未标注样本若被当成一致，一致率会凭空变高——这是最危险的一种错法。"""
    cases = [
        _case("supported", "supported"),
        _case("supported", ""),
        _case("unsupported", ""),
    ]

    report = evaluate(cases)

    assert report.total == 1
    assert report.skipped_unlabeled == 2
    assert report.accuracy == pytest.approx(1.0)


def test_per_label_precision_and_recall() -> None:
    cases = [
        _case("supported", "supported"),
        _case("supported", "supported"),
        _case("supported", "unsupported"),  # 误报：judge 说支持，人工说不支持
        _case("unsupported", "unsupported"),
        _case("uncertain", "unsupported"),  # 漏报：人工说不支持，judge 说不确定
    ]

    report = evaluate(cases)

    supported = report.per_label["supported"]
    assert supported.predicted == 3
    assert supported.support == 2
    assert supported.correct == 2
    assert supported.precision == pytest.approx(2 / 3)
    assert supported.recall == pytest.approx(1.0)

    unsupported = report.per_label["unsupported"]
    assert unsupported.support == 3
    assert unsupported.correct == 1
    assert unsupported.recall == pytest.approx(1 / 3)


def test_confusion_matrix_orientation() -> None:
    """行是人工、列是 judge，弄反了整张表的解读就颠倒了。"""
    report = evaluate([_case("supported", "unsupported")])
    assert report.confusion[("unsupported", "supported")] == 1
    assert ("supported", "unsupported") not in report.confusion


def test_verdict_thresholds_are_monotonic() -> None:
    high = evaluate([_case("supported", "supported")] * 9 + [_case("unsupported", "unsupported")])
    low = evaluate([_case("supported", "supported")] * 5 + [_case("supported", "unsupported")] * 5)
    assert "版本回归" in high.verdict
    assert "不应把" in low.verdict


def test_stratified_sample_keeps_minority_classes() -> None:
    """随机抽样会得到几乎全是 supported 的集合，一致率因此虚高。"""
    cases = [_case("supported", claim=f"s{i}") for i in range(100)]
    cases += [_case("unsupported", claim=f"u{i}") for i in range(4)]
    cases += [_case("uncertain", claim=f"q{i}") for i in range(3)]

    sampled = stratified_sample(cases, 12)

    labels = {case.judge_label for case in sampled}
    assert len(sampled) == 12
    assert labels == {"supported", "unsupported", "uncertain"}


def test_stratified_sample_is_deterministic() -> None:
    cases = [_case("supported", claim=f"s{i}") for i in range(50)]
    first = [case.claim for case in stratified_sample(cases, 10)]
    second = [case.claim for case in stratified_sample(cases, 10)]
    assert first == second, "抽样必须可复现，否则两次校准结果无法比较"


def test_sample_smaller_than_limit_is_returned_whole() -> None:
    cases = [_case("supported"), _case("unsupported")]
    assert len(stratified_sample(cases, 10)) == 2


def test_roundtrip_through_jsonl_preserves_labels(tmp_path) -> None:
    path = tmp_path / "cases.jsonl"
    original = [_case("supported", "unsupported", claim="论断一"), _case("uncertain", "")]

    write_cases(path, original)
    restored = load_cases(path)

    assert [c.judge_label for c in restored] == ["supported", "uncertain"]
    assert [c.human_label for c in restored] == ["unsupported", ""]
    assert restored[0].claim == "论断一"
    # 导出的文件必须是人能直接编辑的：每行一个 JSON 对象，含空的 human_label 字段
    first_line = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert "human_label" in first_line


def test_report_renders_matrix_and_conclusion() -> None:
    cases = [
        _case("supported", "supported"),
        _case("supported", "unsupported"),
        _case("unsupported", "unsupported"),
    ]

    markdown = format_report(evaluate(cases), source="cases.jsonl")

    assert "混淆矩阵" in markdown
    assert "Cohen's κ" in markdown
    assert "precision" in markdown
    # 报告必须声明自己度量的边界，否则会被当成「报告正确率」引用
    assert "逐字引用验证是确定性的" in markdown


def test_report_on_unlabeled_file_says_so_instead_of_printing_zeros() -> None:
    markdown = format_report(evaluate([_case("supported"), _case("uncertain")]), source="x.jsonl")
    assert "没有任何样本填写了" in markdown
