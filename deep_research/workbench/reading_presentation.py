"""Read-only peer/paper annotations, separate from the original reviewed body."""

from __future__ import annotations

from typing import Any

from .contract import contract_from_scratch
from .prose_review import reviewer_for_report, stored_review


def reading_records(detail: Any) -> dict[str, Any]:
    scratch = detail.orchestration.checkpoint.get("scratch", {}) if detail.orchestration else {}
    if detail.report is None:
        return {"review_bound": False, "review_issues": ["尚无正文"], "review": {}}
    from ..report.service import requires_corroboration

    checker = reviewer_for_report(
        None,
        detail.query,
        detail.results,
        detail.report.citations,
        scratch,
        0,
        corroboration=requires_corroboration(detail),
        sources=detail.sources,
    )
    record = stored_review(scratch)
    bound, issues = (
        checker.check(detail.report.markdown, record)
        if checker and record
        else (False, ["当前正文尚无绑定的审核记录"])
    )
    return {"review_bound": bound, "review_issues": issues, "review": record if bound else {}}


def peer_notes(detail: Any) -> str:
    from .reading_map import peer_summary

    records = reading_records(detail)
    review = records["review"]
    coverage = peer_summary(detail, review) or {}
    lines = ["### 评审覆盖与意见记录", ""]
    lines.append("以下为覆盖和审核记录的展示，不是额外生成的论文结论；建议和评分仍需人工判断。")
    if not coverage.get("coverage_bound"):
        lines.extend("- 阅读限制：" + str(issue) for issue in coverage.get("coverage_issues", []))
    else:
        lines.append(
            "- 已核对方法章节：" + "；".join(s["title"] for s in coverage.get("coverage", []))
        )
    if coverage.get("coverage_truncated"):
        lines.append(f"- 本处仅展示部分章节；覆盖记录共 {coverage['coverage_total']} 项。")
    if not records["review_bound"]:
        lines.append("- 当前正文与评审审核记录尚未绑定，不把旧意见或旧评分作为当前已核验结论展示。")
        return "\n".join(lines)
    peer = review.get("peer_review", {})
    if not peer.get("items"):
        lines.append("- 未记录结构化评审条目。")
        return "\n".join(lines)
    score = peer.get("score")
    if score is not None:
        lines.append(
            f"- 记录中的主观评分：{score}/10；记录状态："
            + ("通过工程检查" if peer.get("status") == "pass" else "待复核")
            + "。"
        )
    lines.append("- 可用于核对评分的意见：" + "、".join(peer.get("score_item_ids", [])))
    severity = {"critical": "关键", "general": "一般", "expression": "表达"}
    kinds = {
        "strength": "优点",
        "weakness": "不足",
        "comment": "意见",
        "question": "待澄清问题",
        "suggestion": "建议",
        "recommendation": "总体推荐",
    }
    for item in peer["items"]:
        label = kinds.get(item["type"], item["type"])
        if item.get("severity"):
            label += " / " + severity[item["severity"]]
        lines.extend(
            [
                "",
                f"#### {item['id']} · {label}",
                "",
                item["text"],
                "",
                "分类与影响说明（审核判断）：" + item["reason"],
            ]
        )
        if item.get("evidence_ids"):
            lines.append("依据记录：" + "、".join(item["evidence_ids"]))
        if item.get("action"):
            action = item["action"]
            lines.extend(
                [
                    "- 行动对象（正文原话）：" + action["target_quote"],
                    "- 具体动作（正文原话）：" + action["action_quote"],
                    "- 完成后检查（正文原话）：" + action["completion_quote"],
                ]
            )
        elif item["type"] == "suggestion":
            lines.append("- 尚未记录完整可执行建议，不能仅因存在建议条目就视为已解决。")
    for group in peer.get("groups", []):
        if len(group["item_ids"]) > 1:
            lines.append(
                "\n同一问题候选组（原意见均保留，待人工确认）：" + "、".join(group["item_ids"])
            )
    return "\n".join(lines)


def paper_notes(detail: Any) -> str:
    from .support import evidence_records

    scratch = detail.orchestration.checkpoint.get("scratch", {}) if detail.orchestration else {}
    contract = contract_from_scratch(scratch)
    records = reading_records(detail)
    review = records["review"]
    lines = [
        "### 精读关注点与证据范围",
        "",
        "以下展示本轮关注点及已保存的核验映射，不补写论文事实，也不把历史追问当作原文证据。",
    ]
    decisions = {
        d["requirement_id"]: d for d in review.get("requirements_review", {}).get("decisions", [])
    }
    status = {
        "covered": "已映射",
        "partial": "部分覆盖",
        "missing": "正文尚未落实",
        "insufficient": "材料不足",
        "not_checked": "未确认",
    }
    for item in contract.requested_items if contract else []:
        decision = decisions.get(item.id)
        label = status.get(decision["status"], "未确认") if decision else "未确认"
        lines.append(f"- 关注点 {item.id}：{item.label}；{label}。")
    if not records["review_bound"]:
        lines.append("- 当前正文审核未绑定，不展示已通过的解释—证据映射。")
        return "\n".join(lines)
    material = {
        e["id"]: e
        for e in evidence_records(
            detail.results, {u: i for i, u in enumerate(detail.report.citations, 1)}
        )
    }
    for decision in review.get("decisions", []):
        selected = list(dict.fromkeys(decision.get("evidence_ids", [])))
        if decision.get("verdict") != "supported" or not selected:
            continue
        citations = list(dict.fromkeys(material[e]["citation"] for e in selected if e in material))
        if not citations:
            continue
        lines.append(
            f"- 解释 {decision['unit_id']} 的依据："
            + "".join(f"[{i}]" for i in citations)
            + f"；{len(selected)} 条证据记录，逐条保留各自来源和条件。"
        )
        for eid in selected:
            context = material.get(eid, {}).get("measurement_context")
            if context:
                from ..models import ExperimentConditions, Quantity
                from ..report.assemble import _quantity_label

                if context.get("quantity"):
                    lines.append(
                        "  已保存数值："
                        + _quantity_label(Quantity.model_validate(context["quantity"]))
                    )
                if context.get("conditions"):
                    conditions = ExperimentConditions.model_validate(context["conditions"])
                    lines.append(
                        "  已保存条件（仍须与引文对照）：" + (conditions.describe() or "未记录")
                    )
    lines.append(
        "- 原文文本位置与PDF版面位置分别标记；重复引句须查看周边上下文，"
        "多条依据不能合并成虚构的单一精确定位。"
    )
    return "\n".join(lines)
