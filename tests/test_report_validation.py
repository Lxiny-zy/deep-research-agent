from __future__ import annotations

import pytest

from deep_research.models import Report, ResearchResult
from deep_research.report.validation import finalize_report
from tests.fakes import verified_finding


def calculation_check(body: str, values: str = "33.18, 32.67, 1, 3, 0"):
    from deep_research.report.validation import validate_body

    results = [
        ResearchResult(
            sub_question="原始数值",
            findings=[verified_finding(statement=values, evidence_quote=values)],
        )
    ]
    return validate_body(body, results, {"https://a.com": 1}, fallback=False)


@pytest.mark.parametrize(
    "equation,values",
    [
        ("33.18 - 32.67 = 0.51", "33.18, 32.67"),
        ("33.18 − 32.67 = 0.51", "33.18, 32.67"),
        ("3 + 1 = 4", "3, 1"),
        (r"3 \times 2 = 6", "3, 2"),
        (r"1 / 3 \approx 0.33", "1, 3"),
        ("1e-4 - 1e-5 = 9e-5", "1e-4, 1e-5"),
    ],
)
def test_explicit_computation_uses_cited_operands_and_program_verified_result(equation, values):
    body = f"据所列原始值计算：${equation}$ [1]。"
    check = calculation_check(body, values)
    assert not check.issues and check.body == body


@pytest.mark.parametrize(
    "body,issue",
    [
        ("差值为0.51 [1]。", "unsupported_number"),
        ("33.18 - 32.67 = 0.50 [1]。", "invalid_calculation"),
        ("33.18 - 32.67 = 32.67 [1]。", "invalid_calculation"),
        ("34.18 - 32.67 = 1.51 [1]。", "unsupported_number"),
        ("1 / 0 = 0 [1]。", "invalid_calculation"),
        ("1 / 3 = 0.33 [1]。", "invalid_calculation"),
        ("33.18 - 32.67 = 0.51，准确率达到0.51 [1]。", "unsupported_number"),
        ("1 + 33.18 - 32.67 = 0.51 [1]。", "invalid_calculation"),
        ("33.18 - 32.67 = 0.51 + 1 [1]。", "invalid_calculation"),
    ],
)
def test_computation_does_not_license_wrong_or_unbound_numbers(body, issue):
    assert issue in calculation_check(body).issues


def test_calculation_operands_cannot_come_from_an_uncited_source():
    from deep_research.report.validation import validate_body

    results = [
        ResearchResult(
            sub_question="q",
            findings=[
                verified_finding("33.18", "https://a.com", "33.18"),
                verified_finding("32.67", "https://b.com", "32.67"),
            ],
        )
    ]
    mapping = {"https://a.com": 1, "https://b.com": 2}
    assert validate_body("33.18 - 32.67 = 0.51 [1]。", results, mapping, fallback=False).issues
    assert not validate_body(
        "33.18 - 32.67 = 0.51 [1,2]。", results, mapping, fallback=False
    ).issues


def test_faithful_translation_cannot_add_a_derived_result():
    from deep_research.report.validation import validate_body

    results = [
        ResearchResult(
            sub_question="q",
            findings=[verified_finding("33.18 and 32.67", evidence_quote="33.18 and 32.67")],
        )
    ]
    check = validate_body(
        "## 摘要翻译\n\n33.18 - 32.67 = 0.51 [1]。",
        results,
        {"https://a.com": 1},
        fallback=False,
        section_support={"摘要翻译": "33.18 and 32.67"},
    )
    assert "unsupported_number" in check.issues


@pytest.mark.parametrize(
    "text",
    [
        "RGB 图像被线性缩放到 [0,1]",
        "RGB images are linearly rescaled to [0, 1]",
        "取值范围为 [1,2]",
        "the interval [1, 2]",
        "RGB 缩放到 **[0,1]**",
    ],
)
def test_explicit_numeric_intervals_remain_data_during_validation_and_fallback(text):
    from deep_research.report.validation import validate_body

    results = [
        ResearchResult(sub_question="q", findings=[verified_finding(text, evidence_quote=text)])
    ]
    assert not validate_body(text + " [1]。", results, {"https://a.com": 1}, fallback=False).issues
    fallback = validate_body("错误结果 999 [1]。", results, {"https://a.com": 1})
    assert text in fallback.body


