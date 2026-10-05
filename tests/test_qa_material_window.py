"""Answer material windows keep whole quotes, stable citations and honest omission metadata."""

from types import SimpleNamespace

from deep_research.context_budget import ContextBudget
from deep_research.workbench.qa_material_window import render_evidence_lines, select_answer_findings
from deep_research.workbench.support import evidence_id
from tests.fakes import verified_finding


def model(window=3200):
    return SimpleNamespace(
        context_window_tokens=window, settings=SimpleNamespace(llm_max_output_tokens=512)
    )


def test_complete_quotes_references_and_same_source_citations_are_preserved():
    first = verified_finding("结论甲", "https://a.test", "完整原文甲。" * 8)
    second = verified_finding("结论乙", "https://a.test", "完整原文乙。" * 8)
    third = verified_finding("结论丙", "https://b.test", "完整原文丙。" * 8)
    first.verification.source_reference = "作者甲，2026，完整出处"
    findings = [first, second, third]
    originals = [finding.model_dump() for finding in findings]
    result = select_answer_findings(
        findings, model=model(), system="系统规则", question="有哪些结论？",
        origins={"https://a.test": "paper", "https://b.test": "web"},
        reserve_dialogue_chars=64,
    )
    assert result.status == "complete" and result.can_generate
    assert result.omitted_count == 0 and not result.omitted_ids
    assert result.url_to_idx == {"https://a.test": 1, "https://b.test": 2}
    assert result.available_citations == [1, 2]
    lines, mapping = render_evidence_lines(findings, {"https://a.test": "paper"})
    assert mapping == result.url_to_idx
    assert lines[0].startswith("- [1]【本论文】") and lines[1].startswith("- [1]")
    assert lines[2].startswith("- [2]")
    for finding in findings:
        assert finding.statement in result.materials_text
        assert finding.evidence_quote in result.materials_text
    assert first.verification.source_reference in result.materials_text
    assert [finding.model_dump() for finding in findings] == originals
    assert result.dialogue_capacity_chars >= result.dialogue_reserved_chars == 64
    assert ContextBudget.from_model(model()).fits(
        "系统规则", result.prompt("文" * result.dialogue_capacity_chars)
    )


def test_oversized_individual_finding_is_skipped_without_dropping_later_material():
    large = verified_finding("大型发现", "https://large.test", "不可拆分的长引文" * 2000)
    first = verified_finding("可容纳甲", "https://a.test", "甲的完整依据。")
    second = verified_finding("可容纳乙", "https://b.test", "乙的完整依据。")
    result = select_answer_findings(
        [large, first, second], model=model(), system="规则", question="比较甲乙",
        fixed_context="当前任务范围：两种方法。", reserve_dialogue_chars=80,
    )
    assert result.status == "partial" and result.findings == [first, second]
    assert result.findings[0] is first
    assert result.omitted_ids == [evidence_id(large)] and result.omitted_count == 1
    assert "不可拆分的长引文" not in result.materials_text
    assert "另有 1 条已保存发现未纳入" in result.materials_text
    assert "不能据此推断原文缺失" in result.materials_text
    assert result.url_to_idx == {"https://a.test": 1, "https://b.test": 2}
    assert "当前任务范围：两种方法。" in result.prompt()
    assert ContextBudget.from_model(model()).fits("规则", result.prompt())


def test_no_finding_fits_reports_capacity_instead_of_truncating_a_quote():
    finding = verified_finding(evidence_quote="完整长原文" * 2000)
    result = select_answer_findings(
        [finding], model=model(), system="规则", question="问题", reserve_dialogue_chars=0,
    )
    assert result.status == "no_capacity" and not result.can_generate
    assert not result.findings and result.omitted_ids == [evidence_id(finding)]
    assert finding.evidence_quote not in result.materials_text


def test_fixed_instructions_cannot_be_silently_removed_to_fit_material():
    result = select_answer_findings(
        [verified_finding()], model=model(), system="系统规则" * 2000,
        question="当前问题", fixed_context="必须保留的研究范围", reserve_dialogue_chars=0,
    )
    assert result.status == "no_capacity" and not result.can_generate
    assert result.dialogue_capacity_chars == 0
    assert "必须保留的研究范围" in result.prompt()


def test_revision_preserves_every_bound_finding_and_existing_citation_numbers():
    first = verified_finding("原回答甲", "https://a.test", "原引用必须保持完整" * 2000)
    second = verified_finding("原回答乙", "https://b.test", "原引用乙")
    result = select_answer_findings(
        [first, second], model=model(), system="规则", question="修订原回答",
        citations=["https://b.test", "https://a.test"], preserve_all=True,
        reserve_dialogue_chars=0,
    )
    assert result.status == "bound_material_exceeds_capacity" and not result.can_generate
    assert result.findings == [first, second]
    assert not result.omitted_ids and result.omitted_count == 0
    assert result.url_to_idx == {"https://b.test": 1, "https://a.test": 2}
    assert "- [2]" in result.materials_text and "- [1]" in result.materials_text
    assert first.evidence_quote in result.materials_text


def test_legacy_character_capacity_also_leaves_room_for_dialogue():
    llm = SimpleNamespace(input_capacity_chars=700)
    result = select_answer_findings(
        [verified_finding(evidence_quote="完整原文。" * 25)], model=llm,
        system="规则", question="问题", reserve_dialogue_chars=50,
    )
    assert result.can_generate and result.dialogue_capacity_chars >= 50
    assert ContextBudget.from_model(llm).fits(
        "规则", result.prompt("文" * result.dialogue_capacity_chars)
    )
