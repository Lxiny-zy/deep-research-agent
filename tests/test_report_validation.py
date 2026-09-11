from __future__ import annotations

import pytest

from deep_research.models import Report, ResearchResult
from deep_research.report.validation import finalize_report
from tests.fakes import verified_finding


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
