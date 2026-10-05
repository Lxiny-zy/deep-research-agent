"""Reading limitations are reproducible report content, independent of finding counts."""

from __future__ import annotations

import io
import json
from copy import deepcopy
from pathlib import Path

import pytest

from deep_research.models import ExtractionAudit, Report, ResearchResult, Source, SourceSelection
from deep_research.persistence.repository import RunDetail
from deep_research.report import render_markdown
from deep_research.report.service import ReportService

FIXTURE = json.loads((Path(__file__).parent / "fixtures/reading_limits_sc82.json").read_text(
    encoding="utf-8"
))


def sources():
    return [Source.model_validate(source) for source in FIXTURE["sources"]]


class Repo:
    def __init__(self, detail):
        self.detail = detail

    async def get_run(self, run_id):
        return self.detail

    async def get_events(self, run_id, **kwargs):
        return []


async def test_sc82_zero_finding_sources_are_disclosed_in_report_and_print_views():
    from deep_research.report.latex import render_latex
    from deep_research.report.pdf import render_pdf_html

    detail = RunDetail(
        id="sc82-replay", query="比较边际覆盖与条件覆盖", status="done", sources=sources(),
        report=Report(query="q", markdown="# 覆盖保证\n\n## 局限\n结论须满足适用条件。"),
    )
    original = deepcopy(detail)
    document = await ReportService(Repo(detail)).document(detail.id)
    text = render_markdown(document)
    assert "文献读取范围与局限" in text
    assert "Jing Lei" in text and "Vladimir Vovk" in text
    assert "仅取得摘要" in text and "未取得可读取文本" in text
    assert "不能据此断言文献未报告相关内容" in text
    assert "Jing Lei" in document.bibliography.body  # Used by the web report view.
    assert "Vladimir Vovk" in render_pdf_html(document)
    assert "Vladimir Vovk" in render_latex(document)
    assert detail == original
    assert document.references == [] and document.bibliography.documents == []


def test_a_fulltext_section_suppresses_an_abstract_only_notice_for_the_same_work():
    from deep_research.reading_limits import collect_reading_limits

    raw = sources()
    full = raw[0].model_copy(deep=True)
    full.url += "?dr_section=pdf-2"
    full.content = "This section states the complete method and its assumptions."
    full.scholarly.section = "method"
    records = collect_reading_limits([*raw, full], [])
    assert [r["title"] for r in records] == [raw[1].title]
    assert records[0]["status"] == "unavailable"


def test_explicit_shared_doi_links_an_arxiv_fulltext_to_the_metadata_record():
    from deep_research.reading_limits import collect_reading_limits

    abstract = sources()[0]
    full = abstract.model_copy(deep=True, update={"url": "https://arxiv.org/abs/2201.00001v2"})
    full.scholarly.section = "method"
    full.content = "Method and assumptions."
    assert collect_reading_limits([abstract, full], []) == []


def test_actual_fulltext_manifest_and_plain_web_pages_do_not_get_abstract_notices():
    from deep_research.document_corpus import mark_complete_sources
    from deep_research.reading_limits import collect_reading_limits

    paper = sources()[0]
    complete = mark_complete_sources([paper])
    assert collect_reading_limits(
        [*complete, Source(url="https://example.org", content="page")], [],
    ) == []


def test_only_retrieval_rejections_are_excluded_and_metadata_is_not_promoted_to_evidence():
    from deep_research.reading_limits import collect_reading_limits

    raw = sources()
    rejected = SourceSelection(
        question="q", search_query="q", backend="openalex", source=raw[0],
        verdict="irrelevant", reason="离题", evidence_quote="abstract",
    )
    result = ResearchResult(sub_question="q", extraction_audit=ExtractionAudit(
        question="q", source_selections=[rejected],
    ))
    assert len(collect_reading_limits(raw, [result])) == 1
    # Explicit reading can select it for another task.
    result.extraction_audit.sources.append(raw[0])
    assert len(collect_reading_limits(raw, [result])) == 2
    result.extraction_audit.sources.clear()
    accepted = rejected.model_copy(update={"question": "other", "verdict": "relevant"})
    result.extraction_audit.source_selections.append(accepted)
    assert len(collect_reading_limits(raw, [result])) == 2
    assert result.findings == []


def test_audit_only_sources_and_duplicate_locations_are_preserved_once():
    from deep_research.reading_limits import collect_reading_limits

    raw = sources()
    duplicate = raw[0].model_copy(update={"url": raw[0].url + "?dr_section=pdf-0"})
    result = ResearchResult(sub_question="q", extraction_audit=ExtractionAudit(
        question="q", sources=[*raw, duplicate],
    ))
    records = collect_reading_limits([], [result])
    assert len(records) == 2
    assert records[0]["status"] == "abstract_only"


