"""交付剩余问题的唯一分级表：硬门 / 可修订 / 建议。

修订轮次用尽后仍残留的问题分三级：

* 硬门（hard）——引用越界或未知编号、正文数值在证据中无依据、算式错误、统计数值
  绑定不一致、正文/证据/核验规则版本冲突、文件损坏或不可读、权限与地名边界、
  必需验收记录缺失、终稿未经任何核验、正文为空或整篇只剩素材摘要。任务停在
  ``needs_review``，正式文件（HTML/DOCX/PDF）不生成。
* 可修订（revisable）——逐段支持关系判定不支持/存疑、摘录与句子对齐不足、公式核对
  不一致、核验调用失败导致该段未核验、评审结构与覆盖、导图节点证据、修订记录
  中的剩余问题、章节结构缺失等。任务完成（``done``），正式文件照常生成，受影响
  段落原文保留并追加 ``〔待核：原因〕``，全部条目附在完成记录的 ``flagged`` 中。
* 建议（advisory）——文体类代码，沿用 ``gate_classification.STYLE_CODES``。

分级只按检查器给出的问题代码、门名与记录结构判断，不做关键词匹配；
未登记的代码、门与无法解析的记录一律按硬门处理（宁可多停一次，也不放过事实问题）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .gates import GateResult

# 终稿机械复核（report/validation.py）的问题代码。
HARD_VALIDATION_CODES = frozenset(
    {
        "invalid_citation",
        "unsupported_number",
        "invalid_calculation",
        # 未渲染的表格规格是 JSON 原文，不能作为正文保留，也不是可标注的段落。
        "unrendered_table",
        "empty_report",
        "no_eligible_evidence",
    }
)
# 只有这些代码的段落在终稿中原文保留并标注待核；段内数值仍须在已核验素材中找到。
REVISABLE_VALIDATION_CODES = frozenset({"uncited_paragraph"})

# 终稿逐段支持关系核验（prose_review.check_findings）中可修订的代码；
# 其余代码（记录过期、覆盖不全、统计绑定、诊断摘录、待机械修订等）是硬门。
REVISABLE_PROSE_CODES = frozenset(
    {
        "unsupported",  # 模型判定不支持或存疑，含核验调用失败导致的未核验
        "translation_unchecked",
        "misclassified_comparison",
        "misclassified_number",
        "evidence_scope",
        "alignment",
        "record_issue",  # 公式、全文核查与字段缺失说明
        "missing_translation",
        "peer_review",
    }
)

# 导图节点核对（mindmap_contract.checked_review_findings）中可修订的代码。
REVISABLE_NODE_CODES = frozenset(
    {"structure", "node_unsupported", "node_evidence_scope", "node_unverified_fact", "node_record"}
)

# 学术检查中非文体、但也不涉及事实真伪的代码：来源覆盖、时效与局限说明。
REVISABLE_SCHOLARLY_CODES = frozenset(
    {"duplicate-source", "citation-shortfall", "recency-gap", "missing-limitations"}
)

# 整门可修订：结构、评审格式、检索充分性与终稿中保留的未引用段落，均不改变任何已核验事实。
REVISABLE_GATES = frozenset(
    {
        "structure",
        "structured_content",
        "review",
        "research_sufficiency",
        "research_progress",
        "uncited_paragraphs",
    }
)

# 逐条问题记录门：门名 → 可修订代码。
_FINDING_GATES = {
    "prose_evidence": REVISABLE_PROSE_CODES,
    "node_evidence": REVISABLE_NODE_CODES,
    "scholarly": REVISABLE_SCHOLARLY_CODES,
}


def _finding_codes(gate: GateResult) -> dict[str, set[str]] | None:
    """消息 → 代码集合；记录缺失或任一条无法解析时返回 None（整门按硬门）。"""
    findings = gate.metrics.get("findings")
    if not isinstance(findings, list):
        return None
    codes: dict[str, set[str]] = {}
    for item in findings:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("code"), str)
            or not isinstance(item.get("message"), str)
            or not item["message"]
        ):
            return None
        codes.setdefault(item["message"], set()).add(item["code"])
        codes.setdefault("（建议）" + item["message"], set()).add(item["code"])
    return codes


def _record_trusted(gate: GateResult) -> bool:
    """核验记录与当前正文绑定且确实经过模型核验，逐条分级才有意义。"""
    if gate.name == "prose_evidence":
        return (
            gate.metrics.get("review_bound") is True
            and gate.metrics.get("method") != "not_reviewed"
        )
    if gate.name == "node_evidence":
        return gate.metrics.get("review_bound") is True
    return True


def split_blocked(gate: GateResult, blocked: list[str]) -> tuple[list[str], list[str]]:
    """把一道门的非建议问题分成（硬门, 可修订）。"""
    if not blocked:
        return [], []
    if gate.name in _FINDING_GATES:
        codes = _finding_codes(gate)
        if codes is None or not _record_trusted(gate):
            return list(blocked), []
        allowed = _FINDING_GATES[gate.name]
        hard = [m for m in blocked if not codes.get(m) or not codes[m] <= allowed]
        return hard, [m for m in blocked if m not in hard]
    revisable_gate = (
        gate.name in REVISABLE_GATES
        or (gate.name == "figure_evidence" and gate.status == "warn")
        or (gate.name == "review_coverage" and gate.metrics.get("scope") == "peer_review")
    )
    # 没有具体问题文本的失败（只剩兜底说明）无法定位，按硬门处理。
    if revisable_gate and gate.issues:
        return [], list(blocked)
    return list(blocked), []


def split_revision(remaining: list[str], malformed: bool) -> tuple[list[str], list[str]]:
    """修订记录的剩余问题：结构完整时可修订；记录缺失或无法分类时是硬门。"""
    return (list(remaining), []) if malformed else ([], list(remaining))


def hard_issues(gate: GateResult) -> list[str]:
    """不改动门记录，只取出其中的硬门问题（用于决定是否拒绝正式导出）。"""
    blocked = gate.issues or [f"交付检查 {gate.name} 尚未通过"]
    return split_blocked(gate, blocked)[0]


def revisable_code(gate_name: str, code: Any) -> bool:
    allowed = _FINDING_GATES.get(gate_name)
    return allowed is not None and isinstance(code, str) and code in allowed


__all__ = [
    "HARD_VALIDATION_CODES",
    "REVISABLE_GATES",
    "REVISABLE_NODE_CODES",
    "REVISABLE_PROSE_CODES",
    "REVISABLE_SCHOLARLY_CODES",
    "REVISABLE_VALIDATION_CODES",
    "hard_issues",
    "revisable_code",
    "split_blocked",
    "split_revision",
]
