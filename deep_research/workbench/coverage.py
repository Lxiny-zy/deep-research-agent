"""证据覆盖目标：把交付质量要求翻译成检索阶段能直接行动的缺口。

写作返工只能修正「怎么写」，修不了「证据不够」：可用来源少于综述的引用下限、
任务点名的年份没有任何文献、合格发现太少——这些只能靠继续检索解决。反思环节
原本只凭模型判断「证据是否充分」，模型很容易在只有 5 篇文献时就宣布充分。

``coverage_gaps`` 用确定性规则计算当前证据离交付要求还差什么；反思循环据此
（a）把缺口写进反思提示词，（b）在还有补洞轮数时推翻「已充分」的判断。
规则只读黑板上的数据，不调用模型。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..bibliography import build_bibliography, work_keys
from ..guardrails import report_eligible
from ..models import ResearchResult
from .contract import contract_from_scratch
from .quality import coerce_policy
from .scholarly import requested_year, source_counts
from .templates import get_template

_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


@dataclass
class CoverageGaps:
    gaps: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    metrics: dict[str, int] = field(default_factory=dict)

    @property
    def open(self) -> bool:
        return bool(self.gaps)


def _eligible(results: list[ResearchResult], corroboration: bool) -> list[Any]:
    return [
        finding
        for result in results
        for finding in result.findings
        if report_eligible(finding, require_corroboration=corroboration)
    ]


def coverage_gaps(
    scratch: dict[str, Any],
    results: list[ResearchResult],
    query: str,
    *,
    settings_quality: dict[str, Any] | None = None,
    require_corroboration: bool = False,
) -> CoverageGaps:
    """当前证据相对任务契约的交付要求还缺什么。无契约的普通研究只看证据数量。"""
    contract = contract_from_scratch(scratch)
    policy = coerce_policy(contract.quality if contract is not None else settings_quality)
    template = get_template(contract.template) if contract is not None else None
    findings = _eligible(results, require_corroboration)
    sources = list(dict.fromkeys(finding.source_url for finding in findings))
    document_keys = work_keys(build_bibliography("", sources, findings))
    source_count = source_counts(sources, document_keys=document_keys)[0]
    gaps: list[str] = []
    suggestions: list[str] = []
    minimum = 0
    if contract is not None:
        minimum = contract.min_citations
    elif template is not None:
        minimum = policy.min_citations_for(template.key, template.min_citations)
    if minimum and source_count < minimum:
        gaps.append(f"已核验的不同文献只有 {source_count} 篇，交付要求至少 {minimum} 篇")
        suggestions.append(
            "从尚未覆盖的方向（代表方法、对比基线、数据集与评测、应用场景、近期进展）"
            "分别提出更具体的子问题，每个子问题指向一批不同的文献"
        )
    if policy.min_evidence_findings and len(findings) < policy.min_evidence_findings:
        gaps.append(
            f"通过逐字核验的发现只有 {len(findings)} 条，少于 {policy.min_evidence_findings} 条"
        )
    year = requested_year(contract.original_request if contract is not None else query)
    if policy.recency_check and year is not None:
        texts = {
            f.source_url: " ".join(
                filter(
                    None,
                    (f.verification.source_reference, f.verification.source_title, f.source_url),
                )
            )
            for f in findings
        }
        recent = sum(
            1 for text in texts.values() if any(int(y) >= year for y in _YEAR.findall(text))
        )
        if recent == 0:
            gaps.append(f"任务要求 {year} 年及之后的文献，当前证据中没有该时段的来源")
            suggestions.append(f"子问题中明确写出「{year} 年以来」「最新」等时间限定")
    return CoverageGaps(
        gaps=gaps,
        suggestions=suggestions,
        metrics={
            "findings": len(findings),
            "sources": source_count,
            "source_locations": len(sources),
            "required_sources": minimum,
        },
    )


def gap_prompt(gaps: CoverageGaps) -> str:
    """附在反思提示词后的确定性缺口说明。"""
    if not gaps.open:
        return ""
    lines = ["", "", "交付要求检查（程序计算，必须据此判断是否充分）："]
    lines += [f"- 未满足：{gap}" for gap in gaps.gaps]
    lines += [f"- 建议：{item}" for item in gaps.suggestions]
    lines.append("存在未满足项时不得标记 is_sufficient=true，请提出能补足这些缺口的新子问题。")
    return "\n".join(lines)


__all__ = ["CoverageGaps", "coverage_gaps", "gap_prompt"]
