"""Peer review must acquire evidence from each method section before writing."""

from __future__ import annotations

import asyncio
import hashlib
from copy import deepcopy

import pytest

from deep_research.agents.base import Blackboard, RunContext
from deep_research.models import ExtractedFindingList, FindingContent, ResearchResult
from deep_research.observability import Tracer
from deep_research.workbench.attachments import Attachment, AttachmentChunk
from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, build_contract
from deep_research.workbench.paper_evidence import PaperEvidenceSelection
from deep_research.workbench.review_coverage import ReviewEvidenceCoverage, coverage_issues
from deep_research.workbench.templates import PEER_REVIEW
from tests.fakes import FakeLLM, verified_finding
from tests.test_review_coverage import NoSearch

INTRO = "We introduce a spectral reconstruction system."
FORMULATION = "The measurement operator maps the spectral cube to a coded observation."
NETWORK = "Each network stage implements one optimization update with learned parameters."


def evidence(source, quote):
    finding = verified_finding(quote, source.url, quote)
    finding.verification.source_content_hash = hashlib.sha256(source.content.encode()).hexdigest()
    finding.verification.quote_start = source.content.index(quote)
    finding.verification.quote_end = source.content.index(quote) + len(quote)
    finding.verification.claim_id = hashlib.sha256((source.url + quote).encode()).hexdigest()
    return finding


def material(*, rounds=1, network_covered=True, shared_chunk=False):
    texts = [
        ("1 Introduction", INTRO), ("3 Method", ""),
        ("3.1 Formulation", FORMULATION), ("3.2 Network", NETWORK),
        ("4 Results", "The benchmark measures reconstruction accuracy."),
    ]
    chunks = [AttachmentChunk(
        ordinal=i, locator=title, section_title=title,
        content=title + "\n" + text, section_start=True, section_end=True,
    ) for i, (title, text) in enumerate(texts)]
    if shared_chunk:
        chunks = [AttachmentChunk(
            ordinal=0, locator="Full text", content="\n\n".join(c.content for c in chunks),
        )]
    attachment = Attachment(id="b" * 24, filename="methods.txt", kind="text", size=500,
                            char_count=500, chunks=chunks)
    sources = attachment.sources()
    findings = [evidence(sources[0], INTRO)]
    if network_covered:
        findings.append(evidence(sources[0] if shared_chunk else sources[3], NETWORK))
    contract = build_contract(PEER_REVIEW, "评审论文的方法与实验依据", quality={
        "review_evidence_rounds": rounds, "max_revisions": 0,
    })
    bb = Blackboard(query=contract.original_request,
                    results=[ResearchResult(sub_question="initial", findings=findings)],
                    scratch={CONTRACT_SCRATCH_KEY: contract.model_dump(),
                             "attachments": [attachment.model_dump()]})
    return bb, sources


class Reader(FakeLLM):
    def __init__(self, sources, *, cancel_network=False, omit=False):
        super().__init__()
        self.sources, self.cancel_network, self.omit = sources, cancel_network, omit
        self.reads = []

    async def parse(self, system, user, schema, **kwargs):
        if schema is PaperEvidenceSelection:
            # An optimistic checker must not turn introduction-only evidence into method coverage.
            return PaperEvidenceSelection(sufficient=True, finding_ids=["e1"])
        if schema is ExtractedFindingList:
            quote = FORMULATION if FORMULATION in user else NETWORK
            self.reads.append(quote)
            if quote == NETWORK and self.cancel_network:
                raise asyncio.CancelledError()
            if self.omit:
                return ExtractedFindingList()
            source = next(s for s in self.sources if quote in s.content)
            return ExtractedFindingList(findings=[FindingContent(
                statement=quote, source_url=source.url, evidence_quote=quote,
            )])
        return await super().parse(system, user, schema, **kwargs)


def context(llm, settings, store=None):
    return RunContext(llm=llm, search_tool=NoSearch(), tracer=Tracer(), settings=settings,
                      artifact_store=store, run_id="peer-methods")


