"""Evidence-bound checks for optional concept diagram nodes and directed relations."""

from __future__ import annotations

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


def _normalized(figure: ConceptFigure) -> ConceptFigure:
    from .territory import normalize

    return figure.model_copy(
        update={
            "title": normalize(figure.title),
            "caption": normalize(figure.caption),
            "nodes": [
                node.model_copy(
                    update={
                        "id": normalize(node.id),
                        "label": normalize(node.label),
                        "group": normalize(node.group),
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
                    }
                )
                for edge in figure.edges
            ],
        }
    )


def figure_units(figure: ConceptFigure, citations: list[int]) -> list[SupportUnit]:
    figure = _normalized(figure)
    nodes = {node.id: node for node in figure.nodes}
    units = [
        SupportUnit(
            "figure-caption",
            figure.title + "\n" + figure.caption,
            context=_CONTEXT + "\n" + str(figure.model_dump()),
            kind="concept",
            citations=citations,
        )
    ]
    units += [
        SupportUnit(
            "figure-node-" + node.id,
            node.label,
            context=f"图示：{figure.title}；分组：{node.group}。" + _CONTEXT,
            kind="concept",
            citations=citations,
        )
        for node in figure.nodes
    ]
    units += [
        SupportUnit(
            f"figure-edge-{index}",
            f"{nodes[edge.source].label} --{edge.label or '关系'}--> {nodes[edge.target].label}",
            context=_CONTEXT + f"\n图示范围：{figure.title}",
            kind="claim" if edge.label else "concept",
            citations=citations,
        )
        for index, edge in enumerate(figure.edges)
    ]
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
    return digest(
        {
            "version": 1,
            "policy": SUPPORT_POLICY_VERSION,
            "figure": _normalized(figure).model_dump(mode="json"),
            "evidence": evidence,
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
        "version": 1,
        "input_hash": figure_signature(figure, reviewer.evidence),
        "status": "fail" if issues else "pass",
        "issues": issues,
        "can_revise": can_revise(decisions),
        "reviewer": reviewer.provenance,
        "units": [asdict(unit) for unit in units],
        "decisions": [d.model_dump(mode="json") for d in decisions],
    }


def check_figure(figure: ConceptFigure, evidence: list[dict[str, Any]], record: Any) -> list[str]:
    structural = structure_issues(figure)
    if structural:
        return structural
    if not isinstance(record, dict) or record.get("input_hash") != figure_signature(
        figure, evidence
    ):
        return ["缺少与当前图示及证据匹配的关系核验记录"]
    units = figure_units(figure, sorted({e["citation"] for e in evidence}))
    if record.get("units") != [asdict(unit) for unit in units]:
        return ["图示核验未覆盖当前全部节点与关系"]
    try:
        decisions = [SupportDecision.model_validate(d) for d in record.get("decisions", [])]
    except ValueError:
        return ["图示核验记录无法解析"]
    if len(decisions) != len(units) or {d.unit_id for d in decisions} != {u.id for u in units}:
        return ["图示核验决定缺失或重复"]
    known = {e["id"] for e in evidence}
    by_id = {unit.id: unit for unit in units}
    return [
        d.reason or "图示未通过关系核验"
        for d in decisions
        if (
            d.verdict not in {"supported", "non_factual"}
            or (
                d.verdict == "supported"
                and (not d.evidence_ids or not set(d.evidence_ids).issubset(known))
            )
            or (
                d.verdict == "non_factual"
                and (by_id[d.unit_id].kind == "claim" or asserted_comparison(by_id[d.unit_id].text))
            )
        )
    ]
