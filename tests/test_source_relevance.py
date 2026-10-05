"""Reject off-topic metadata before expensive reading; keep a recoverable audit."""

from __future__ import annotations

import asyncio
import hashlib
import json
from html import escape
from pathlib import Path

import httpx
import pytest

from deep_research.agents.researcher import Researcher
from deep_research.models import ExtractedFindingList
from deep_research.observability import Tracer
from deep_research.tools.arxiv_search import ArxivSearch
from deep_research.tools.openalex import OpenAlexSearch

FIXTURE = json.loads((Path(__file__).parent / "fixtures/source_relevance_sc82.json").read_text(
    encoding="utf-8"
))


class SelectionLLM:
    def __init__(self, mode="normal"):
        self.mode = mode
        self.screened = []
        self.extracted = []

    async def parse(self, system, user, schema, **kwargs):
        if schema.__name__ != "SourceRelevanceDecisions":
            self.extracted.append(user)
            return ExtractedFindingList()
        payload = json.loads(user)
        self.screened.append(payload)
        if self.mode == "error":
            raise RuntimeError("temporary model failure")
        if self.mode == "cancel":
            raise asyncio.CancelledError()
        if self.mode == "lease":
            from deep_research.persistence.repository import LeaseLostError

            raise LeaseLostError("lost")
        decisions = []
        for source in payload["sources"]:
            saved = next((s for s in FIXTURE["sources"] if s["title"] == source["title"]), None)
            decisions.append({
                "source_id": source["id"],
                "verdict": saved["verdict"] if saved else "uncertain",
                "reason": saved["reason"] if saved else "摘要信息不足，保留候选。",
                "evidence_quote": saved["evidence_quote"] if saved else "",
            })
        if self.mode == "missing":
            decisions.pop()
        elif self.mode == "duplicate":
            decisions[-1] = decisions[0]
        elif self.mode == "unknown_id":
            decisions[0]["source_id"] = "invented"
        elif self.mode == "invented_quote":
            for decision in decisions:
                decision["evidence_quote"] = "not in the actual metadata"
        elif self.mode == "empty_reason":
            for decision in decisions:
                decision["reason"] = "   "
        return schema.model_validate({"decisions": decisions})


class Fetcher:
    def __init__(self):
        self.read = []

    async def sections(self, source, question, **kwargs):
        self.read.append((source, question))
        return [source]

    async def aclose(self):
        pass


def backend_tool(backend, items=None):
    items = items if items is not None else FIXTURE["sources"]
    fetcher = Fetcher()
    if backend == "arxiv":
        feed = '<feed xmlns="http://www.w3.org/2005/Atom">' + "".join(
            f'<entry><id>https://arxiv.org/abs/2401.{i:05d}</id>'
            f'<title>{escape(s["title"])}</title><summary>{escape(s["content"])}</summary>'
            '</entry>' for i, s in enumerate(items)
        ) + '</feed>'
        tool = ArxivSearch(fulltext=True, eprint_fetcher=fetcher)

        def handler(request):
            return httpx.Response(200, text=feed)
    else:
        works = [{
            "id": f"https://openalex.org/W{i}", "doi": f"https://doi.org/10.1/item{i}",
            "display_name": s["title"],
            "abstract_inverted_index": {s["content"]: [0]} if s["content"] else {},
            "best_oa_location": {"pdf_url": f"https://example.org/{i}.pdf"},
        } for i, s in enumerate(items)]
        tool = OpenAlexSearch(fulltext=True, pdf_fetcher=fetcher)

        def handler(request):
            return httpx.Response(200, json={"results": works})
    tool._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return tool, fetcher


def test_recorded_metadata_fixture_is_exact_and_covers_shared_word_false_matches():
    assert len(FIXTURE["sources"]) == 3
    for source in FIXTURE["sources"]:
        assert hashlib.sha256(source["content"].encode()).hexdigest() == source["content_sha256"]
    assert "prediction" in FIXTURE["sources"][0]["content"]
    assert "conformal" in FIXTURE["sources"][1]["title"]


@pytest.mark.parametrize("backend", ["arxiv", "openalex"])
async def test_sc82_off_topic_sources_are_skipped_before_fulltext_and_kept_in_audit(
    backend, settings,
):
    tool, fetcher = backend_tool(backend)
    llm, tracer = SelectionLLM(), Tracer()
    try:
        result = await Researcher(llm, tool, tracer, settings).run(
            FIXTURE["question"], search_queries=["conformal predictive inference"]
        )
    finally:
        await tool.aclose()
    assert [s.title for s, _ in fetcher.read] == [FIXTURE["sources"][2]["title"]]
    assert llm.screened[0]["question"] == FIXTURE["question"]
    assert "Whatever next?" not in str(llm.extracted)
    records = result.extraction_audit.source_selections
    assert [r.verdict for r in records] == ["irrelevant", "irrelevant", "relevant"]
    assert records[0].reason == FIXTURE["sources"][0]["reason"]
    assert records[0].source.content == llm.screened[0]["sources"][0]["abstract"]
    assert records[0].search_query == "conformal predictive inference"
    assert any(e.data and e.data.get("category") == "source_relevance" for e in tracer.events)