def test_peer_workflow_checks_coverage_between_intake_and_review():
    from deep_research.workflows import PEER_REVIEW as workflow

    roles = [step.agent for step in workflow.steps]
    assert roles.index("paper_intake") < roles.index("review_evidence_coverage")
    assert roles.index("review_evidence_coverage") < roles.index("peer_reviewer")


@pytest.mark.parametrize("shared_chunk", [False, True])
async def test_missing_method_is_read_without_repeating_the_covered_method(shared_chunk, settings):
    bb, sources = material(shared_chunk=shared_chunk)
    reader = Reader(sources)
    before = deepcopy(bb.results[0])
    await ReviewEvidenceCoverage().step(bb, context(reader, settings))
    assert reader.reads == [FORMULATION]
    assert not coverage_issues(bb.scratch, bb.results)
    assert [f.model_dump(exclude={"verification"}) for f in bb.results[0].findings] == [
        f.model_dump(exclude={"verification"}) for f in before.findings
    ]
    record = bb.scratch["review_coverage"]
    assert any("3.1 Formulation" in str(item) for item in record["documents"])
    await ReviewEvidenceCoverage().step(bb, context(reader, settings))
    assert reader.reads == [FORMULATION]


async def test_missing_section_stays_blocking_with_zero_read_rounds_or_empty_extraction(settings):
    for rounds in (0, 1):
        bb, sources = material(rounds=rounds)
        reader = Reader(sources, omit=True)
        await ReviewEvidenceCoverage().step(bb, context(reader, settings))
        assert coverage_issues(bb.scratch, bb.results)
        assert "3.1" in " ".join(coverage_issues(bb.scratch, bb.results))
        assert len(reader.reads) == rounds


async def test_coverage_binds_section_boundaries_source_bytes_and_evidence(settings):
    bb, sources = material()
    await ReviewEvidenceCoverage().step(bb, context(Reader(sources), settings))
    assert not coverage_issues(bb.scratch, bb.results)
    for field in ("content", "section_title"):
        changed = deepcopy(bb.scratch)
        changed["attachments"][0]["chunks"][2][field] += " changed"
        assert coverage_issues(changed, bb.results)
    changed = deepcopy(bb.scratch)
    changed["attachments"][0]["chunks"][2]["section_end"] = False
    assert coverage_issues(changed, bb.results)
    remaining = [
        r for r in bb.results if not any(f.evidence_quote == FORMULATION for f in r.findings)
    ]
    assert coverage_issues(bb.scratch, remaining)


async def test_cancelled_coverage_recovers_saved_method_extraction_without_repeating_it(
    settings, tmp_path,
):
    from deep_research.artifacts import ArtifactStore

    original, sources = material(network_covered=False)
    first = Reader(sources, cancel_network=True)
    with pytest.raises(asyncio.CancelledError):
        await ReviewEvidenceCoverage().step(
            original.model_copy(deep=True), context(first, settings, ArtifactStore(tmp_path)),
        )
    assert first.reads == [FORMULATION, NETWORK]
    restored = original.model_copy(deep=True)
    second = Reader(sources)
    await ReviewEvidenceCoverage().step(
        restored, context(second, settings, ArtifactStore(tmp_path)),
    )
    assert second.reads == [NETWORK]
    assert not coverage_issues(restored.scratch, restored.results)


async def test_writer_only_retry_runs_missing_coverage_before_drafting(settings, monkeypatch):
    from deep_research.workbench.writers import PeerReviewer, TemplateWriter

    bb, sources = material()
    reader = Reader(sources)
    called = []

    async def write_after_coverage(self, board, ctx):
        assert not coverage_issues(board.scratch, board.results)
        assert reader.reads == [FORMULATION]
        called.append(True)
        return board

    monkeypatch.setattr(TemplateWriter, "step", write_after_coverage)
    await PeerReviewer().step(bb, context(reader, settings))
    assert called


