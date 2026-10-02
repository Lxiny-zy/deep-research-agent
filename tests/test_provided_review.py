from __future__ import annotations

from types import SimpleNamespace

from deep_research.agents.base import Blackboard, RunContext
from deep_research.models import ExtractedFindingList, ResearchResult, Source
from deep_research.observability import Tracer
from deep_research.workbench.attachments import parse_attachment
from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, build_contract
from deep_research.workbench.corpus import corpus_issues
from deep_research.workbench.coverage import coverage_gaps
from deep_research.workbench.intake import PaperIntake, pasted_sources
from deep_research.workbench.revision import _used_indices
from deep_research.workbench.templates import LIT_REVIEW, PAPER_READ
from tests.fakes import FakeLLM, FakeSearch, verified_finding


def test_closed_review_retains_every_requested_paper_and_uses_corpus_coverage():
    query = "\n".join(f"https://arxiv.org/abs/2401.{i:05d}" for i in range(7))
    contract = build_contract(LIT_REVIEW, query, strategy="none")
    assert len(contract.papers) == 7 and contract.min_citations == 0
    assert "指定" in contract.render()
    assert LIT_REVIEW.workflow_for("none") == "lit_review_provided"
    assert len(build_contract(PAPER_READ, query).papers) == 7


def test_research_coverage_counts_documents_not_sections():
    contract = build_contract(LIT_REVIEW, "Review", quality={"survey_min_citations": 2})
    findings = [verified_finding(source_url=f"https://paper.test/a#chunk-{i}") for i in range(8)]
    gaps = coverage_gaps(
        {CONTRACT_SCRATCH_KEY: contract.model_dump()},
        [ResearchResult(sub_question="q", findings=findings)],
        "Review",
    )
    assert gaps.open and gaps.metrics["sources"] == 1 and gaps.metrics["source_locations"] == 8


def test_coverage_counts_versions_of_a_work_once():
    contract = build_contract(LIT_REVIEW, "Review", quality={"survey_min_citations": 2})
    findings = [
        verified_finding(source_url=f"https://arxiv.org/abs/2401.01234v{i}") for i in range(1, 7)
    ]
    gaps = coverage_gaps(
        {CONTRACT_SCRATCH_KEY: contract.model_dump()},
        [ResearchResult(sub_question="q", findings=findings)],
        "Review",
    )
    assert gaps.metrics["sources"] == 1


async def test_corpus_requires_each_input_in_the_body_and_flags_truncation():
    a = await parse_attachment(b"Alpha has verified supporting material.", "same.txt")
    b = await parse_attachment(b"Beta has other verified supporting material.", "same.txt")
    scratch = {
        CONTRACT_SCRATCH_KEY: build_contract(
            LIT_REVIEW, "Compare supplied papers", strategy="none"
        ).model_dump(),
        "attachments": [a.model_dump(), b.model_dump()],
    }
    urls = [a.sources()[0].url, b.sources()[0].url]
    results = [
        ResearchResult(
            sub_question="q", findings=[verified_finding(source_url=url) for url in urls]
        )
    ]
    assert not corpus_issues(scratch, results, urls)
    assert len(corpus_issues(scratch, results, urls[:1])) == 1
    assert _used_indices("Body [1].\n\n## 参考文献\n[2] second paper") == {1}
    scratch["attachments"][1]["truncated"] = True
    assert any("未完整" in issue for issue in corpus_issues(scratch, results, urls))


def test_redirected_sources_cover_only_the_corresponding_requested_document():
    contract = build_contract(
        LIT_REVIEW, "https://paper.test/a.pdf\nhttps://paper.test/b.pdf", strategy="none"
    )
    scratch = {
        CONTRACT_SCRATCH_KEY: contract.model_dump(),
        "intake_sources": {
            "documents": [
                {
                    "input_url": "https://paper.test/a.pdf",
                    "title": "A",
                    "source_urls": ["https://cdn.test/a#chunk-0"],
                },
                {"input_url": "https://paper.test/b.pdf", "title": "B", "source_urls": []},
            ]
        },
    }
    results = [
        ResearchResult(
            sub_question="q", findings=[verified_finding(source_url="https://cdn.test/a#chunk-0")]
        )
    ]
    issues = corpus_issues(scratch, results)
    assert len(issues) == 1 and "B" in issues[0]
    # Missing input cannot be repaired by rewriting; omitting an available paper can.
    assert not corpus_issues(scratch, results, ["https://cdn.test/a#chunk-0"], writable_only=True)
    writable = corpus_issues(scratch, results, [], writable_only=True)
    assert len(writable) == 1 and "A" in writable[0] and "正文" in writable[0]


