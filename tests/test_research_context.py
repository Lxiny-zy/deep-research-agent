"""N1/N2 preserve scope records without promoting metadata into scientific approval."""

import json
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal

import httpx
import pytest

from deep_research.bibliography import build_bibliography
from deep_research.models import ExperimentConditions, Quantity, Report, ResearchResult, Source
from deep_research.persistence.repository import RunDetail
from deep_research.report.validation import _numbers
from deep_research.workbench.fulltext_review import FullTextReviewer, located_passages
from deep_research.workbench.prose_edit import ProseEdits, repair_paragraphs
from deep_research.workbench.prose_review import ProseReviewer
from deep_research.workbench.research_presentation import research_context, research_notes
from deep_research.workbench.research_scope import field_absence_issue
from deep_research.workbench.support import (
    SupportDecision,
    SupportReviewer,
    SupportUnit,
    digest,
    evidence_records,
)
from deep_research.workbench.tables import render_specs, render_table, review_tables
from tests.test_acceptance_api import context, seed
from tests.test_fulltext_absence import FullTextJudge, make_corpus
from tests.test_support_alignment import SelectedJudge, item
from tests.test_workbench import api_repo as api_repo
from tests.test_workbench_tables import TableJudge, admitted, inputs, quantity_spec


def measurement(*, bands=31, notes=""):
    return admitted(
        "Alpha coverage 0.908 ± 0.085",
        entity="Alpha",
        quantity=Quantity(
            metric="coverage",
            value=0.908,
            rendered="0.908",
            uncertainty=0.085,
            comparator=">=",
            unit="ratio",
        ),
        conditions=ExperimentConditions(
            dataset="CAVE",
            split="test",
            bands=bands,
            train_data="CAVE training",
            protocol="fixed mask",
            acquisition="simulated",
            notes=notes,
        ),
        quote="Alpha coverage >= 0.908 ± 0.085 ratio on CAVE test, 31 bands, "
        "CAVE training, fixed mask, simulated.",
    )


def test_original_quantity_and_conditions_survive_evidence_input_without_reverification():
    finding = measurement()
    results, mapping = inputs(finding)
    before = finding.model_dump(mode="json")
    record = evidence_records(results, mapping)[0]
    metadata = record["measurement_context"]
    assert metadata["quantity"] == before["quantity"]
    assert metadata["conditions"] == before["conditions"]
    assert metadata["quantity_status"] == "verified"
    assert metadata["condition_verification"] == "must_be_checked_against_quote"
    assert finding.model_dump(mode="json") == before


def test_partial_quantity_fields_are_not_silently_dropped():
    finding = admitted("Original unit is dB", quantity=Quantity(unit="dB"))
    metadata = evidence_records(*inputs(finding))[0]["measurement_context"]
    assert metadata["quantity"]["unit"] == "dB"
    assert metadata["quantity"]["value"] is None


async def test_numeric_table_reviews_actual_conditions_and_rejects_wrong_band_count():
    from deep_research.workbench.tables import TableSpec

    finding = measurement()
    results, mapping = inputs(finding)
    table = render_table(
        TableSpec.model_validate(quantity_spec(finding)),
        results,
        mapping,
        comparison_context=True,
    )
    assert "31 波段" in table.markdown and "fixed mask" in table.markdown
    assert "31 波段" in table.units[0].text
    assert table.block.rows[0].cells["v"].note_ref == 1
    wrong = measurement(bands=28)
    results, mapping = inputs(wrong)
    body, record = render_specs(
        "```evidence-table\n" + json.dumps(quantity_spec(wrong)) + "\n```",
        results,
        mapping,
        comparison_context=True,
    )
    assert record["comparison_context_version"] == 1
    issues = await review_tables(body, record, results, mapping, TableJudge(), 50000)
    assert issues
    assert record["decisions"][0]["verdict"] == "unsupported"


def test_note_indices_are_structural_but_measurements_and_math_stay_numeric():
    assert _numbers("0.908（注 1） [2]") == {Decimal("0.908")}
    assert _numbers("样本数为 1，结果为 2（注 3）") == {Decimal(1), Decimal(2)}
    assert Decimal(7) in _numbers("$x = 7$（注 8）")