def test_titles_are_literal_and_note_insertion_is_idempotent_before_references():
    from deep_research.reading_limits import append_reading_limits, collect_reading_limits

    paper = sources()[0].model_copy(update={"title": "Fake\n## Header [99] <script>"})
    records = collect_reading_limits([paper], [])
    body = "# Report\n\nChecked prose [1].\n\n## References\n[1] original"
    rendered = append_reading_limits(body, records)
    assert "\n## Header" not in rendered and "<script>" not in rendered
    assert "\\[99\\]" in rendered
    assert rendered.index("文献读取范围与局限") < rendered.index("## References")
    assert append_reading_limits(rendered, records) == rendered
    assert rendered.count("[1] original") == 1


async def test_workbench_formats_and_retry_keep_reading_limits_from_frozen_inputs(
    settings, tmp_path, monkeypatch,
):
    from docx import Document

    from deep_research.workbench.delivery import pdf
    from deep_research.workbench.delivery_store import build_or_load, retry_format
    from deep_research.workbench.publish import build_bundle
    from tests.test_delivery_persistence import detail

    run = detail("# 覆盖范围\n\n## 局限\n结论尚待验证，不能据此推断未报告的信息。")
    run.sources.extend(sources())

    def fail(*args, **kwargs):
        raise RuntimeError("temporary PDF failure")

    with monkeypatch.context() as patch:
        patch.setattr(pdf, "render_pdf", fail)
        first = build_or_load(run, str(tmp_path), None, build_bundle)
    files = {f.format: f for f in first.files}
    assert "Jing Lei" in files["md"].data.decode()
    assert "Vladimir Vovk" in files["html"].data.decode()
    docx = Document(io.BytesIO(files["docx"].data))
    assert "Vladimir Vovk" in "\n".join(p.text for p in docx.paragraphs)
    assert any(f["format"] == "pdf" and f["retryable"] for f in first.failures)

    from deep_research import reading_limits

    def no_new_reading(*args, **kwargs):
        raise AssertionError("format retry must use frozen reading information")

    monkeypatch.setattr(reading_limits, "collect_reading_limits", no_new_reading)
    second = retry_format(run, str(tmp_path), None, first.content_version, "pdf", "retry-reading")
    assert not any(f["format"] == "pdf" for f in second.failures)
    _, pdf_text = pdf.pdf_text(next(f.data for f in second.files if f.format == "pdf"))
    assert "Vladimir" in pdf_text and "Vovk" in pdf_text
    assert next(f.data for f in second.files if f.format == "md") == files["md"].data


@pytest.mark.parametrize("text", ["", "Text with no declared reading scope."])
def test_unlabelled_legacy_sources_do_not_invent_an_abstract_reading_status(text):
    from deep_research.models import ScholarlyMetadata
    from deep_research.reading_limits import append_reading_limits, collect_reading_limits

    source = Source(url="https://example.org/paper", content=text,
                    scholarly=ScholarlyMetadata(doi="10.1234/example"))
    records = collect_reading_limits([source], [])
    assert records[0]["status"] == ("unknown_scope" if text else "unavailable")
    note = append_reading_limits("body", records)
    assert "仅取得摘要" not in note


async def test_reading_notes_do_not_turn_an_unapproved_report_into_verified_prose(settings):
    from deep_research.persistence.memory_repository import InMemoryRepository
    from deep_research.workbench.publish import build_bundle
    from tests.test_content_revision import failed_parent

    repo = InMemoryRepository()
    parent = await failed_parent(repo, settings)
    before = await ReportService(repo).document(parent.id)
    await repo.save_sources(parent.id, sources())
    after = await ReportService(repo).document(parent.id)
    assert before.final_validation.support_status == after.final_validation.support_status == "fail"
    assert after.final_validation.issues == before.final_validation.issues
    assert "Jing Lei" in render_markdown(after)
    bundle = build_bundle(await repo.get_run(parent.id))
    assert any(g.name == "prose_evidence" and g.status == "fail" for g in bundle.gates)


def test_new_source_snapshots_refresh_current_delivery_without_changing_old_versions(tmp_path):
    from deep_research.workbench.delivery_store import build_or_load, load_version
    from deep_research.workbench.publish import build_bundle
    from tests.test_delivery_persistence import detail

    run = detail("# 范围\n\n## 局限\n研究结论尚待验证。")
    first = build_or_load(run, str(tmp_path), None, build_bundle)
    run.sources = sources()
    second = build_or_load(run, str(tmp_path), None, build_bundle)
    assert first.content_version != second.content_version
    assert "Jing Lei" in next(f.data for f in second.files if f.format == "md").decode()
    old = load_version(run, str(tmp_path), first.content_version)
    assert "Jing Lei" not in next(f.data for f in old.files if f.format == "md").decode()
    before = {g.name: g.status for g in first.gates}
    after = {g.name: g.status for g in second.gates}
    assert before["length"] == after["length"]  # Notices cannot satisfy the required prose length.
