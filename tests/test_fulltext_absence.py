"""Absence and missing-method claims must be checked against the actual paper text."""

import json

import pytest

from deep_research.document_corpus import FullTextCorpus, mark_complete_sources
from deep_research.models import Source
from deep_research.workbench.fulltext_review import (
    FullTextChecks,
    FullTextReviewer,
    FullTextTarget,
    validate_fulltext_record,
)
from deep_research.workbench.support import (
    SupportDecision,
    SupportDecisions,
    SupportReviewer,
    SupportUnit,
)

SC32_CLAIM = "现有证据未给出 T 的具体取值，建议核对原文实现细节以确认。"
SC56_CLAIM = (
    "【待研究】（未明）MST++ 是否也采用类似 MST 的 Mask-guided Mechanism 来引导光谱注意力？"
)

# Original PDFs are retained under artifacts/acceptance/20261002/review-inputs/.
# These are excerpts, not fixtures purporting to contain either whole paper.
DGSMP_PDF_SHA256 = "68135460fbabfcde81ec13ebe934c43936a3cc4bd57b03c65c65fd39a529db81"
DGSMP_PAGE8 = (
    "results with different number of stages are shown in Fig. 7\n"
    "(b), from which we observe that increasing the stage num-\n"
    "ber T leads to better performance. We set T = 4 in our im-\n"
    "plementation for achieving a good trade-off between recon-\n"
    "struction performance and computational complexity."
)
MSTPP_PDF_SHA256 = "ff7eebb79165c378456bfce9473327bc1ac3ecdae13c1cfe0c9f014c58ab5b64"
MSTPP_PAGE4 = (
    "Different from original MSAs, our\n"
    "S-MSA treats each spectral representation as a token and\n"
    "calculates self-attention for headj:\n"
    "  \\ mathbf {A}_j\n"
    " = \\t ext { s oftmax}(\\sigma _j \\mathbf {K}_j^\\text {T} \\mathbf {Q}_j), "
    "~~{head}_j =\\mathbf {V}_j \\mathbf {A}_j, \\label {s-attention} \\vspace {-0.2mm} \n"
    "(2)"
)


class ExemptEverything:
    async def parse(self, system, user, schema, **kwargs):
        assert schema is SupportDecisions
        return SupportDecisions(
            decisions=[
                SupportDecision(
                    unit_id=unit["id"], verdict="non_factual", reason="fixture exemption"
                )
                for unit in json.loads(user)["units"]
            ]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    SC32_CLAIM, SC56_CLAIM,
    "【待研究】MST++ 的光谱注意力机制是什么？",
])
async def test_saved_failure_cases_cannot_be_exempted_without_fulltext_checks(text):
    reviewer = SupportReviewer(ExemptEverything(), [], 20000)
    decision = (await reviewer.review([SupportUnit("u", text, kind="question")]))[0]
    assert decision.verdict in {"unsupported", "uncertain"}
    assert "全文" in decision.reason


class FullTextJudge:
    def __init__(self, counter="", *, kind="absence", defect="", omit=False, forged=False):
        self.counter, self.kind, self.defect = counter, kind, defect
        self.omit, self.forged = omit, forged
        self.parts = []
        self.calls = 0

    async def parse(self, system, user, schema, **kwargs):
        self.calls += 1
        data = json.loads(user)
        if schema is FullTextTarget:
            return FullTextTarget(
                kind=self.kind,
                document_ids=[data["documents"][0]["id"]],
                keywords=["T", "S-MSA", "dropout"],
                reason="controlled target selection",
            )
        if schema is FullTextChecks:
            self.parts.extend(data["parts"])
            rows = []
            for part in data["parts"]:
                quote = self.counter if self.counter and self.counter in part["text"] else ""
                verdict = "refutes" if quote else "not_relevant"
                if self.defect and self.defect in part["text"]:
                    verdict, quote = "supports", self.defect
                rows.append(
                    dict(
                        part_id=part["id"],
                        verdict=verdict,
                        quote="invented source text" if quote and self.forged else quote,
                        reason="原文已经给出该信息" if quote else "本段没有所查信息",
                    )
                )
            return FullTextChecks(checks=rows[:-1] if self.omit else rows)
        assert schema is SupportDecisions
        return SupportDecisions(
            decisions=[
                SupportDecision(
                    unit_id=unit["id"],
                    verdict="supported"
                    if self.kind != "not_applicable"
                    and unit["id"] in data.get("fulltext_checks", {})
                    else "non_factual",
                    evidence_ids=[],
                    reason="use only the program's bound full-text check",
                )
                for unit in data["units"]
            ]
        )