async def test_an_introduction_quote_cannot_be_rebased_into_a_method_span(settings):
    bb, sources = material(rounds=0, shared_chunk=True)
    intro = bb.results[0].findings[0]
    intro.verification.quote_start = sources[0].content.index(FORMULATION)
    intro.verification.quote_end = intro.verification.quote_start + len(INTRO)
    await ReviewEvidenceCoverage().step(bb, context(Reader(sources), settings))
    assert coverage_issues(bb.scratch, bb.results)


@pytest.mark.parametrize("both_methods", [False, True])
async def test_targeted_reading_localizes_repeated_quotes_to_the_method_occurrence(
    settings, both_methods,
):
    from deep_research.workbench.attachments import Attachment

    bb, _ = material(shared_chunk=True, network_covered=not both_methods)
    raw = bb.scratch["attachments"][0]
    raw["chunks"][0]["content"] = raw["chunks"][0]["content"].replace(FORMULATION, INTRO)
    if both_methods:
        raw["chunks"][0]["content"] = raw["chunks"][0]["content"].replace(NETWORK, INTRO)
    sources = Attachment.model_validate(raw).sources()
    bb.results[0].findings = [evidence(sources[0], INTRO)]
    if not both_methods:
        bb.results[0].findings.append(evidence(sources[0], NETWORK))

    class RepeatedReader(Reader):
        async def parse(self, system, user, schema, **kwargs):
            if schema is ExtractedFindingList:
                self.reads.append(INTRO)
                return ExtractedFindingList(findings=[FindingContent(
                    statement=INTRO, source_url=sources[0].url, evidence_quote=INTRO,
                )])
            return await super().parse(system, user, schema, **kwargs)

    reader = RepeatedReader(sources)
    await ReviewEvidenceCoverage().step(bb, context(reader, settings))
    assert len(reader.reads) == (2 if both_methods else 1)
    assert not coverage_issues(bb.scratch, bb.results)


async def test_writer_does_not_repeat_a_just_completed_unsuccessful_coverage_check(settings):
    from deep_research.workbench.writers import PeerReviewer

    bb, sources = material()
    reader = Reader(sources, omit=True)
    ctx = context(reader, settings)
    await ReviewEvidenceCoverage().step(bb, ctx)
    assert reader.reads == [FORMULATION]
    await PeerReviewer().step(bb, ctx)
    assert reader.reads == [FORMULATION]
    assert coverage_issues(bb.scratch, bb.results)


def test_scoped_verifier_rejects_other_sections_and_changed_source_snapshots():
    from deep_research.guardrails import EvidenceVerifier

    bb, sources = material(shared_chunk=True)
    source = sources[0]
    start = source.content.index(FORMULATION)
    checksum = hashlib.sha256(source.content.encode()).hexdigest()
    verifier = EvidenceVerifier(quote_ranges={
        (source.url, checksum): [(start, start + len(FORMULATION))],
    })
    assert not verifier.verify(evidence(source, INTRO), source).accepted
    accepted = verifier.verify(evidence(source, FORMULATION), source)
    assert accepted.accepted and accepted.finding.verification.quote_start == start
    assert INTRO not in accepted.finding.verification.evidence_context
    changed = source.model_copy(update={"content": source.content + " Changed."})
    assert not verifier.verify(evidence(source, FORMULATION), changed).accepted


async def test_arxiv_section_render_protocol_keeps_custom_method_subsections_separate(settings):
    from deep_research.models import ScholarlyMetadata, Source
    from deep_research.workbench.reader import PAPER_SOURCES_KEY

    bb, _ = material()
    sources = [Source(
        title="Paper", url=f"https://arxiv.org/abs/2201.00001?dr_section={i}",
        content=title + "\n" + text, scholarly=ScholarlyMetadata(section=kind),
    ) for i, (title, text, kind) in enumerate([
        ("Introduction", INTRO, "introduction"), ("Method", "", "method"),
        ("Spectral-wise Attention", FORMULATION, "other"), ("Network", NETWORK, "other"),
        ("Experiment", "Benchmark results.", "experiment"),
    ])]
    bb.scratch.pop("attachments")
    bb.scratch[PAPER_SOURCES_KEY] = [s.model_dump(mode="json") for s in sources]
    bb.results[0].findings = [evidence(sources[0], INTRO), evidence(sources[3], NETWORK)]
    reader = Reader(sources)
    await ReviewEvidenceCoverage().step(bb, context(reader, settings))
    assert reader.reads == [FORMULATION]
    assert not coverage_issues(bb.scratch, bb.results)
    sections = bb.scratch["review_coverage"]["documents"][0]["sections"]
    assert [s["title"] for s in sections] == ["Spectral-wise Attention", "Network"]