def test_interval_handling_does_not_hide_invalid_or_regular_citation_clusters():
    from deep_research.report.validation import validate_body
    from deep_research.workbench.delivery.math_markdown import citation_text

    assert "[1,2]" in citation_text("归一化方法参见 [1,2]。")
    assert "[1,2]" in citation_text("Normalization methods [1,2].")
    assert "[0]" in citation_text("见 [0]。")
    results = [ResearchResult(sub_question="q", findings=[verified_finding()])]
    assert "invalid_citation" in validate_body("见 [0,1]。", results, {"https://a.com": 1}).issues


@pytest.mark.parametrize(
    "body,issue",
    [
        ("准确率99% [1]。", "unsupported_number"),
        ("准确率42% [99]。", "invalid_citation"),
        ("无来源支持的新结论。", "uncited_paragraph"),
    ],
)
def test_invalid_final_body_is_replaced_by_supported_statements(body, issue):
    finding = verified_finding(statement="准确率42%", evidence_quote="准确率42%")
    results = [ResearchResult(sub_question="准确率", findings=[finding])]
    checked, validation = finalize_report(
        Report(query="准确率", markdown=body, citations=[finding.source_url]), results
    )
    assert issue in validation.issues
    assert "准确率42% [1]" in checked.markdown
    assert "99" not in checked.markdown
    assert checked.citations == [finding.source_url]


def test_final_references_are_renumbered_and_checks_are_idempotent():
    finding = verified_finding(statement="准确率42%", evidence_quote="准确率42%")
    results = [ResearchResult(sub_question="准确率", findings=[finding])]
    report = Report(
        query="准确率",
        markdown="## 结果\n\n准确率42% [2]。\n\n## 参考来源\n[1] bad\n[2] good\n",
        citations=["https://ineligible.test", finding.source_url],
    )
    checked, validation = finalize_report(report, results)
    assert not validation.issues
    assert "42% [1]" in checked.markdown and "[2]" not in checked.markdown
    assert "ineligible" not in checked.markdown
    assert finalize_report(checked, results)[0] == checked


def test_original_source_reference_numbers_cannot_break_fallback():
    finding = verified_finding(statement="原文发现 [88]", evidence_quote="原文发现 [88]")
    checked, validation = finalize_report(
        Report(query="q", markdown="不合格 [99]", citations=[finding.source_url]),
        [ResearchResult(sub_question="q", findings=[finding])],
    )
    assert validation.issues
    assert "[88]" not in checked.markdown and "[99]" not in checked.markdown
    assert "原文发现 [1]" in checked.markdown


def test_document_labels_are_not_empirical_numbers_but_measurements_remain_checked():
    from deep_research.report.validation import validate_body

    finding = verified_finding(statement="准确率42%", evidence_quote="准确率42%")
    results = [ResearchResult(sub_question="q", findings=[finding])]
    mapping = {finding.source_url: 1}
    body = "**1. 结果。** 表 2、Figure 3 和公式 (4) 总结准确率42% [1]。"
    assert not validate_body(body, results, mapping, fallback=False).issues
    invalid = validate_body(body.replace("42%", "99%"), results, mapping, fallback=False)
    assert "unsupported_number" in invalid.issues
    math = validate_body("表达式 $x=2$ [1]。", results, mapping, fallback=False)
    assert "unsupported_number" in math.issues


def test_captions_and_bold_headings_need_no_printed_cite_but_keep_numeric_checks():
    from deep_research.report.validation import validate_body

    finding = verified_finding(statement="准确率42%", evidence_quote="准确率42%")
    evidence = [ResearchResult(sub_question="q", findings=[finding])]
    body = "**结果说明**\n\n表 1 指标对照\n\n| 指标 | 结果 |\n|---|---|\n| 准确率 | 42% [1] |"
    assert not validate_body(body, evidence, {finding.source_url: 1}, fallback=False).issues
    assert (
        "unsupported_number"
        in validate_body(
            body.replace("指标对照", "准确率99%"), evidence, {finding.source_url: 1}, fallback=False
        ).issues
    )
