"""测试用假实现：让整条研究流程无需真实密钥与网络即可端到端运行。

依赖注入是这里的关键——Orchestrator 接受外部传入的 LLM / 检索后端，
测试只需提供行为可预测的替身。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from deep_research.guardrails import ClaimConsistencyReport, SemanticEvidenceDecisionList
from deep_research.intent.context import ResolvedQuery
from deep_research.intent.slots import SlotExtraction
from deep_research.models import (
    EvidenceVerification,
    ExtractedFindingList,
    Finding,
    Reflection,
    ResearchPlan,
    Source,
    SubQuestion,
)
from deep_research.tools.base import SearchTool
from deep_research.workbench.analysis_scope import AnalysisScope
from deep_research.workbench.paper_evidence import PaperEvidenceSelection
from deep_research.workbench.support import SupportDecisions


def verified_finding(
    statement: str = "发现X",
    source_url: str = "https://a.com",
    evidence_quote: str = "内容A提供了可核验的原文证据",
) -> Finding:
    """Build trusted fixture data for tests that bypass Researcher."""
    return Finding(
        statement=statement,
        source_url=source_url,
        evidence_quote=evidence_quote,
        confidence=0.9,
        verification=EvidenceVerification(
            status="verified",
            method="normalized_quote",
            source_content_hash="fixture-hash",
            reason="test_fixture",
            semantic_status="supported",
            semantic_confidence=0.95,
            semantic_reason="test_fixture",
            claim_id="fixture-claim",
            consistency_status="clear",
        ),
    )


class FakeLLM:
    """按目标 schema 返回预设结构化对象；complete / stream 返回固定文本。"""

    def __init__(self) -> None:
        self.parse_calls = 0
        self.complete_calls = 0
        self.stream_calls = 0

    async def complete(self, system: str, user: str, *, temperature: float = 0.3) -> str:
        self.complete_calls += 1
        return "# 测试报告\n\n## 摘要\n这是综合结论 [1]。\n\n## 分析\n要点一 [1]；要点二 [2]。"

    async def stream(
        self, system: str, user: str, *, temperature: float = 0.4
    ) -> AsyncIterator[str]:
        self.stream_calls += 1
        for piece in ("# 测试报告\n\n", "## 摘要\n这是综合结论 [1]。\n\n", "## 分析\n要点一 [1]。"):
            yield piece

    async def parse(
        self, system: str, user: str, schema, *, temperature: float = 0.2, retries: int = 2
    ):
        self.parse_calls += 1
        if schema.__name__ == "SearchQueryPlan":
            # Default fixtures preserve their deterministic search lookup; query
            # quality and propagation use explicit plans in dedicated tests.
            return schema(search_queries=[user.split("【本轮检索问题】\n", 1)[1]])
        if schema.__name__ == "PeerReviewClassifications":
            data = json.loads(user)
            items = []
            for unit in data["items"]:
                kind = {"strength": "strength", "weakness": "weakness",
                        "recommendation": "recommendation"}.get(unit["role"], "comment")
                severity = "general" if kind == "weakness" else None
                items.append({"unit_id": unit["id"], "kind": kind, "severity": severity,
                              "reason": "fixture classification; not a factual accuracy test"})
            return schema.model_validate({"items": items})
        if schema.__name__ == "PeerReviewComparisons":
            data = json.loads(user)
            return schema.model_validate({"pairs": [
                {"pair_id": pair["id"], "verdict": "consistent", "reason": "fixture consistency"}
                for pair in data["pairs"]
            ]})
        if schema is AnalysisScope:
            data, _ = json.JSONDecoder().raw_decode(user.split("【完整数据列概况】\n", 1)[1])
            identifiers = {
                "id",
                "index",
                "idx",
                "scene",
                "subject",
                "participant",
                "sample",
                "trial",
            }
            measures = [
                c["name"]
                for c in data["columns"]
                if c["dtype"].startswith(("int", "float"))
                and c["name"] not in identifiers
                and not c["name"].endswith("_id")
            ]
            groups = [
                c["name"]
                for c in data["columns"]
                if c["dtype"] == "object"
                and 1 < c["distinct"] <= min(20, max(2, data["rows"] // 2))
                and c["name"] not in identifiers
            ]
            return AnalysisScope(
                measures=measures,
                groups=groups,
                background=[
                    c["name"] for c in data["columns"] if c["name"] not in measures + groups
                ],
            )
        if schema is PaperEvidenceSelection:
            records, _ = json.JSONDecoder().raw_decode(user.split("【已核验论文候选】\n", 1)[1])
            return PaperEvidenceSelection(sufficient=True, finding_ids=[r["id"] for r in records])
        if schema is SupportDecisions:
            data = json.loads(user)
            return SupportDecisions(
                decisions=[
                    {
                        "unit_id": unit["id"],
                        "verdict": "supported"
                        if unit["kind"] == "claim"
                        or (unit["kind"] in {"prose", "summary"} and unit["citations"])
                        else "non_factual",
                        "evidence_ids": [
                            e["id"] for e in data["evidence"] if e["citation"] in unit["citations"]
                        ],
                        "reason": "fixture judgement; not a factual accuracy test",
                    }
                    for unit in data["units"]
                ]
            )
        from deep_research.workbench.coverage_review import CoverageDecisions
        from deep_research.workbench.formula_review import FormulaDecisions
        from deep_research.workbench.fulltext_review import FullTextChecks, FullTextTarget

        if schema is CoverageDecisions:
            data = json.loads(user)
            decisions = []
            for requirement in data["requirements"]:
                location = None
                for region in data["regions"]:
                    if requirement["kind"] in {"section", "branch"}:
                        if (
                            region["kind"] == requirement["kind"]
                            and requirement["label"] in region["title"]
                            and region["text"]
                        ):
                            location = dict(region_id=region["id"], quote=region["text"])
                    elif requirement["kind"] == "table_column":
                        if region["kind"] == "table" and len(region["rows"]) > 1:
                            for column, name in enumerate(region["rows"][0]):
                                if requirement["label"] in name:
                                    location = dict(
                                        region_id=region["id"],
                                        column=column,
                                        quote=region["rows"][1][column],
                                    )
                    elif requirement["table_only"]:
                        if region["kind"] == "table":
                            for row, cells in enumerate(region["rows"][1:], 1):
                                if requirement["label"] in " | ".join(cells):
                                    location = dict(
                                        region_id=region["id"], row=row, quote=" | ".join(cells)
                                    )
                    elif region["kind"] != "section" and region["text"]:
                        location = dict(region_id=region["id"], quote=region["text"])
                    if location:
                        break
                decisions.append(
                    dict(
                        requirement_id=requirement["id"],
                        status="covered" if location else "missing",
                        locations=[location] if location else [],
                        reason="fixture judgement; not a coverage accuracy test",
                    )
                )
            return CoverageDecisions(decisions=decisions)

        if schema is FormulaDecisions:
            from deep_research.workbench.formula_structure import compare_formulas

            data = json.loads(user)
            rows = []
            for formula in data["formulas"]:
                source = next(
                    (
                        source
                        for source in data["sources"]
                        if source["citation"] in formula["citations"]
                    ),
                    None,
                )
                reference = next(
                    (
                        ref
                        for ref in data["reference_formulas"]
                        if source
                        and ref["source_id"] == source["id"]
                        and compare_formulas(formula["tex"], ref["tex"])[0] == "equal"
                    ),
                    None,
                )
                rows.append(
                    dict(
                        formula_id=formula["id"],
                        verdict="matched" if source else "not_source_claim",
                        source_id=source["id"] if source else "",
                        reference_id=reference["id"] if reference else "",
                        source_quote=source["quote"][:600] if source else "",
                        checks={
                            key: "same"
                            for key in (
                                "symbols",
                                "coefficients",
                                "subscripts",
                                "superscripts",
                                "bounds",
                            )
                        },
                        equivalence_explanation="fixture judgement; not a formula accuracy test",
                        reason="fixture judgement; not a formula accuracy test",
                    )
                )
            return FormulaDecisions(decisions=rows)

        if schema is FullTextTarget:
            return FullTextTarget(
                kind="not_applicable",
                document_ids=[],
                keywords=[],
                reason="fixture judgement; not an absence accuracy test",
            )
        if schema is FullTextChecks:
            return FullTextChecks(
                checks=[
                    dict(
                        part_id=part["id"],
                        verdict="not_relevant",
                        quote="",
                        reason="fixture text check",
                    )
                    for part in json.loads(user)["parts"]
                ]
            )
        if schema is ResearchPlan:
            return ResearchPlan(
                interpretation="测试理解",
                sub_questions=[
                    SubQuestion(question="子问题A", rationale="r"),
                    SubQuestion(question="子问题B", rationale="r"),
                ],
            )
        if schema is ExtractedFindingList:
            return ExtractedFindingList(
                findings=[
                    Finding(
                        statement="发现X",
                        source_url="https://a.com",
                        evidence_quote="内容A提供了可核验的原文证据",
                        confidence=0.9,
                    )
                ]
            )
        if schema is Reflection:
            return Reflection(is_sufficient=True, gaps=[], new_sub_questions=[])
        if schema is SemanticEvidenceDecisionList:
            indexes = [
                int(line.removeprefix("Index: "))
                for line in user.splitlines()
                if line.startswith("Index: ")
            ]
            return SemanticEvidenceDecisionList(
                decisions=[
                    {
                        "index": index,
                        "verdict": "supported",
                        "confidence": 0.95,
                        "reason": "fixture",
                    }
                    for index in indexes
                ]
            )
        if schema is ClaimConsistencyReport:
            return ClaimConsistencyReport(contradictions=[])
        if schema is ResolvedQuery:
            # 指代消解：把最近一轮历史里的问题原样当作补全结果。够用来验证
            # 「消解结果是否真的流到了下游」，且不引入随机性。
            history_line = next(
                (
                    line.removeprefix("第1轮用户提问：").split("（")[0]
                    for line in user.splitlines()
                    if line.startswith("第1轮用户提问：")
                ),
                "",
            )
            return ResolvedQuery(
                resolved=history_line or "", needed_context=bool(history_line), reason="fixture"
            )
        if schema is SlotExtraction:
            return SlotExtraction(entities=["实体A", "实体B"])
        raise AssertionError(f"未预设的 schema：{schema}")


class FakeSearch(SearchTool):
    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        return [
            Source(title="A", url="https://a.com", content="内容A提供了可核验的原文证据"),
            Source(title="B", url="https://b.com", content="内容B提供了另一条参考材料"),
        ]