@pytest.mark.parametrize("bad", ["document_id", "section_id", "checks", "sections"])
async def test_malformed_saved_coverage_is_reported_as_blocking(bad, settings):
    bb, sources = material()
    await ReviewEvidenceCoverage().step(bb, context(Reader(sources), settings))
    document = bb.scratch["review_coverage"]["documents"][0]
    if bad == "document_id":
        document["id"] = []
    elif bad == "section_id":
        document["sections"][0]["id"] = []
    elif bad == "checks":
        document["sections"][0]["checks"] = {"bad": True}
    else:
        document["sections"] = None
    assert coverage_issues(bb.scratch, bb.results)


async def test_failed_method_coverage_prevents_drafting_a_review_score(settings, monkeypatch):
    from deep_research.workbench.writers import PeerReviewer, TemplateWriter

    bb, sources = material(rounds=0)

    async def forbidden(*args, **kwargs):
        raise AssertionError("A review cannot be drafted before its method evidence is covered")

    monkeypatch.setattr(TemplateWriter, "step", forbidden)
    await PeerReviewer().step(bb, context(Reader(sources), settings))
    assert "方法证据待补齐" in bb.report.markdown
    assert bb.scratch["workbench"]["extras"]["score"] is None
    assert coverage_issues(bb.scratch, bb.results)


async def test_consistency_removal_cannot_leave_a_positive_method_record(settings, monkeypatch):
    from deep_research.workbench import peer_coverage

    async def remove_new(results, *args, **kwargs):
        results[-1].findings.clear()

    monkeypatch.setattr(peer_coverage, "verify_claim_consistency", remove_new)
    bb, sources = material()
    await ReviewEvidenceCoverage().step(bb, context(Reader(sources), settings))
    assert coverage_issues(bb.scratch, bb.results)


@pytest.mark.parametrize("failure", ["load", "save"])
async def test_progress_failures_stop_additional_paid_reading(
    failure, settings, tmp_path, monkeypatch,
):
    from deep_research.artifacts import ArtifactStore
    from deep_research.research_progress import ResearchProgressError

    bb, sources = material(network_covered=False)
    reader = Reader(sources)
    store = ArtifactStore(tmp_path)
    if failure == "load":
        monkeypatch.setattr(store, "read_control_text", lambda path: "corrupt checkpoint")
    else:
        def fail_save(*args, **kwargs):
            raise OSError("disk unavailable")
        monkeypatch.setattr(store, "write_control_json", fail_save)
    with pytest.raises(ResearchProgressError):
        await ReviewEvidenceCoverage().step(bb, context(reader, settings, store))
    assert reader.reads == ([] if failure == "load" else [FORMULATION])