@pytest.mark.parametrize("mode", [
    "missing", "duplicate", "unknown_id", "invented_quote", "empty_reason", "error",
])
async def test_unreliable_selection_cannot_silently_drop_candidates(mode, settings):
    tool, fetcher = backend_tool("openalex")
    llm = SelectionLLM(mode)
    try:
        result = await Researcher(llm, tool, Tracer(), settings).run(FIXTURE["question"])
    finally:
        await tool.aclose()
    assert len(fetcher.read) == 3
    records = result.extraction_audit.source_selections
    assert len(records) == 3 and not any(r.verdict == "irrelevant" for r in records)
    assert all(r.reason for r in records)


async def test_all_rejected_candidates_remain_a_durable_zero_finding_result(settings, tmp_path):
    from deep_research.artifacts import ArtifactStore
    from deep_research.research_progress import ResearchProgress

    tool, fetcher = backend_tool("openalex", FIXTURE["sources"][:2])
    llm = SelectionLLM()
    try:
        result = await Researcher(llm, tool, Tracer(), settings).run(FIXTURE["question"])
    finally:
        await tool.aclose()
    assert fetcher.read == [] and llm.extracted == []
    assert not result.findings and len(result.extraction_audit.source_selections) == 2
    store = ResearchProgress(ArtifactStore(tmp_path), {"run_id": "one"})
    key = store.key(FIXTURE["question"], [])
    store.save(key, result)
    restored = store.load(key, FIXTURE["question"])
    assert restored.model_dump() == result.model_dump()


@pytest.mark.parametrize("mode", ["cancel", "lease"])
@pytest.mark.parametrize("composite", [False, True])
async def test_cancel_or_lost_lease_never_falls_back_to_unfiltered_fulltext(
    mode, composite, settings,
):
    from deep_research.persistence.repository import LeaseLostError
    from deep_research.tools.composite import MultiBackendSearch

    tool, fetcher = backend_tool("openalex")
    try:
        with pytest.raises(asyncio.CancelledError if mode == "cancel" else LeaseLostError):
            await Researcher(
                SelectionLLM(mode), MultiBackendSearch([tool]) if composite else tool,
                Tracer(), settings,
            ).run(FIXTURE["question"])
    finally:
        await tool.aclose()
    assert fetcher.read == []


async def test_explicit_paper_lookup_bypasses_open_search_filter():
    from deep_research.source_relevance import SourceSelector, source_selection_scope

    tool, fetcher = backend_tool("arxiv", FIXTURE["sources"][:1])
    llm = SelectionLLM()
    selector = SourceSelector(llm, "unrelated task", Tracer())
    try:
        with source_selection_scope(selector):
            sources = await tool.lookup("2401.00000")
    finally:
        await tool.aclose()
    assert sources and len(fetcher.read) == 1
    assert llm.screened == [] and selector.records == []


async def test_concurrent_questions_do_not_share_decisions_or_leak_the_scope(settings):
    tool, fetcher = backend_tool("openalex")
    llm = SelectionLLM()
    researcher = Researcher(llm, tool, Tracer(), settings)
    try:
        results = await asyncio.gather(
            researcher.run("question-one"), researcher.run("question-two"),
        )
        for result in results:
            records = result.extraction_audit.source_selections
            assert len(records) == 3
            assert {r.question for r in records} == {result.sub_question}
        assert {q for _, q in fetcher.read} == {"question-one", "question-two"}
        fetcher.read.clear()
        await tool.search("standalone")
        assert len(fetcher.read) == 3 and len(llm.screened) == 2
    finally:
        await tool.aclose()


async def test_missing_metadata_is_retained_and_does_not_invent_relevance(settings):
    tool, fetcher = backend_tool("openalex", [{"title": "", "content": ""}])
    try:
        result = await Researcher(SelectionLLM(), tool, Tracer(), settings).run(
            "coverage guarantee",
        )
    finally:
        await tool.aclose()
    assert len(fetcher.read) == 1
    assert result.extraction_audit.source_selections[0].verdict == "uncertain"


def test_old_extraction_audits_keep_identical_serialization():
    from deep_research.models import ExtractionAudit

    old = {"version": 2, "question": "q", "sources": [], "candidates": [], "issues": []}
    restored = ExtractionAudit.model_validate(old)
    assert restored.model_dump(mode="json") == old
    assert json.loads(restored.model_dump_json()) == old


def test_audit_output_schema_keeps_evidence_and_selection_fields():
    from deep_research.models import ExtractionAudit

    schema = ExtractionAudit.model_json_schema(mode="serialization")
    fields = schema["properties"]
    assert {"sources", "candidates", "issues", "source_selections"}.issubset(fields)
    assert fields["source_selections"]["items"]["$ref"].endswith("/SourceSelection")


async def test_research_checkpoint_reuses_selection_without_new_llm_or_downloads(
    settings, tmp_path,
):
    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.artifacts import ArtifactStore
    from deep_research.models import ResearchPlan, SubQuestion

    tool, fetcher = backend_tool("openalex", FIXTURE["sources"][:2])
    llm = SelectionLLM()
    board = Blackboard(query="q", plan=ResearchPlan(
        interpretation="q", sub_questions=[SubQuestion(question="q")],
    ))

    def context():
        return RunContext(llm=llm, search_tool=tool, tracer=Tracer(), settings=settings,
                          artifact_store=ArtifactStore(tmp_path), run_id="r")

    try:
        first = await Researcher().step(board.model_copy(deep=True), context())
        assert len(first.results) == 1
        llm.mode = "lease"  # A new request would fail; successful restore must not call it.
        resumed = await Researcher().step(board.model_copy(deep=True), context())
    finally:
        await tool.aclose()
    assert resumed.results == first.results
    assert len(llm.screened) == 1 and fetcher.read == []
