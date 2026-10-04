"""Evidence-bound checks for optional concept diagram nodes and directed relations."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from typing import Any

from .figures import ConceptFigure
from .support import (
    SUPPORT_POLICY_VERSION,
    SupportDecision,
    SupportReviewer,
    SupportUnit,
    asserted_comparison,
    digest,
)

FIGURE_REVIEW_KEY = "figure_review"
_CONTEXT = (
    "核对论文图示，context 中的图示是待检查对象，不是证据。"
    "数据流箭头从源向目标传递数据或约束；比较关系 A --优于--> B 明确表示 A 优于 B，不能反向理解。"
    "原文说‘除 X 外均优于’并不推出‘X 未优于’。节点分组和图注也不能引入原文没有支持的结论。"
)
FIGURE_RULES = _CONTEXT
SCOPED_FIGURE_RULES = _CONTEXT + (
    "本图使用逐单元引用，不能从其他节点的证据给当前关系背书。"
    "核对完整的节点文字与所属分组：广义深度学习模型的实验结果不能直接套到深度展开子类。"
    "事实、数值、机制和方向关系需由该单元指定的证据直接支持；"
    "concept/question 只允许纯主题、编排或不预设结论的问题，不能借类型免除事实核验。"
)


def uses_bindings(figure: ConceptFigure) -> bool:
    return figure.evidence_mode == "scoped" or bool(
        figure.citations
        or any(node.citations for node in figure.nodes)
        or any(edge.citations for edge in figure.edges)
    )


def figure_payload(figure: ConceptFigure) -> dict[str, Any]:
    """Keep the original v1 representation for previously reviewed diagrams."""
    value = figure.model_dump(mode="json")
    if not uses_bindings(figure):
        value.pop("evidence_mode")
        value.pop("citations")
        for item in [*value["nodes"], *value["edges"]]:
            item.pop("kind")
            item.pop("citations")
    return value


def _normalized(figure: ConceptFigure) -> ConceptFigure:
    from .territory import normalize

    return figure.model_copy(
        update={
            "title": normalize(figure.title),
            "caption": normalize(figure.caption),
            "citations": sorted(set(figure.citations)),
            "nodes": [
                node.model_copy(
                    update={
                        "id": normalize(node.id),
                        "label": normalize(node.label),
                        "group": normalize(node.group),
                        "citations": sorted(set(node.citations)),
                    }
                )
                for node in figure.nodes
            ],
            "edges": [
                edge.model_copy(
                    update={
                        "source": normalize(edge.source),
                        "target": normalize(edge.target),
                        "label": normalize(edge.label),
                        "citations": sorted(set(edge.citations)),
                    }
                )
                for edge in figure.edges
            ],
        }
    )


def figure_units(figure: ConceptFigure, citations: list[int]) -> list[SupportUnit]:
    figure = _normalized(figure)
    scoped = uses_bindings(figure)
    context = SCOPED_FIGURE_RULES if scoped else _CONTEXT
    nodes = {node.id: node for node in figure.nodes}
    units = [
        SupportUnit(
            "figure-caption",
            figure.title + "\n" + figure.caption,
            context=context + "\n" + str(figure_payload(figure)),
            kind="concept",
            citations=sorted(set(figure.citations)) if scoped else citations,
        )
    ]
    units += [
        SupportUnit(
            "figure-node-" + node.id,
            node.label + (f"\n所属分组：{node.group}" if scoped and node.group else ""),
            context=f"图示：{figure.title}；分组：{node.group}。" + context,
            kind=node.kind if scoped else "concept",
            citations=sorted(set(node.citations)) if scoped else citations,
        )
        for node in figure.nodes
    ]
    occurrences: Counter[str] = Counter()
    for index, edge in enumerate(figure.edges):
        identifier = f"figure-edge-{index}"
        if scoped:
            key = digest([edge.source, edge.target, edge.label, sorted(set(edge.citations))])[:20]
            identifier = f"figure-edge-{key}-{occurrences[key]}"
            occurrences[key] += 1
        labels = [nodes[edge.source].label, nodes[edge.target].label]
        if scoped:
            labels = [
                label + (f"（所属分组：{nodes[node_id].group}）" if nodes[node_id].group else "")
                for label, node_id in zip(labels, (edge.source, edge.target), strict=True)
            ]
        units.append(
            SupportUnit(
                identifier,
                f"{labels[0]} --{edge.label or '关系'}--> {labels[1]}",
                context=context + f"\n图示范围：{figure.title}",
                kind=edge.kind if scoped else "claim" if edge.label else "concept",
                citations=sorted(set(edge.citations)) if scoped else citations,
            )
        )
    return units


def structure_issues(figure: ConceptFigure) -> list[str]:
    figure = _normalized(figure)
    ids = [node.id for node in figure.nodes]
    if (
        not ids
        or len(ids) != len(set(ids))
        or any(not node.id.strip() or not node.label.strip() for node in figure.nodes)
    ):
        return ["图示节点为空或存在重复标识"]
    if any(
        edge.source not in ids or edge.target not in ids or edge.source == edge.target
        for edge in figure.edges
    ):
        return ["图示关系引用未知节点或未支持的自环"]
    return []


def figure_signature(figure: ConceptFigure, evidence: list[dict[str, Any]]) -> str:
    scoped = uses_bindings(figure)
    selected = (
        {n for unit in figure_units(figure, []) for n in unit.citations}
        if scoped and not structure_issues(figure)
        else None
    )
    return digest(
        {
            "version": 2 if scoped else 1,
            "policy": SUPPORT_POLICY_VERSION,
            "figure": figure_payload(_normalized(figure)),
            "evidence": [e for e in evidence if e["citation"] in selected]
            if selected is not None
            else evidence,
        }
    )


async def review_figure(figure: ConceptFigure, reviewer: SupportReviewer) -> dict[str, Any]:
    from .prose_review import can_revise

    issues = structure_issues(figure)
    decisions = []
    units = []
    if not issues:
        units = figure_units(figure, sorted({e["citation"] for e in reviewer.evidence}))
        decisions = await reviewer.review(units)
        issues = [d.reason for d in decisions if d.verdict not in {"supported", "non_factual"}]
    return {
        "version": 2 if uses_bindings(figure) else 1,
        "input_hash": figure_signature(figure, reviewer.evidence),
        "status": "fail" if issues else "pass",
        "issues": issues,
        "can_revise": can_revise(decisions),
        "reviewer": reviewer.provenance,
        "units": [asdict(unit) for unit in units],
        "decisions": [d.model_dump(mode="json") for d in decisions],
    }


def check_figure(
    figure: ConceptFigure,
    evidence: list[dict[str, Any]],
    record: Any,
    *,
    corpus: Any = None,
) -> list[str]:
    structural = structure_issues(figure)
    if structural:
        return structural
    if not isinstance(record, dict) or record.get("input_hash") != figure_signature(
        figure, evidence
    ):
        return ["缺少与当前图示及证据匹配的关系核验记录"]
    units = figure_units(figure, sorted({e["citation"] for e in evidence}))
    available = {e["citation"] for e in evidence}
    if any(not set(unit.citations).issubset(available) for unit in units):
        return ["图示包含不存在或未通过准入的引用编号"]
    if record.get("units") != [asdict(unit) for unit in units]:
        return ["图示核验未覆盖当前全部节点与关系"]
    try:
        decisions = [SupportDecision.model_validate(d) for d in record.get("decisions", [])]
    except ValueError:
        return ["图示核验记录无法解析"]
    if len(decisions) != len(units) or {d.unit_id for d in decisions} != {u.id for u in units}:
        return ["图示核验决定缺失或重复"]
    by_id = {unit.id: unit for unit in units}
    checker = SupportReviewer(None, evidence, 0, fulltext_corpus=corpus)
    return [
        d.reason or "图示未通过关系核验"
        for d in decisions
        if (
            d.verdict not in {"supported", "non_factual"}
            or (
                d.verdict == "supported"
                and (
                    (not d.evidence_ids and not checker.fulltext_supports(by_id[d.unit_id], d))
                    or not set(d.evidence_ids).issubset(
                        {e["id"] for e in evidence if e["citation"] in by_id[d.unit_id].citations}
                    )
                )
            )
            or (
                d.verdict == "non_factual"
                and (by_id[d.unit_id].kind == "claim" or asserted_comparison(by_id[d.unit_id].text))
            )
            or checker.fulltext_issue(by_id[d.unit_id], d)
        )
    ]
