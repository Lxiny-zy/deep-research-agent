"""Mindmap node meaning, relationships and content-bound evidence review."""

from __future__ import annotations

import re
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

RELATIONS = ("包含", "导致", "依赖", "对比", "改进", "前提", "应用于")
MINDMAP_POLICY_VERSION = 3
FACTUAL_RELATIONS = frozenset(RELATIONS) - {"包含", "对比"}
MAX_CROSS_LINKS = 8


class MindmapNode(BaseModel):
    label: str = Field(min_length=1, max_length=240)
    kind: Literal["concept", "claim", "question"] = "concept"
    # Keep legacy values readable so they can receive a review issue rather than a 500.
    relation: str = Field("包含", max_length=80, json_schema_extra={"enum": list(RELATIONS)})
    citations: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list)
    children: list[MindmapNode] = Field(default_factory=list)


class MindmapLink(BaseModel):
    source: str = Field(min_length=1, max_length=120, description="起点节点路径，如 0 或 1.2")
    target: str = Field(min_length=1, max_length=120, description="终点节点路径，如 1 或 0.2")
    relation: str = Field(max_length=80, json_schema_extra={"enum": list(RELATIONS)})
    citations: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list)


class Mindmap(BaseModel):
    root: str = Field(min_length=1, max_length=240)
    branches: list[MindmapNode] = Field(default_factory=list)
    links: list[MindmapLink] = Field(
        default_factory=list,
        json_schema_extra={"maxItems": MAX_CROSS_LINKS},
    )


def node_index(mindmap: Mindmap) -> dict[str, MindmapNode]:
    found: dict[str, MindmapNode] = {}

    def walk(nodes: list[MindmapNode], prefix: str = "") -> None:
        for i, node in enumerate(nodes):
            key = f"{prefix}.{i}" if prefix else str(i)
            found[key] = node
            walk(node.children, key)

    walk(mindmap.branches)
    return found


def composition(mindmap: Mindmap) -> tuple[dict[str, Any], list[str]]:
    nodes = list(node_index(mindmap).values())
    claims = sum(node.kind == "claim" for node in nodes)
    ratio = claims / len(nodes) if nodes else 0.0
    advice = []
    if len(nodes) >= 10 and ratio > 0.7:
        advice.append(
            f"结论节点 {claims}/{len(nodes)}（{ratio:.0%}），建议用主题与关系组织必要结论，"
            "避免逐条堆放摘录；保留关键事实与限定，不为降低比例改标节点类型"
        )
    return {
        "nodes": len(nodes),
        "claims": claims,
        "claim_ratio": ratio,
        "cross_links": len(mindmap.links),
    }, advice


def units(mindmap: Mindmap) -> list[SupportUnit]:
    output = [SupportUnit("root", mindmap.root, kind="concept")]
    nodes = node_index(mindmap)

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
        if node.relation != "包含":
            source = nodes[path.rsplit(".", 1)[0]].label if "." in path else mindmap.root
            output.append(
                SupportUnit(
                    "relation:" + path,
                    f"{source} —{node.relation}→ {node.label}",
                    context="有方向的父子关系，来源须支持这条关系本身，不能仅分别提及两个节点。",
                    kind="claim" if node.relation in FACTUAL_RELATIONS else "concept",
                    citations=node.citations,
                )
            )
        for i, child in enumerate(node.children):
            walk(child, f"{path}.{i}", f"{parent} / {node.label}")

    for i, branch in enumerate(mindmap.branches):
        walk(branch, str(i), mindmap.root)
    for i, link in enumerate(mindmap.links):
        source = nodes[link.source].label if link.source in nodes else link.source
        target = nodes[link.target].label if link.target in nodes else link.target
        output.append(
            SupportUnit(
                f"link:{i}",
                f"{source} —{link.relation}→ {target}",
                context=(
                    f"跨分支关联 {link.source} → {link.target}；按起点到终点核对关系和引用。"
                    "支持两个端点不等于支持因果、改进或依赖关系。"
                ),
                kind="claim" if link.relation in FACTUAL_RELATIONS else "concept",
                citations=link.citations,
            )
        )
    return output


def structural_issues(mindmap: Mindmap, citation_count: int) -> list[str]:
    from .mindmap_duplicates import duplicate_nodes

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
            elif node.relation not in RELATIONS:
                issues.append(f"「{node.label}」关系不在固定关系词表中：{node.relation}")
            if node.relation in FACTUAL_RELATIONS and not node.citations:
                issues.append(f"「{node.label}」的{node.relation}关系需要绑定支持该关系的引用")
            if any(index > citation_count for index in node.citations):
                issues.append(f"「{node.label}」使用了不存在的引用编号")
            if node.kind == "claim" and not node.citations:
                issues.append(f"事实节点「{node.label}」需要绑定素材引用")
            walk(node.children, node.label)

    walk(mindmap.branches, mindmap.root)
    nodes = node_index(mindmap)
    if len(mindmap.links) > MAX_CROSS_LINKS:
        issues.append(f"跨分支关联超过 {MAX_CROSS_LINKS} 条，请只保留理解主题必需的关联")
    seen = set()
    for i, link in enumerate(mindmap.links):
        prefix = f"跨分支关联 {i + 1}"
        if any(
            not re.fullmatch(r"\d+(?:\.\d+)*", key) or key not in nodes
            for key in (link.source, link.target)
        ):
            issues.append(prefix + "引用了不存在的节点路径")
        if link.source.split(".")[0] == link.target.split(".")[0]:
            issues.append(prefix + "必须连接不同分支，不能连接自身或重复父子层级")
        if link.relation not in RELATIONS:
            issues.append(prefix + "不在固定关系词表中")
        key = (
            tuple(sorted((link.source, link.target)))
            if link.relation == "对比"
            else (
                link.source,
                link.target,
            )
        )
        if (key, link.relation) in seen:
            issues.append(prefix + "重复，保留一条并合并其引用")
        seen.add((key, link.relation))
        if link.relation in FACTUAL_RELATIONS and not link.citations:
            issues.append(prefix + "的事实关系缺少引用")
        if any(c > citation_count for c in link.citations):
            issues.append(prefix + "使用了不存在的引用编号")
    for duplicate in duplicate_nodes(mindmap):
        qualifier = "" if duplicate["exact"] else "疑似"
        issues.append(
            f"跨分支{qualifier}重复节点 {duplicate['left']} / {duplicate['right']}："
            f"「{duplicate['labels'][0]}」与「{duplicate['labels'][1]}」；"
            "核对后合并重复内容或明确各自条件，保留原有事实、限定与引用"
        )
    return list(dict.fromkeys(issues))


def input_hash(raw: dict, citations: list[str], results: list[ResearchResult]) -> str:
    return digest(
        {
            "version": MINDMAP_POLICY_VERSION,
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
    structural = structural_issues(model, len(citations))
    if not isinstance(record, dict):
        return False, [*structural, "历史导图没有节点证据核对记录，不能确认事实与关系均有支持"]
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
    problems = structural
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