def make_corpus(first="Method overview.", second="Implementation details.", *, complete=False):
    sources = [
        Source(
            url=f"https://example.org/alpha.pdf#chunk-{index}",
            title="Alpha",
            content=text,
            locator=f"page {index + 1}",
        )
        for index, text in enumerate([first, second])
    ]
    if complete:
        sources = mark_complete_sources(sources)
    return sources, FullTextCorpus(sources, {sources[0].url: 1})


@pytest.mark.asyncio
@pytest.mark.parametrize("claim, counter", [
    (SC32_CLAIM, DGSMP_PAGE8), (SC56_CLAIM, MSTPP_PAGE4),
    ("MST++ 是否也采用类似 MST 的 Mask-guided Mechanism 来引导光谱注意力？", MSTPP_PAGE4),
    ("【待研究】MST++ 的光谱注意力机制是什么？", MSTPP_PAGE4),
])
async def test_original_counterexamples_are_found_outside_the_selected_excerpts(claim, counter):
    sources, corpus = make_corpus(second=counter)
    llm = FullTextJudge(counter)
    reviewer = SupportReviewer(
        llm,
        [
            dict(
                id="chosen",
                citation=1,
                source=sources[0].url,
                statement="Selected finding",
                quote=sources[0].content,
            )
        ],
        20000,
        fulltext_corpus=corpus,
    )
    unit = SupportUnit("u", claim, kind="question", citations=[1])
    decision = (await reviewer.review([unit]))[0]
    assert decision.verdict == "unsupported"
    assert counter in decision.reason and sources[1].url in decision.reason
    assert decision.fulltext_review["status"] == "refuted"
    assert validate_fulltext_record(unit, decision.fulltext_review, corpus) is None


@pytest.mark.asyncio
async def test_absence_requires_every_original_part_and_explicit_fulltext_wording():
    sources, corpus = make_corpus(complete=True)
    llm = FullTextJudge()
    reviewer = SupportReviewer(llm, [], 20000, fulltext_corpus=corpus)
    unit = SupportUnit("u", "本次取得的全文文本中未见 dropout 的设置。", kind="prose")
    decision = (await reviewer.review([unit]))[0]
    assert decision.verdict == "supported"
    assert not decision.evidence_ids
    assert {part["source"] for part in llm.parts} == {source.url for source in sources}
    assert validate_fulltext_record(unit, decision.fulltext_review, corpus) is None
    count = llm.calls
    assert (await reviewer.review([unit]))[0] == decision
    assert llm.calls == count
    vague = SupportUnit("v", "论文未给出 dropout 设置。", kind="prose")
    assert (await reviewer.review([vague]))[0].verdict == "unsupported"


@pytest.mark.asyncio
async def test_a_partial_document_cannot_prove_absence_and_other_papers_cannot_refute_it():
    sources, _ = make_corpus(complete=True)
    other = Source(url="https://example.org/beta.pdf", content=DGSMP_PAGE8)
    corpus = FullTextCorpus([sources[0], other], {sources[0].url: 1, other.url: 2})
    llm = FullTextJudge(DGSMP_PAGE8)
    unit = SupportUnit("u", "Alpha 全文未见 T 的具体取值。", kind="prose", citations=[1])
    record = await FullTextReviewer(llm, corpus, 20000).review(unit)
    assert record["status"] == "uncertain"
    assert all(part["source"] == sources[0].url for part in llm.parts)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["omit", "forged"])