@pytest.mark.parametrize("legacy", [False, True])
async def test_peer_workflow_fills_method_gap_before_writing_and_export_gate_checks_it(
    settings, monkeypatch, legacy,
):
    import re

    from deep_research.orchestrator import DeepResearchAgent, create_initial_execution
    from deep_research.persistence.memory_repository import InMemoryRepository
    from deep_research.workbench.publish import build_bundle

    bb, sources = material()

    class Writer(Reader):
        async def parse(self, system, user, schema, **kwargs):
            if schema is ExtractedFindingList and FORMULATION in user and NETWORK in user:
                self.reads.append("initial")
                return ExtractedFindingList(findings=[FindingContent(
                    statement=INTRO, source_url=sources[0].url, evidence_quote=INTRO,
                )])
            return await super().parse(system, user, schema, **kwargs)

        async def stream(self, system, user, **kwargs):
            assert FORMULATION in user and NETWORK in user
            f = re.search(r"- \[(\d+)\] " + re.escape(FORMULATION), user)[1]
            n = re.search(r"- \[(\d+)\] " + re.escape(NETWORK), user)[1]
            yield "\n\n".join(
                f"## {section.title}\n{FORMULATION} [{f}] {NETWORK} [{n}]"
                for section in PEER_REVIEW.sections
            ) + "\n\n评分：7/10"

    from deep_research.workflows import WORKFLOWS

    with monkeypatch.context() as patch:
        if legacy:
            original = WORKFLOWS["peer_review"]
            patch.setitem(WORKFLOWS, "peer_review", original.model_copy(update={
                "steps": [s for s in original.steps if s.agent != "review_evidence_coverage"],
            }))
        execution = create_initial_execution(bb.query, "peer_review", settings)
    roles = [s["agent"] for s in execution.definition["steps"]]
    assert ("review_evidence_coverage" in roles) != legacy
    execution.checkpoint.update(bb.model_dump(mode="json"))
    repo = InMemoryRepository()
    run_id = await repo.create_run(bb.query, execution=execution)
    reader = Writer(sources)
    agent = DeepResearchAgent(
        settings, llm=reader, search_tool=NoSearch(), workflow="peer_review", repo=repo,
        run_id=run_id, initial_execution=execution,
    )
    await agent.run(bb.query)
    detail = await repo.get_run(run_id)
    assert detail.orchestration.definition == execution.definition
    scratch = detail.orchestration.checkpoint["scratch"]
    assert not coverage_issues(scratch, detail.results)
    assert reader.reads == ["initial", FORMULATION]
    assert "7/10" in detail.report.markdown
    bundle = build_bundle(detail)
    assert next(g for g in bundle.gates if g.name == "review_coverage").status == "pass"
    from deep_research.report.markdown import render_markdown
    from deep_research.report.service import ReportService
    from deep_research.workbench.presentation_notes import presentation_markdown

    note = presentation_markdown(detail)
    assert "评审覆盖与意见记录" in note
    assert "阅读与核验记录" not in detail.report.markdown
    source_file = next(file for file in bundle.files if file.format == "md")
    assert note in source_file.data.decode("utf-8")
    shown = await ReportService(repo).document(run_id)
    assert "评审覆盖与意见记录" in render_markdown(shown)
    if shown.final_validation.support_status == "pass":
        assert shown.bibliography.binding_status == "bound"
    import json

    frozen_review = json.loads(
        next(f.data for f in bundle.files if f.name.endswith("-review-items.json"))
    )
    assert frozen_review["reading_notes"] == note
    assert frozen_review["review"]["items"] == (
        scratch["workbench"]["extras"]["prose_review"]["peer_review"]["items"]
    )
    if any(f.format == "docx" for f in bundle.files):
        import io

        from docx import Document

        word = Document(io.BytesIO(next(f.data for f in bundle.files if f.format == "docx")))
        assert any("评审覆盖与意见记录" in paragraph.text for paragraph in word.paragraphs)
    if any(f.format == "pdf" for f in bundle.files):
        from deep_research.workbench.delivery.pdf import pdf_text

        _, text = pdf_text(next(f.data for f in bundle.files if f.format == "pdf"))
        assert "评审覆盖与意见记录" in text
    scratch["review_coverage"]["documents"][0]["sections"][0]["status"] = "fail"
    blocked = build_bundle(detail)
    assert next(g for g in blocked.gates if g.name == "review_coverage").status == "fail"
    assert {f.format for f in blocked.files} == {"md", "json"}
    import json

    review_data = next(f for f in blocked.files if f.name.endswith("-review-items.json"))
    assert json.loads(review_data.data)["delivery_blocked"] is True