async def test_truncated_attachment_cannot_be_hidden_by_an_older_manifest():
    attachment = await parse_attachment(b"Verified source text.", "paper.txt")
    attachment.truncated = True
    url = attachment.sources()[0].url
    scratch = {
        CONTRACT_SCRATCH_KEY: build_contract(LIT_REVIEW, "Review", strategy="none").model_dump(),
        "attachments": [attachment.model_dump()],
        "intake_sources": {
            "documents": [
                {
                    "input_url": f"https://workspace.invalid/attachments/{attachment.id}",
                    "source_urls": [url],
                    "truncated": False,
                }
            ]
        },
    }
    results = [ResearchResult(sub_question="q", findings=[verified_finding(source_url=url)])]
    assert any("未完整" in issue for issue in corpus_issues(scratch, results, [url]))
    assert not corpus_issues(scratch, results, [url], writable_only=True)
    assert scratch["intake_sources"]["documents"][0]["truncated"] is False


def test_malformed_old_manifest_does_not_crash_or_satisfy_corpus_coverage():
    scratch = {
        CONTRACT_SCRATCH_KEY: build_contract(
            LIT_REVIEW, "https://paper.test/a.pdf", strategy="none"
        ).model_dump(),
        "intake_sources": {"documents": None},
    }
    assert len(corpus_issues(scratch, [])) == 1
    scratch["intake_sources"]["documents"] = [
        {"input_url": "https://paper.test/a.pdf", "source_urls": [None, 3]},
    ]
    assert len(corpus_issues(scratch, [])) == 1


def test_pasted_source_chunks_do_not_silently_stop_at_twelve():
    chunks = pasted_sources("\n".join(f"Paragraph {i} " + "x" * 1800 for i in range(16)))
    assert len(chunks) == 16 and "Paragraph 15" in chunks[-1].content


async def test_pdf_document_import_retains_all_prepared_chunks(monkeypatch):
    from deep_research.library import ingestion
    from deep_research.workbench.intake import _fetch_document

    async def prepare(**kwargs):
        return SimpleNamespace(
            title="Paper",
            origin_url="https://paper.test/a.pdf",
            chunks=[
                {
                    "ordinal": i,
                    "content": f"Complete source paragraph {i}",
                    "locator": f"第 {i + 1} 页",
                }
                for i in range(19)
            ],
        )

    monkeypatch.setattr(ingestion, "prepare_source", prepare)
    sources = await _fetch_document("https://paper.test/a.pdf")
    assert len(sources) == 19 and sources[-1].content.endswith("18")
    assert sources[-1].locator == "第 19 页"


async def test_multi_paper_intake_keeps_late_documents_and_never_searches(settings, monkeypatch):
    from deep_research.workbench import intake

    papers = [f"https://paper.test/{i}.pdf" for i in range(3)]
    sources = [
        Source(url=f"{paper}#chunk-{j}", content=f"Evidence for paper {i}, part {j}.")
        for i, paper in enumerate(papers)
        for j in range(7)
    ]

    async def fetch(paper, ctx):
        return [s for s in sources if s.url.startswith(paper.url + "#")]

    class Reader(FakeLLM):
        async def parse(self, system, user, schema, **kwargs):
            if schema is ExtractedFindingList:
                return ExtractedFindingList(
                    findings=[
                        verified_finding("Fact", s.url, s.content) for s in sources if s.url in user
                    ]
                )
            return await super().parse(system, user, schema, **kwargs)

    class Forbidden(FakeSearch):
        async def search(self, *args, **kwargs):
            raise AssertionError("closed corpus cannot search")

    monkeypatch.setattr(intake, "fetch_paper", fetch)
    bb = Blackboard(query="Compare these papers")
    bb.scratch[CONTRACT_SCRATCH_KEY] = build_contract(
        LIT_REVIEW, "\n".join(papers), strategy="none"
    ).model_dump()
    ctx = RunContext(llm=Reader(), search_tool=Forbidden(), tracer=Tracer(), settings=settings)
    await PaperIntake().step(bb, ctx)
    assert len(bb.scratch["paper_sources"]) == 21
    assert len(bb.scratch["intake_sources"]["documents"]) == 3
    assert not corpus_issues(bb.scratch, bb.results)


async def test_missing_closed_corpus_does_not_treat_long_instructions_as_a_paper(settings):
    contract = build_contract(
        LIT_REVIEW, "Please compare only supplied papers. " * 15, strategy="none"
    )
    bb = Blackboard(
        query=contract.original_request, scratch={CONTRACT_SCRATCH_KEY: contract.model_dump()}
    )
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    await PaperIntake().step(bb, ctx)
    assert bb.scratch["intake_sources"]["mode"] == "missing" and not bb.results