async def test_missing_checks_and_invented_counterquotes_never_become_fulltext_proof(failure):
    _, corpus = make_corpus(second=DGSMP_PAGE8, complete=True)
    llm = FullTextJudge(DGSMP_PAGE8, **{failure: True})
    record = await FullTextReviewer(llm, corpus, 20000).review(SupportUnit("u", SC32_CLAIM))
    assert record["status"] == "uncertain"


@pytest.mark.asyncio
async def test_all_bytes_are_checked_when_a_document_needs_multiple_model_requests():
    source = mark_complete_sources(
        [Source(url="https://example.org/long.pdf", content="Ordinary text. " * 1200)]
    )[0]
    corpus = FullTextCorpus([source], {})
    llm = FullTextJudge()
    unit = SupportUnit("u", "全文未见 dropout 设置。", kind="prose")
    record = await FullTextReviewer(llm, corpus, 7000).review(unit)
    assert record["status"] == "absence_confirmed"
    assert len(record["scanned"]) > 1
    assert validate_fulltext_record(unit, record, corpus) is None
    record["scanned"].pop()
    assert validate_fulltext_record(unit, record, corpus)


async def test_fulltext_progress_reports_actual_batches_without_skipping_sources():
    source = mark_complete_sources([
        Source(url="https://example.org/progress.pdf", content="Ordinary text. " * 1200)
    ])[0]
    corpus = FullTextCorpus([source], {})
    llm, progress = FullTextJudge(), []
    unit = SupportUnit("u", "本次取得的全文文本中未见 dropout 设置。", kind="prose")
    record = await FullTextReviewer(
        llm, corpus, 7000, on_progress=progress.append
    ).review(unit)
    assert record["status"] == "absence_confirmed"
    assert validate_fulltext_record(unit, record, corpus) is None
    assert len(progress) == llm.calls
    assert progress[0] == "正在定位需回查全文的断言与文献…"
    batches = llm.calls - 1
    assert batches > 1
    assert progress[1:] == [
        f"正在回查原文（第 {index}/{batches} 批）…" for index in range(1, batches + 1)
    ]


async def test_fulltext_transport_failure_does_not_request_answer_revisions():
    from deep_research.workbench.prose_review import can_revise

    class Unavailable(FullTextJudge):
        async def parse(self, *args, **kwargs):
            self.calls += 1
            raise TimeoutError("upstream timed out")

    _, corpus = make_corpus(complete=True)
    llm = Unavailable()
    reviewer = SupportReviewer(llm, [], 20000, fulltext_corpus=corpus)
    decisions = await reviewer.review([SupportUnit("u", SC32_CLAIM)])
    assert decisions[0].verdict == "uncertain" and llm.calls == 1
    assert not can_revise(decisions)


@pytest.mark.asyncio
async def test_factual_criticism_is_checked_but_pure_advice_can_be_exempted():
    quote = "Evaluation uses only one scene."
    _, corpus = make_corpus(second=quote, complete=True)
    unit = SupportUnit("u", "评估范围过窄。", context="缺点", kind="prose")
    decision = (
        await SupportReviewer(
            FullTextJudge(kind="critique", defect=quote),
            [],
            20000,
            fulltext_corpus=corpus,
        ).review([unit])
    )[0]
    assert decision.verdict == "supported"
    advice = SupportUnit("v", "建议增加跨域实验。", context="缺点", kind="prose")
    llm = FullTextJudge(kind="not_applicable")
    decision = (await SupportReviewer(llm, [], 20000, fulltext_corpus=corpus).review([advice]))[0]
    assert decision.verdict == "non_factual" and not llm.parts


def research_material(sources):
    from deep_research.models import ExtractionAudit, ResearchResult
    from tests.fakes import verified_finding

    return [
        ResearchResult(
            sub_question="Alpha",
            findings=[verified_finding("Method overview", sources[0].url, sources[0].content)],
            extraction_audit=ExtractionAudit(question="Alpha", sources=sources),
        )
    ]


