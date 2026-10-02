from __future__ import annotations

import json
from copy import deepcopy

import pytest

from deep_research.agents.base import Blackboard, RunContext
from deep_research.models import ExtractedFindingList, ResearchResult, Source
from deep_research.observability import Tracer
from deep_research.report.validation import validate_body
from deep_research.workbench.prose_review import ProseReviewer, prose_units
from deep_research.workbench.support import SupportDecisions
from tests.fakes import FakeLLM, FakeSearch, verified_finding


def findings():
    return [
        ResearchResult(
            sub_question="关系",
            findings=[
                verified_finding(
                    "变量存在相关关系，不能据此证明因果关系",
                    evidence_quote="变量存在相关关系，未证明因果关系。",
                )
            ],
        )
    ]


def reviewer(llm=None, results=None, **kwargs):
    return ProseReviewer.research(
        llm or FakeLLM(),
        results or findings(),
        {"https://a.com": 1},
        50000,
        query="解释相关关系",
        **kwargs,
    )


async def test_changed_support_policy_cannot_reuse_an_old_positive_review(monkeypatch):
    from deep_research.workbench import prose_review

    checker = reviewer()
    body = "存在相关关系 [1]。"
    record = await checker.review(body)
    assert checker.check(body, record)[0]
    monkeypatch.setattr(
        prose_review, "SUPPORT_POLICY_VERSION", prose_review.SUPPORT_POLICY_VERSION + 1
    )
    assert not checker.check(body, record)[0]
    assert not checker.prime(body, record)


@pytest.mark.parametrize(
    "body",
    [
        "表 1 汇总三篇文献。三者的输入性质不同，任何跨行的精度比较都不成立。",
        "注：三行任务的输入测量互不相同，其精度指标不可跨行比较。",
        "Table 1. These inputs are different and cannot be directly compared.",
    ],
)
async def test_factual_comparability_cannot_be_exempted_as_table_layout(body):
    class ExemptingJudge(FakeLLM):
        async def parse(self, system, user, schema, **kwargs):
            data = json.loads(user)
            return SupportDecisions(
                decisions=[
                    {
                        "unit_id": unit["id"],
                        "verdict": "non_factual",
                        "evidence_ids": [],
                        "reason": "表格编排与方法学比较边界说明",
                    }
                    for unit in data["units"]
                ]
            )

    checker = reviewer(ExemptingJudge())
    audit = await checker.review(body)
    assert audit["status"] == "fail" and audit["can_revise"]
    assert "不能作为纯编排说明免检" in audit["issues"][0]
    # A stored successful label cannot bypass the same check at export or seed its cache.
    forged = deepcopy(audit)
    forged["status"] = "pass"
    for item in forged["decisions"]:
        item["verdict"] = "non_factual"
    bound, issues = checker.check(body, forged)
    assert bound and any("错误归类" in issue for issue in issues)
    checker.reviewer.cache.clear()
    assert checker.prime(body, forged) and not checker.reviewer.cache


@pytest.mark.parametrize(
    "text",
    [
        "表 1 汇总方法、输入和评价指标。",
        "注：空栏表示本表没有列出该项目。",
        "请核查两项研究的输入是否相同。",
        "需进一步确认这些结果是否可以比较。",
        "Investigate whether the inputs are different.",
    ],
)
def test_pure_layout_and_open_comparison_questions_are_not_factual_claims(text):
    from deep_research.workbench.support import asserted_comparison

    assert not asserted_comparison(text)


async def test_comparison_can_pass_when_the_judge_binds_supporting_evidence():
    results = [
        ResearchResult(
            sub_question="输入",
            findings=[
                verified_finding("两项任务的输入相同", evidence_quote="两项任务的输入相同。")
            ],
        )
    ]
    audit = await reviewer(results=results).review("两项任务的输入相同 [1]。")
    assert audit["status"] == "pass"
    assert audit["decisions"][0]["evidence_ids"]


class Judge(FakeLLM):
    """Test decisions inspect actual text; unlike broad integration fixtures."""

    def __init__(self):
        super().__init__()
        self.judged = []

    async def parse(self, system, user, schema, **kwargs):
        if schema is ExtractedFindingList:
            return ExtractedFindingList(findings=findings()[0].findings)
        if schema is not SupportDecisions:
            return await super().parse(system, user, schema, **kwargs)
        data = json.loads(user)
        self.judged.extend(data["units"])
        return SupportDecisions(
            decisions=[
                {
                    "unit_id": u["id"],
                    "verdict": "unsupported"
                    if "已经证明因果" in u["text"]
                    else ("supported" if u["citations"] else "non_factual"),
                    "evidence_ids": [
                        e["id"] for e in data["evidence"] if e["citation"] in u["citations"]
                    ],
                    "reason": "引用只支持相关，没有因果证据"
                    if "已经证明因果" in u["text"]
                    else "支持",
                }
                for u in data["units"]
            ]
        )


