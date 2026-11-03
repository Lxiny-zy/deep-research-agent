"""Absence and missing-method claims must be checked against the actual paper text."""

import json

import pytest

from deep_research.document_corpus import FullTextCorpus, mark_complete_sources
from deep_research.models import Source
from deep_research.workbench.fulltext_review import (
    FullTextChecks,
    FullTextReviewer,
    FullTextTarget,
    FullTextTargets,
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
        if schema is FullTextTargets:
            return FullTextTargets.model_validate({"targets": [
                {"unit_id": item["unit"]["id"], "target": {
                    "kind": self.kind, "document_ids": [item["documents"][0]["id"]],
                    "keywords": ["T", "S-MSA", "dropout"], "reason": "controlled target selection",
                }} for item in data["units"]
            ]})
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
async def test_an_evidence_scoped_absence_is_accepted_once_the_full_text_confirms_it():
    # 第五轮线上精读（5d5dc4c7）：「现有证据未给出…」被要求逐字改写为「全文文本中未见」
    _, corpus = make_corpus(complete=True)
    reviewer = SupportReviewer(FullTextJudge(), [], 20000, fulltext_corpus=corpus)
    scoped = SupportUnit("s", "现有证据未给出 dropout 设置的多次运行方差。", kind="prose")
    assert (await reviewer.review([scoped]))[0].verdict == "supported"
    vague = SupportUnit("v", "作者没有给出 dropout 设置。", kind="prose")
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


class SkipsOnce(FullTextJudge):
    skipped = False

    async def parse(self, system, user, schema, **kwargs):
        result = await super().parse(system, user, schema, **kwargs)
        if schema is FullTextChecks and not self.skipped:
            self.skipped = True
            return FullTextChecks(checks=result.checks[:-1])
        return result


@pytest.mark.asyncio
async def test_a_part_the_model_skipped_is_asked_again_and_the_check_completes():
    _, corpus = make_corpus(complete=True)
    llm = SkipsOnce()
    unit = SupportUnit("u", "本次取得的全文文本中未见 dropout 设置。", kind="prose")
    record = await FullTextReviewer(llm, corpus, 20000).review(unit)
    assert record["status"] == "absence_confirmed"
    assert validate_fulltext_record(unit, record, corpus) is None
    # Target, the batch, then only the skipped part.
    assert llm.calls == 3 and len(llm.parts) == 3


@pytest.mark.asyncio
async def test_a_truncated_check_is_split_instead_of_left_unfinished():
    # 第四轮线上精读与幻灯片：一批分段的逐段判断超过输出上限，整条断言记为「全文核查未完成」
    from deep_research.llm import ModelOutputTruncated

    class TruncatesLargeBatches(FullTextJudge):
        async def parse(self, system, user, schema, **kwargs):
            if schema is FullTextChecks and len(json.loads(user)["parts"]) > 1:
                self.calls += 1
                raise ModelOutputTruncated(1024)
            return await super().parse(system, user, schema, **kwargs)

    source = mark_complete_sources(
        [Source(url="https://example.org/long.pdf", content="Ordinary text. " * 1200)]
    )[0]
    corpus = FullTextCorpus([source], {})
    unit = SupportUnit("u", "全文未见 dropout 设置。", kind="prose")
    record = await FullTextReviewer(TruncatesLargeBatches(), corpus, 20000).review(unit)
    assert record["status"] == "absence_confirmed", record.get("reason")
    assert validate_fulltext_record(unit, record, corpus) is None


@pytest.mark.asyncio
async def test_a_counterquote_with_reflowed_whitespace_is_kept_verbatim():
    class Reflowed(FullTextJudge):
        async def parse(self, system, user, schema, **kwargs):
            result = await super().parse(system, user, schema, **kwargs)
            if schema is FullTextChecks:
                for row in result.checks:
                    row.quote = " ".join(row.quote.split())
            return result

    counter = "We set T = 4 in our im-\nplementation"
    _, corpus = make_corpus(second=DGSMP_PAGE8, complete=True)
    llm = Reflowed(counter)
    unit = SupportUnit("u", SC32_CLAIM)
    record = await FullTextReviewer(llm, corpus, 20000).review(unit)
    assert record["status"] == "refuted" and llm.calls == 2
    assert [row["quote"] for row in record["scanned"] if row["verdict"] == "refutes"] == [counter]
    assert validate_fulltext_record(unit, record, corpus) is None


@pytest.mark.asyncio
async def test_a_part_left_unanswered_stays_unproven_without_stopping_revision():
    from deep_research.workbench.prose_review import can_revise

    _, corpus = make_corpus(complete=True)
    llm = FullTextJudge(omit=True)
    decisions = await SupportReviewer(llm, [], 20000, fulltext_corpus=corpus).review(
        [SupportUnit("u", SC32_CLAIM)]
    )
    assert decisions[0].verdict == "uncertain"
    assert decisions[0].fulltext_review["scanned"][-1]["verdict"] == "uncertain"
    assert can_revise(decisions)


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
    assert record["failure_kind"] == "source_snapshot_conflict"
    from deep_research.workbench.prose_review import can_revise

    decision = SupportDecision(unit_id="u", verdict="uncertain", reason=record["reason"])
    assert can_revise([decision])
    assert not can_revise([
        SupportDecision(unit_id="u", verdict="uncertain", reason="全文核查未完成：TimeoutError")
    ])


@pytest.mark.parametrize(
    "text",
    [
        # 线上课题调研的原句：说的是本次证据的范围，不是某篇文献缺了什么
        "所核验证据未提供深度展开网络与端到端网络在训练样本效率上的直接对比，"
        "也未提供两类方法训练与推理计算量、显存开销的可比数值。",
        "所引条目未给出该 PSNR 数值对应的数据集，跨论文引用该数值前需核查原文实验设置。",
        "现有证据给出的图景是分散的：端到端方法报告了较好结果 [20]，缺乏可比的统一评价。",
        "表 1 中空单元格表示本次已核验发现中未收录该条目，不代表原论文未报告相应设计。",
    ],
)
def test_open_synthesis_gap_statements_describe_the_evidence_not_a_paper(text):
    from deep_research.workbench.fulltext_review import requires_fulltext

    unit = SupportUnit("u", text, kind="prose", citations=[1])
    assert requires_fulltext(unit, open_evidence=True) is False
    # 精读、评审和指定材料的综述仍要回查：那里的“证据”就是被评价的论文
    assert requires_fulltext(unit) is True


@pytest.mark.parametrize(
    "text",
    [
        "该文未报告真实数据结果。",
        "DAUNet 未给出训练时长 [5]。",
        "现有证据未给出 DAUNet 的训练时长 [5]。",
        "所引文献未提供显存开销。",
        "经核验后发现该文未报告显存开销。",
    ],
)
def test_claims_about_a_specific_paper_stay_under_fulltext_review(text):
    from deep_research.workbench.fulltext_review import requires_fulltext

    unit = SupportUnit("u", text, kind="prose", citations=[5])
    assert requires_fulltext(unit, open_evidence=True) is True


def test_critique_section_is_reviewed_by_its_citations_in_open_synthesis():
    from deep_research.workbench.fulltext_review import requires_fulltext

    unit = SupportUnit("u", "该方法在真实数据上退化 [3]。", kind="prose", context="现有方法的不足")
    assert requires_fulltext(unit) is True
    assert requires_fulltext(unit, open_evidence=True) is False


@pytest.mark.parametrize(
    "caption",
    [
        "*表注：未报告表示本次已核验发现中未列出该字段，不代表原文全文没有报告；适用条件见口径脚注。*",
        "*表注：按所引原始表格的行列呈现；未报告表示原表该格为空或使用缺失标记。*",
        "*表注：统计范围：tests/0/group_summaries；未报告表示该统计台账字段没有结果。*",
    ],
)
def test_the_renderers_table_caption_is_not_a_critique_of_a_paper(caption):
    from deep_research.workbench.fulltext_review import requires_fulltext

    # 线上派生幻灯片：表格放在「不足」页，固定表注被当作缺陷判断回查全文
    unit = SupportUnit("u", caption, kind="prose", context="两类方法的不足")
    assert requires_fulltext(unit) is False
    written = SupportUnit(
        "w", "*表注：DAUHST 未报告真实数据的参数量。*", kind="prose", context="两类方法的不足"
    )
    assert requires_fulltext(written) is True


def test_open_evidence_follows_the_task_contract():
    from deep_research.workbench.contract import build_contract
    from deep_research.workbench.prose_review import ProseReviewer
    from deep_research.workbench.templates import get_template

    def reviewer(template, strategy):
        contract = build_contract(get_template(template), "比较两类方法", strategy=strategy)
        return ProseReviewer(
            None, [], 20000, source_version="v", contract=contract
        ).reviewer.open_evidence

    assert reviewer("autoResearch", "deep") is True
    assert reviewer("litReview", "deep") is True
    assert reviewer("litReview", "none") is False
    assert reviewer("paperRead", "none") is False
    assert reviewer("peerReview", "none") is False


def test_open_synthesis_mindmap_question_is_a_direction_unless_it_asserts_absence():
    from deep_research.workbench.fulltext_review import requires_fulltext

    question = SupportUnit(
        "q", "MST++ 的注意力能否迁移到 DOE 编码？", kind="question", citations=[2]
    )
    assert requires_fulltext(question) is True
    assert requires_fulltext(question, open_evidence=True) is False
    absent = SupportUnit("q", "MST++ 为何未报告真实数据结果？", kind="question", citations=[2])
    assert requires_fulltext(absent, open_evidence=True) is True


@pytest.mark.asyncio
async def test_open_synthesis_mindmap_record_follows_its_own_scope():
    from deep_research.workbench.mindmap_contract import Mindmap, checked_review, review_record
    from deep_research.workbench.mindmap_edit import review_units
    from deep_research.workbench.support import evidence_records
    from deep_research.workbench.writers import mindmap_to_markdown

    sources, corpus = make_corpus(complete=True)
    results = research_material(sources)
    model = Mindmap(
        root="Alpha",
        branches=[{"label": "Alpha 能否用于 DOE 编码", "kind": "question", "citations": [1]}],
    )
    llm = FullTextJudge()
    reviewer = SupportReviewer(
        llm,
        evidence_records(results, {sources[0].url: 1}),
        20000,
        fulltext_corpus=corpus,
        open_evidence=True,
    )
    decisions = await reviewer.review(review_units(model, "Alpha"))
    assert not any(part for part in llm.parts)
    record = review_record(model.model_dump(), [sources[0].url], results, decisions)
    assert record["status"] == "pass"
    arguments = (model.model_dump(), [sources[0].url], results, record, mindmap_to_markdown(model))
    assert checked_review(*arguments, corpus=corpus, query="Alpha", open_evidence=True) == (
        True,
        [],
    )
    assert checked_review(*arguments, corpus=corpus, query="Alpha")[1]


@pytest.mark.asyncio
async def test_an_unattributed_absence_claim_asks_for_a_citation_and_allows_revision():
    from deep_research.workbench.prose_review import can_revise

    class NoTarget(FullTextJudge):
        async def parse(self, system, user, schema, **kwargs):
            if schema is FullTextTarget:
                self.calls += 1
                return FullTextTarget(kind="absence", document_ids=[], reason="未指明对象")
            return await super().parse(system, user, schema, **kwargs)

    _, corpus = make_corpus(complete=True)
    decisions = await SupportReviewer(NoTarget(), [], 20000, fulltext_corpus=corpus).review(
        [SupportUnit("u", SC32_CLAIM)]
    )
    assert decisions[0].verdict == "uncertain"
    assert decisions[0].fulltext_review["failure_kind"] == "target_unresolved"
    assert "附上所评价文献的引用" in decisions[0].reason
    assert can_revise(decisions)