@pytest.mark.asyncio
async def test_final_prose_and_export_bind_fulltext_proof_and_reject_missing_or_stale_proof():
    from copy import deepcopy

    from deep_research.bibliography import build_bibliography
    from deep_research.workbench.citation_binding import bind_review
    from deep_research.workbench.delivery.html import render_html
    from deep_research.workbench.prose_review import ProseReviewer

    sources, _ = make_corpus(complete=True)
    results = research_material(sources)
    body = "本次取得的全文文本中未见 dropout 的设置 [1]。"
    reviewer = ProseReviewer.research(FullTextJudge(), results, {sources[0].url: 1}, 20000)
    record = await reviewer.review(body)
    assert record["status"] == "pass"
    assert reviewer.check(body, record) == (True, [])
    catalog = build_bibliography(body, [sources[0].url], results[0].findings, sources)
    assert bind_review(catalog, reviewer, body, record)
    assert catalog.occurrences[0].scope == "fulltext_review"
    assert "全文核查" in catalog.occurrences[0].review_note
    html = render_html(catalog.body, title="Report", bibliography=catalog, evidence=[])
    assert "全文核查" in html and "未绑定依据" not in html
    forged = deepcopy(record)
    for decision in forged["decisions"]:
        decision.pop("fulltext_review", None)
    assert reviewer.check(body, forged)[1]
    reviewer.reviewer.cache.clear()
    reviewer.prime(body, forged)
    assert not reviewer.reviewer.cache
    assert not bind_review(catalog, reviewer, body, forged)
    changed = research_material(
        [sources[0], sources[1].model_copy(update={"content": "Dropout is 0.1."})]
    )
    current = ProseReviewer.research(None, changed, {sources[0].url: 1}, 20000)
    assert not current.check(body, record)[0]


@pytest.mark.asyncio
async def test_mindmap_record_rejects_an_exempted_unknown_node_and_survives_valid_fulltext_review():
    from copy import deepcopy

    from deep_research.workbench.mindmap_contract import Mindmap, checked_review, review_record
    from deep_research.workbench.mindmap_edit import review_units
    from deep_research.workbench.support import evidence_records
    from deep_research.workbench.writers import mindmap_to_markdown

    sources, corpus = make_corpus(complete=True)
    results = research_material(sources)
    model = Mindmap(
        root="Alpha",
        branches=[
            {
                "label": "本次取得的全文文本中未见 dropout 设置",
                "kind": "question",
                "citations": [1],
            }
        ],
    )
    reviewer = SupportReviewer(
        FullTextJudge(),
        evidence_records(results, {sources[0].url: 1}),
        20000,
        fulltext_corpus=corpus,
    )
    decisions = await reviewer.review(review_units(model, "Alpha"))
    record = review_record(model.model_dump(), [sources[0].url], results, decisions)
    assert record["status"] == "pass"
    assert checked_review(
        model.model_dump(),
        [sources[0].url],
        results,
        record,
        mindmap_to_markdown(model),
        corpus=corpus,
        query="Alpha",
    ) == (True, [])
    forged = deepcopy(record)
    forged["decisions"][1].update(verdict="non_factual", fulltext_review=None)
    assert checked_review(
        model.model_dump(),
        [sources[0].url],
        results,
        forged,
        mindmap_to_markdown(model),
        corpus=corpus,
        query="Alpha",
    )[1]


def test_normal_judge_schema_cannot_request_program_owned_fulltext_proof():
    assert "fulltext_review" not in SupportDecision.model_json_schema()["properties"]


@pytest.mark.asyncio
async def test_two_versions_of_the_same_source_cannot_be_mixed_to_refute_a_claim():
    source = Source(url="https://example.org/alpha.pdf", content=DGSMP_PAGE8)
    corpus = FullTextCorpus(
        [source, source.model_copy(update={"content": "No implementation appendix."})],
        {source.url: 1},
    )
    llm = FullTextJudge(DGSMP_PAGE8)
    record = await FullTextReviewer(llm, corpus, 20000).review(
        SupportUnit("u", SC32_CLAIM, citations=[1])
    )
    assert record["status"] == "uncertain"
    assert not llm.parts