class CorrelationSearch(FakeSearch):
    async def search(self, query, **kwargs):
        return [
            Source(
                title="观察记录", url="https://a.com", content="变量存在相关关系，未证明因果关系。"
            )
        ]


async def test_valid_citation_and_numbers_do_not_prove_new_causal_explanation():
    body = "变量已经证明因果关系 [1]。"
    assert not validate_body(body, findings(), {"https://a.com": 1}, fallback=False).issues
    audit = await reviewer(Judge()).review(body)
    assert audit["status"] == "fail" and "因果" in audit["issues"][0]


async def test_invalid_prose_citation_is_a_repairable_content_error():
    audit = await reviewer().review("结论 [999]。")
    assert audit["status"] == "fail" and audit["can_revise"]


def test_material_preserves_verified_findings_after_the_old_120_limit():
    from deep_research.workbench.writers import eligible_material

    results = [
        ResearchResult(
            sub_question="完整材料",
            findings=[
                verified_finding(statement=f"已核验发现-{i}", source_url=f"https://example.org/{i}")
                for i in range(130)
            ],
        )
    ]
    text, mapping = eligible_material(results)
    assert "已核验发现-129" in text and len(mapping) == 130


def test_prose_unit_coverage_includes_nested_lists_rows_headings_and_code():
    body = (
        "## 结果\n\n- 外层 [1]\n  - 内层 [2]\n\n|项目|结论|\n|---|---|\n"
        "|A|一 [1]|\n|B|二 [2]|\n\n```py\na=[999]\n```\n\n## 参考来源\n[1] https://a.com"
    )
    units, locations = prose_units(body, [1, 2])
    assert len(units) == 7 and len(locations) == 7
    assert any("内层" in u.text and u.citations == [2] for u in units)
    assert any("|A|" in u.text and u.citations == [1] and "表头" in u.context for u in units)
    assert any("|B|" in u.text and u.citations == [2] for u in units)
    assert any("a=[999]" in u.text and not u.citations for u in units)
    assert not any("https://a.com" in u.text for u in units)


def test_abstract_uses_evidence_without_requiring_printed_markers():
    units, _ = prose_units(
        "## 摘要\n\n相关关系。\n\n## 方法\n\n另一结论。", [1], uncited_sections=("摘要",)
    )
    assert units[1].kind == "summary" and units[1].citations == [1]
    assert units[-1].kind == "prose" and units[-1].citations == []


async def test_record_is_bound_to_body_bibliography_evidence_and_complete_coverage():
    checker = reviewer()
    body = "## 结论\n\n变量相关 [1]。\n\n## 参考来源\n[1] https://a.com"
    record = await checker.review(body)
    assert checker.check(body, record) == (True, [])
    assert not checker.check(body.replace("变量相关", "完全相反的结论"), record)[0]
    assert not checker.check(body + "\n另加一段未核验文本", record)[0]
    changed = deepcopy(findings())
    changed[0].findings[0].evidence_quote = "证据已变更"
    assert not reviewer(results=changed).check(body, record)[0]
    broken = deepcopy(record)
    broken["decisions"].pop()
    assert not checker.check(body, broken)[0]


async def test_unchanged_units_are_reused_even_when_an_earlier_paragraph_changes():
    model = Judge()
    checker = reviewer(model)
    body = "## 结论\n\n" + "\n\n".join(f"第 {i} 项相关解释 [1]。" for i in range(20))
    await checker.review(body)
    assert len(model.judged) == 21
    await checker.review(body.replace("第 0 项", "修改第 0 项"))
    assert (
        len(model.judged) == 23
    )  # Changed text also invalidates its successor's reference context.
    await checker.review(body + "\n\n## 参考来源\n[1] https://a.com")
    assert len(model.judged) == 23


