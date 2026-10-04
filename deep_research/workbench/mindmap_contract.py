"""Mindmap node meaning, relationships and content-bound evidence review."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from ..document_corpus import FullTextCorpus, corpus_from_inputs
from ..models import ResearchResult
from .support import (
    SUPPORT_POLICY_VERSION,
    SupportDecision,
    SupportReviewer,
    SupportUnit,
    asserted_comparison,
    digest,
    evidence_records,
)


class MindmapNode(BaseModel):
    label: str = Field(min_length=1, max_length=240)
    kind: Literal["concept", "claim", "question"] = "concept"
    relation: str = Field("包含", max_length=80)
    citations: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list)
    children: list[MindmapNode] = Field(default_factory=list)


class Mindmap(BaseModel):
    root: str = Field(min_length=1, max_length=240)
    branches: list[MindmapNode] = Field(default_factory=list)


def units(mindmap: Mindmap) -> list[SupportUnit]:
    output = [SupportUnit("root", mindmap.root, kind="concept")]

    def walk(node: MindmapNode, path: str, parent: str) -> None:
        output.append(
            SupportUnit(
                path,
                node.label,
                context=f"{parent} —{node.relation}→ {node.label}",
                kind=node.kind,
                citations=node.citations,
            )
        )
        for i, child in enumerate(node.children):
            walk(child, f"{path}.{i}", f"{parent} / {node.label}")

    for i, branch in enumerate(mindmap.branches):
        walk(branch, str(i), mindmap.root)
    return output


def structural_issues(mindmap: Mindmap, citation_count: int) -> list[str]:
    issues = []
    if not mindmap.branches:
        issues.append("导图没有内容分支，请围绕主题组织必要内容")

    def walk(nodes: list[MindmapNode], parent: str) -> None:
        labels = [node.label.strip() for node in nodes]
        if len(set(labels)) != len(labels):
            issues.append(f"「{parent}」下存在重复节点，请合并重复内容")
        for node in nodes:
            if not node.label.strip():
                issues.append("节点标题不能为空白")
            if not node.relation.strip():
                issues.append(f"「{node.label}」缺少与上级的关系")
            if any(index > citation_count for index in node.citations):
                issues.append(f"「{node.label}」使用了不存在的引用编号")
            if node.kind == "claim" and not node.citations:
                issues.append(f"事实节点「{node.label}」需要绑定素材引用")
            walk(node.children, node.label)

    walk(mindmap.branches, mindmap.root)
    return list(dict.fromkeys(issues))


def input_hash(raw: dict, citations: list[str], results: list[ResearchResult]) -> str:
    return digest(
        {
            "version": 1,
            "policy": SUPPORT_POLICY_VERSION,
            "mindmap": Mindmap.model_validate(raw).model_dump(mode="json"),
            "citations": citations,
            "results": [r.material_data() for r in results],
        }
    )


def review_record(
    raw: dict, citations: list[str], results: list[ResearchResult], decisions: list
) -> dict[str, Any]:
    problems = [d for d in decisions if d.verdict in {"unsupported", "uncertain"}]
    return {
        "version": 1,
        "input_hash": input_hash(raw, citations, results),
        "status": "fail" if problems else "pass",
        "decisions": [d.model_dump(mode="json") for d in decisions],
        "issues": [f"节点 {d.unit_id}：{d.reason}" for d in problems],
        "scope": "model_assessed_node_evidence_and_relations",
    }


def checked_review(
    raw: dict,
    citations: list[str],
    results: list[ResearchResult],
    record: Any,
    body: str,
    *,
    corpus: FullTextCorpus | None = None,
    query: str = "",
) -> tuple[bool, list[str]]:
    """Check coverage and binding; bool indicates a current review, not a pass."""
    from .gates import _body_without_references
    from .writers import mindmap_to_markdown

    model = Mindmap.model_validate(raw)
    if _body_without_references(body).strip() != mindmap_to_markdown(model).strip():
        return False, ["导图结构与审核后的大纲不一致，未导出旧结构"]
    if not isinstance(record, dict):
        return False, ["历史导图没有节点证据核对记录，不能确认事实与关系均有支持"]
    if record.get("input_hash") != input_hash(raw, citations, results):
        return False, ["导图或证据已变更，原核对记录不再适用"]
    try:
        decisions = [SupportDecision.model_validate(d) for d in record.get("decisions", [])]
    except ValueError:
        return False, ["导图节点核对记录无法解析"]
    from .mindmap_edit import review_units

    expected = {u.id: u for u in (review_units(model, query) if query else units(model))}
    if {d.unit_id for d in decisions} != set(expected) or len(decisions) != len(expected):
        return False, ["导图的节点核对记录不完整或重复"]
    evidence = evidence_records(results, {url: i for i, url in enumerate(citations, 1)})
    checker = SupportReviewer(
        None,
        evidence,
        0,
        fulltext_corpus=corpus
        or corpus_from_inputs(
            results,
            {url: i for i, url in enumerate(citations, 1)},
        ),
    )
    problems = structural_issues(model, len(citations))
    for decision in decisions:
        unit = expected[decision.unit_id]
        allowed = {e["id"] for e in evidence if e["citation"] in unit.citations}
        fulltext_only = not decision.evidence_ids and checker.fulltext_supports(unit, decision)
        if decision.verdict in {"unsupported", "uncertain"}:
            problems.append(f"节点 {unit.id}：{decision.reason}")
        elif decision.verdict == "supported" and (
            (not decision.evidence_ids and not fulltext_only)
            or not set(decision.evidence_ids).issubset(allowed)
        ):
            problems.append(f"节点 {unit.id}：证据映射不属于该节点")
        elif decision.verdict == "non_factual" and (
            unit.kind == "claim" or asserted_comparison(unit.text)
        ):
            problems.append(f"节点 {unit.id}：事实节点不能免于证据核对")
        if decision.verdict in {"supported", "non_factual"}:
            if issue := checker.record_issue(unit, decision):
                problems.append(f"节点 {unit.id}：{issue}")
    return True, problems