@pytest.mark.parametrize(
    "text",
    [
        "现有研究尚未验证该方法。",
        "没有任何研究验证这一结论。",
        "No prior studies have examined this method.",
        "No prior studies have examined this method. What should we study next?",
        "The authors claim this is novel. No prior studies have examined this method.",
        "是否需要复查？现有研究尚未验证该方法。",
        "作者认为这项工作很重要，但没有任何研究验证该方法。",
    ],
)
async def test_domain_absence_cannot_pass_fresh_cached_or_stored_records(text):
    evidence = [item("a", text)]
    unit = SupportUnit("u", text, citations=[1])
    reviewer = SupportReviewer(SelectedJudge(["a"]), evidence, 50000)
    forged = SupportDecision(unit_id="u", verdict="supported", evidence_ids=["a"], reason="fake")
    reviewer.cache[digest([asdict(unit), evidence])] = forged
    decision = (await reviewer.review([unit]))[0]
    assert decision.verdict == "unsupported" and "领域" in decision.reason
    assert reviewer.record_issue(unit, forged)
    reviewer.cache.clear()
    assert (await reviewer.review([unit]))[0].verdict == "unsupported"


@pytest.mark.parametrize(
    "text",
    [
        "作者声称现有研究尚未验证该方法。",
        "是否没有任何研究验证该方法？",
        "短摘录不能证明没有任何研究验证该方法。",
        "本次检索范围内，现有研究尚未验证该方法。",
        "According to the authors, no prior studies have examined this method.",
    ],
)
def test_attribution_questions_and_explicit_scope_are_not_domain_wide_assertions(text):
    assert field_absence_issue(text) is None


async def test_faithful_abstract_translation_is_exempt_from_domain_scope_rewriting():
    text = "No prior studies have examined this method."
    reviewer = SupportReviewer(SelectedJudge(["a"]), [item("a", text, -1)], 50000)
    decision = (
        await reviewer.review([SupportUnit("u", text, kind="translation", citations=[-1])])
    )[0]
    assert decision.verdict == "supported"


async def test_all_checked_counterexamples_keep_exact_offsets_and_reject_stale_records():
    sources, corpus = make_corpus(first="Leading text. T = 4.", second="More text. T = 4.")
    unit = SupportUnit("u", "论文未报告 T 的取值。", citations=[1])
    record = await FullTextReviewer(FullTextJudge("T = 4."), corpus, 50000).review(unit)
    passages = located_passages(unit, record, corpus)
    assert len(passages) == 2
    for passage in passages:
        source = next(s for s in sources if s.url == passage["source"])
        assert source.content[passage["start"] : passage["end"]] == passage["quote"]
        assert passage["locator"] == source.locator
    assert located_passages(SupportUnit("u", "changed", citations=[1]), record, corpus) == []
    forged = deepcopy(record)
    forged["scanned"][0]["quote"] = "invented"
    assert located_passages(unit, forged, corpus) == []
    forged = deepcopy(record)
    forged["scanned"][0]["locator"] = "invented page"
    assert located_passages(unit, forged, corpus)[0]["locator"] == sources[0].locator
    del forged["scanned"][0]["locator"]
    assert len(located_passages(unit, forged, corpus)) == 2
    _, changed = make_corpus(first="changed", second=sources[1].content)
    assert located_passages(unit, record, changed) == []


async def test_paragraph_repair_receives_all_passages_without_new_evidence_ids():
    sources, _ = make_corpus(first="T = 4.", second="Implementation: T = 4.")
    finding = admitted("Alpha method overview", quote="Alpha method overview")
    finding.source_url = sources[0].url
    results = [ResearchResult(sub_question="method", findings=[finding])]

    class RepairJudge(FullTextJudge):
        async def parse(self, system, user, schema, **kwargs):
            if schema is ProseEdits:
                self.repair_system = system
                self.repair_payload = str(user)
                payload = json.loads(str(user).split("【只修订以下段落】\n", 1)[1])
                self.paragraph = payload["paragraphs"][0]
                return ProseEdits(
                    edits=[
                        {
                            "unit_id": self.paragraph["unit_id"],
                            "replacement": "建议逐项核对实现细节 [1]。",
                        }
                    ]
                )
            return await super().parse(system, user, schema, **kwargs)

    llm = RepairJudge("T = 4.")
    checker = ProseReviewer.research(llm, results, {sources[0].url: 1}, 50000, sources=sources)
    body = "论文未报告 T 的取值 [1]。"
    record = await checker.review(body)
    patched = await repair_paragraphs(llm, checker, body, record)
    assert patched == "建议逐项核对实现细节 [1]。"
    passages = llm.paragraph["located_fulltext_passages"]
    assert len(passages) == 2 and all(p["quote"] == "T = 4." for p in passages)
    assert "不自动批准新断言" in llm.repair_system
    original_evidence = json.loads(
        llm.repair_payload.split("【已核验证据】\n", 1)[1].split("\n\n【只修订", 1)[0]
    )
    assert [e["id"] for e in original_evidence] == [e["id"] for e in checker.evidence]


