"""测试用假实现：让整条研究流程无需真实密钥与网络即可端到端运行。

依赖注入是这里的关键——Orchestrator 接受外部传入的 LLM / 检索后端，
测试只需提供行为可预测的替身。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from deep_research.guardrails import ClaimConsistencyReport, SemanticEvidenceDecisionList
from deep_research.models import (
    EvidenceVerification,
    Finding,
    FindingList,
    Reflection,
    ResearchPlan,
    Source,
    SubQuestion,
)
from deep_research.tools.base import SearchTool


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
        if schema is ResearchPlan:
            return ResearchPlan(
                interpretation="测试理解",
                sub_questions=[
                    SubQuestion(question="子问题A", rationale="r"),
                    SubQuestion(question="子问题B", rationale="r"),
                ],
            )
        if schema is FindingList:
            return FindingList(
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
        raise AssertionError(f"未预设的 schema：{schema}")


class FakeSearch(SearchTool):
    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        return [
            Source(title="A", url="https://a.com", content="内容A提供了可核验的原文证据"),
            Source(title="B", url="https://b.com", content="内容B提供了另一条参考材料"),
        ]