def test_pronouns_and_table_rows_keep_their_reference_context():
    text = (
        "## 方法\n\n本文采用方法甲 [1]。\n\n它使用多视图 [1]。\n\n"
        "表 1 配对 t 检验\n\n|量|值|\n|---|---|\n|自由度|11|"
    )
    units, _ = prose_units(text, [1])
    pronoun = next(u for u in units if "它使用" in u.text)
    assert "本文采用方法甲" in pronoun.context
    row = next(u for u in units if "|自由度|" in u.text)
    assert "配对 t 检验" in row.context
    changed, _ = prose_units(text.replace("方法甲", "方法乙"), [1])
    assert next(u.id for u in changed if "它使用" in u.text) != pronoun.id


async def test_qa_repairs_semantically_unsupported_answer_before_delivery(settings):
    from deep_research.workbench.qa import answer_question

    class Answer(Judge):
        async def stream(self, *args, **kwargs):
            self.stream_calls += 1
            yield (
                "变量已经证明因果关系 [1]。" if self.stream_calls == 1 else "变量存在相关关系 [1]。"
            )

    model = Answer()
    ctx = RunContext(llm=model, search_tool=CorrelationSearch(), tracer=Tracer(), settings=settings)
    result = await answer_question("解释这些变量之间的关系", history=[], ctx=ctx, include_web=True)
    assert model.stream_calls == 2 and not result.fallback
    binding = next(t["binding"] for t in result.thoughts if t["tool"] == "citation_binding")
    assert binding["binding_status"] == "bound" and binding["occurrences"]
    assert "#cite-o-" in binding["body"] and "#cite-o-" not in result.answer
    assert "已经证明因果" not in result.answer
    assert (
        next(t for t in result.thoughts if t["tool"] == "claim_check")["review"]["status"] == "pass"
    )


async def test_qa_verifier_failure_does_not_rewrite_or_claim_missing_evidence(
    settings,
):
    from deep_research.workbench.qa import answer_question

    class Unavailable(Judge):
        async def stream(self, *args, **kwargs):
            self.stream_calls += 1
            yield "变量相关 [1]。"

        async def parse(self, system, user, schema, **kwargs):
            if schema is SupportDecisions:
                raise TimeoutError("private provider message")
            return await super().parse(system, user, schema, **kwargs)

    model = Unavailable()
    ctx = RunContext(llm=model, search_tool=CorrelationSearch(), tracer=Tracer(), settings=settings)
    result = await answer_question("解释变量之间关系", history=[], ctx=ctx, include_web=True)
    assert result.fallback and model.stream_calls == 1
    assert "结论核验未完成" in result.answer and "private provider message" not in str(result)
    audit = next(t for t in result.thoughts if t["tool"] == "claim_check")
    assert audit["unapproved_draft"] == "变量相关 [1]。"


async def test_qa_support_review_receives_full_dialogue_for_pronouns(settings):
    from deep_research.workbench.qa import answer_question

    class Capture(Judge):
        async def stream(self, *args, **kwargs):
            yield "变量存在相关关系 [1]。"

        async def parse(self, system, user, schema, **kwargs):
            if schema is SupportDecisions:
                data = json.loads(user)
                assert "第二项关键定义" in data["context"]
                assert "本轮问题：那第二个呢" in data["context"]
            return await super().parse(system, user, schema, **kwargs)

    ctx = RunContext(
        llm=Capture(), search_tool=CorrelationSearch(), tracer=Tracer(), settings=settings
    )
    answer = await answer_question(
        "那第二个呢",
        history=[
            {
                "query": "解释两种方法",
                "answer": "长回答" * 150 + "第二项关键定义",
            }
        ],
        ctx=ctx,
        include_web=True,
    )
    assert not answer.fallback