async def test_supporting_passages_require_a_valid_fulltext_review_status():
    _, corpus = make_corpus(first="bad design", second="bad design", complete=True)
    unit = SupportUnit("u", "Alpha method is flawed", citations=[1])
    record = await FullTextReviewer(
        FullTextJudge(kind="critique", defect="bad design"),
        corpus,
        50000,
    ).review(unit)
    assert record["status"] == "critique_supported"
    assert len(located_passages(unit, record, corpus)) == 2
    forged = deepcopy(record)
    forged["status"] = "not_applicable"
    forged["target"]["kind"] = "not_applicable"
    assert located_passages(unit, forged, corpus) == []


def test_readonly_appendix_preserves_scope_uncertainty_and_citation_occurrences():
    finding = measurement(notes="paper reference [99]")
    second = finding.model_copy(deep=True)
    second.conditions.bands = 28
    unknown = admitted("Beta score 7", entity="Beta", quantity=Quantity(metric="score", value=7))
    other_unknown = admitted(
        "Gamma score 7", entity="Gamma", quantity=Quantity(metric="score", value=7)
    )
    results, mapping = inputs(finding, second, unknown, other_unknown)
    source = Source(
        url=finding.source_url, title="Paper [42]", content=finding.evidence_quote, locator="page 2"
    )
    detail = RunDetail(
        id="r",
        query="compare",
        status="needs_review",
        results=results,
        sources=[source],
        report=Report(
            query="compare", markdown="Alpha coverage 0.908 [1].", citations=list(mapping)
        ),
    )
    before = deepcopy(detail)
    packet = research_context(detail)
    assert len(packet["measurements"]) == 4
    assert all(r["metadata_conflict"] for r in packet["measurements"][:2])
    assert packet["measurements"][2]["scope_key"] != packet["measurements"][3]["scope_key"]
    assert packet["search_scope"]["search_completed_at"] is None
    assert not packet["search_scope"]["domain_absence_established"]
    assert packet["source_coverage"][0]["citation_positions"] == [1]
    assert not packet["source_coverage"][0]["complete_manifest"]
    notes = research_notes(detail)
    assert "素材位置" in notes and r"\[99\]" in notes and r"\[42\]" in notes
    original = build_bibliography(
        detail.report.markdown, detail.report.citations, results[0].findings
    )
    displayed = build_bibliography(
        detail.report.markdown + "\n\n" + notes, detail.report.citations, results[0].findings
    )
    assert original.documents == displayed.documents
    assert original.locations == displayed.locations
    assert original.occurrences == displayed.occurrences
    assert original.cited_documents == displayed.cited_documents
    assert detail == before


async def test_research_appendix_reaches_document_and_acceptance_location(api_repo, tmp_path):
    api, repo = api_repo
    run = await seed(api, repo, tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        snapshot = await context(client, run)
        note = next(row for row in snapshot["locations"] if row["kind"] == "presentation_note")
        assert note["verification_status"] == "source_record"
        location = await client.get(
            f"/api/runs/{run}/acceptance/locations/{note['id']}?version={snapshot['document_version']}"
        )
        assert location.status_code == 200
        assert "不证明整个领域不存在其他研究" in location.json()["excerpt"]
        document = await client.get(f"/api/runs/{run}/document")
        assert "来源读取范围" in document.text
    detail = await repo.get_run(run)
    assert "来源读取范围" not in detail.report.markdown