async def test_writer_preserves_failed_review_for_exact_final_draft(settings):
    from deep_research.workbench.writers import PaperReader

    class Invalid(Judge):
        async def stream(self, *args, **kwargs):
            self.stream_calls += 1
            yield "## 摘要\n\n变量已经证明因果关系。\n\n## 方法\n\n变量已经证明因果关系 [1]。"

    settings.quality = {"max_revisions": 0}
    ctx = RunContext(llm=Invalid(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    bb = await PaperReader().step(Blackboard(query="解释相关关系", results=findings()), ctx)
    audit = bb.scratch["workbench"]["extras"]["prose_review"]
    assert audit["status"] == "fail" and audit["mechanically_finalized"]
    from deep_research.workbench.prose_review import reviewer_for_report

    checker = reviewer_for_report(None, bb.query, bb.results, bb.report.citations, bb.scratch, 0)
    bound, issues = checker.check(bb.report.markdown, audit)
    assert bound and issues  # A missing task contract must not lose the writer's abstract policy.


async def test_peer_review_score_survives_terminal_checks(settings, monkeypatch):
    from deep_research.report.document import FinalReportValidation
    from deep_research.workbench import intake
    from deep_research.workbench.publish import build_bundle
    from deep_research.workbench.templates import get_template
    from tests.test_workbench import _run

    async def fetch_paper(*args, **kwargs):
        return await FakeSearch().search("paper")

    monkeypatch.setattr(intake, "fetch_paper", fetch_paper)

    template = get_template("peerReview")
    body = "\n\n".join(f"## {s.title}\n发现X [1]。" for s in template.sections) + "\n\n评分：7/10\n"
    _, detail, _ = await _run("peerReview", "评审 https://a.com/paper.pdf", body, settings)
    assert "评分：7/10" in detail.report.markdown
    assert "已验证素材摘要" not in detail.report.markdown
    validation = FinalReportValidation.model_validate(
        detail.orchestration.checkpoint["scratch"]["_report_validation"]
    )
    assert validation.support_status == "pass" and validation.semantic_verification
    bundle = build_bundle(detail)
    assert next(g for g in bundle.gates if g.name == "prose_evidence").status == "pass"


async def test_failed_statistical_interpretation_keeps_data_but_blocks_formal_report(settings):
    from deep_research.orchestrator import create_initial_execution
    from deep_research.persistence.repository import RunDetail
    from deep_research.workbench.analysis import DataAnalyst
    from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, build_contract
    from deep_research.workbench.publish import build_bundle
    from deep_research.workbench.templates import get_template

    template = get_template("dataAnalysis")
    body = "\n\n".join(f"## {s.title}\n变量已经证明因果关系。" for s in template.sections)

    class BadStatistics(Judge):
        async def stream(self, *args, **kwargs):
            yield body

    contract = build_contract(
        template, "比较两种测量", attachments_csv="a,b\n1,2\n2,3.1\n3,4.2\n4,5.3"
    )
    settings.quality = {"max_revisions": 0}
    bb = Blackboard(
        query="比较两种测量", scratch={CONTRACT_SCRATCH_KEY: contract.model_dump(mode="json")}
    )
    ctx = RunContext(
        llm=BadStatistics(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings
    )
    await DataAnalyst().step(bb, ctx)
    execution = create_initial_execution(bb.query, template.workflow, settings)
    execution.checkpoint["scratch"] = bb.scratch
    detail = RunDetail(
        id="stats-review", query=bb.query, status="done", report=bb.report, orchestration=execution
    )
    bundle = build_bundle(detail)
    assert next(g for g in bundle.gates if g.name == "prose_evidence").status == "fail"
    assert any(f.format == "xlsx" for f in bundle.files)
    assert not any(f.format in {"pdf", "docx", "html"} for f in bundle.files)


def test_statistics_review_uses_the_writer_ledger_and_distinguishes_input_origin():
    from deep_research.workbench.analysis import analyse, ledger_facts
    from deep_research.workbench.prose_review import reviewer_for_report

    result = analyse("a,b\n1,2\n2,3.1\n3,4.2\n4,5.3", "比较同一场景的配对差异，数据由用户合成")
    frozen = result.snapshot()
    assert frozen["facts"] == result.facts()
    legacy = {key: value for key, value in frozen.items() if key != "facts"}
    assert ledger_facts(legacy) == result.facts()
    checker = reviewer_for_report(
        None,
        "用户提供的合成数据",
        [],
        [],
        {
            "analysis": frozen,
            "workbench": {"template": "dataAnalysis"},
        },
        200000,
    )
    text = checker.evidence[0]["quote"]
    assert "未自动验证这些前提" in text
    assert "用户提供的数据" in text and "不判断用户数据来自真实测量还是合成" in text
    assert '"synthetic": false' not in text


def test_production_legacy_statistics_do_not_invent_missing_metadata_or_fail_loading():
    from deep_research.workbench.analysis import ledger_facts
    from deep_research.workbench.prose_review import reviewer_for_report

    legacy = {
        "rows": 12,
        "columns": ["a", "b"],
        "describe": [],
        "tests": [],
        "correlations": [],
        "synthetic": False,
        "source": {},
        "figures": [],
    }
    text = ledger_facts(legacy)
    assert "未保留的信息不可视为不存在" in text
    assert "缺失值：无" not in text
    checker = reviewer_for_report(
        None,
        "历史分析",
        [],
        [],
        {
            "analysis": legacy,
            "workbench": {"template": "dataAnalysis"},
        },
        0,
    )
    assert checker is not None and "历史统计记录" in checker.evidence[0]["quote"]
