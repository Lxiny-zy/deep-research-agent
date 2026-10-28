"""User-facing workflow state derived from durable research checkpoints.

The execution engine still exposes low-level agents and gates for recovery and
auditing. This module provides the stable product-level view above them: a
small set of phases, actionable issue classes, and one next action. It is
derived rather than stored as a second source of truth, so old runs remain
readable while new UI flows can stop depending on agent names.
"""

from __future__ import annotations

from typing import Any, Literal

PhaseStatus = Literal["pending", "active", "done", "needs_review", "blocked", "skipped"]
ActionKind = Literal["retrieve", "read", "rewrite", "render", "security", "review", "none"]

PHASES: tuple[tuple[str, str], ...] = (
    ("intake", "任务理解"),
    ("evidence", "证据准备"),
    ("coverage", "要求覆盖"),
    ("draft", "正文生成"),
    ("review", "内容核验"),
    ("repair", "定向修订"),
    ("render", "格式生成"),
    ("acceptance", "交付验收"),
)

_SECURITY_TERMS = ("越界", "无依据数值", "错误计算", "快照", "损坏", "权限", "注入")
_RETRIEVE_TERMS = ("检索", "来源", "证据不足", "覆盖不足", "材料不足", "补读")
_RENDER_TERMS = ("格式", "DOCX", "PDF", "HTML", "文件", "一致性")


def _gate(gates: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((gate for gate in gates if gate.get("name") == name), None)


def _messages(gates: list[dict[str, Any]]) -> list[str]:
    output: list[str] = []
    for gate in gates:
        for key in ("blocking_issues", "issues", "advisories"):
            values = gate.get(key)
            if isinstance(values, list):
                output.extend(str(value) for value in values if isinstance(value, str) and value)
    return list(dict.fromkeys(output))


def classify_issue(message: str) -> ActionKind:
    if any(term in message for term in _SECURITY_TERMS):
        return "security"
    if any(term in message for term in _RENDER_TERMS):
        return "render"
    if "全文" in message or "原文" in message:
        return "read"
    if any(term in message for term in _RETRIEVE_TERMS):
        return "retrieve"
    if "支持关系" in message or "覆盖" in message:
        return "review"
    if message:
        return "rewrite"
    return "none"


def _phase(
    phase_id: str,
    label: str,
    status: PhaseStatus,
    *,
    detail: str = "",
    issues: list[str] | None = None,
    action: ActionKind = "none",
) -> dict[str, Any]:
    return {
        "id": phase_id,
        "label": label,
        "status": status,
        "detail": detail,
        "issues": list(dict.fromkeys(issues or [])),
        "action": action,
    }


def derive_workflow_state(
    detail: Any,
    bundle: Any | None = None,
    completion_issues: list[str] | None = None,
) -> dict[str, Any]:
    """Build a stable phase view from a run detail and its latest delivery."""
    scratch = (
        detail.orchestration.checkpoint.get("scratch", {})
        if getattr(detail, "orchestration", None)
        else {}
    )
    workbench = scratch.get("workbench", {}) if isinstance(scratch, dict) else {}
    extras = workbench.get("extras", {}) if isinstance(workbench, dict) else {}
    gates = [gate.to_dict() for gate in bundle.gates] if bundle is not None else []
    all_issues = list(dict.fromkeys([*(completion_issues or []), *_messages(gates)]))
    by_action: dict[ActionKind, list[str]] = {
        "retrieve": [], "read": [], "rewrite": [], "render": [],
        "security": [], "review": [], "none": [],
    }
    for issue in all_issues:
        by_action[classify_issue(issue)].append(issue)

    results = getattr(detail, "results", []) or []
    report = getattr(detail, "report", None)
    has_report = report is not None and bool(getattr(report, "markdown", "").strip())
    revision = extras.get("revision", {}) if isinstance(extras, dict) else {}
    revision_remaining = revision.get("remaining", []) if isinstance(revision, dict) else []
    if not isinstance(revision_remaining, list):
        revision_remaining = []

    phases: list[dict[str, Any]] = []
    phases.append(_phase(
        "intake", "任务理解", "done" if workbench or getattr(detail, "query", "") else "active",
        detail="已建立任务契约" if workbench else "正在解析任务要求",
    ))
    evidence_gate = _gate(gates, "source_processing")
    evidence_status: PhaseStatus = "done" if results else "active"
    if by_action["retrieve"] or by_action["read"]:
        evidence_status = "needs_review"
    if evidence_gate and evidence_gate.get("status") == "fail":
        evidence_status = "blocked"
    phases.append(_phase(
        "evidence", "证据准备", evidence_status,
        detail=f"已形成 {len(results)} 组研究结果" if results else "尚未形成可用研究结果",
        issues=by_action["retrieve"] + by_action["read"],
        action="retrieve" if by_action["retrieve"] else ("read" if by_action["read"] else "none"),
    ))
    coverage_gate = next((g for g in gates if g.get("name") in {
        "review_coverage", "provided_corpus", "user_requirements", "task_content",
    } and g.get("status") in {"fail", "warn"}), None)
    coverage_status: PhaseStatus = "done" if not coverage_gate else "needs_review"
    phases.append(_phase(
        "coverage", "要求覆盖", coverage_status,
        detail="用户要求与证据范围已检查" if not coverage_gate else "仍有要求或证据范围需要处理",
        issues=by_action["retrieve"] + by_action["review"],
        action="retrieve" if by_action["retrieve"] else ("review" if coverage_gate else "none"),
    ))
    phases.append(_phase(
        "draft", "正文生成", "done" if has_report else "active",
        detail="已生成正文" if has_report else "正在生成正文",
        issues=by_action["rewrite"], action="rewrite" if not has_report else "none",
    ))
    review_gate = next((g for g in gates if g.get("name") in {
        "prose_evidence", "node_evidence", "review", "scholarly",
    }), None)
    review_status: PhaseStatus = "pending" if not has_report else "done"
    if review_gate and review_gate.get("status") in {"fail", "warn"}:
        review_status = "needs_review"
    phases.append(_phase(
        "review", "内容核验", review_status,
        detail="正文已完成支持关系检查" if review_status == "done" else "正文存在待处理核验项",
        issues=by_action["review"] + by_action["security"],
        action=(
            "security" if by_action["security"]
            else ("review" if review_status == "needs_review" else "none")
        ),
    ))
    repair_status: PhaseStatus = "pending"
    if revision_remaining:
        repair_status = "needs_review"
    elif has_report and review_status == "done":
        repair_status = "skipped"
    phases.append(_phase(
        "repair", "定向修订", repair_status,
        detail=(
            f"剩余 {len(revision_remaining)} 项待修订"
            if revision_remaining else "当前无待执行修订"
        ),
        issues=[str(item) for item in revision_remaining if isinstance(item, str)],
        action="rewrite" if revision_remaining else "none",
    ))
    render_gate = next(
        (g for g in gates if g.get("name") in {"render", "file_readability", "consistency"}),
        None,
    )
    render_status: PhaseStatus = "pending" if bundle is None else "done"
    if render_gate and render_gate.get("status") == "fail":
        render_status = "needs_review"
    phases.append(_phase(
        "render", "格式生成", render_status,
        detail="交付格式已生成" if render_status == "done" else "格式生成或一致性检查仍有问题",
        issues=by_action["render"], action="render" if by_action["render"] else "none",
    ))
    acceptance_status: PhaseStatus = (
        "active" if bundle is None else ("done" if not completion_issues else "needs_review")
    )
    phases.append(_phase(
        "acceptance", "交付验收", acceptance_status,
        detail="交付已通过验收" if acceptance_status == "done" else "交付仍有待处理问题",
        issues=all_issues, action=classify_issue(all_issues[0]) if all_issues else "none",
    ))
    # 交付尚未生成时，研究仍在进行：阶段必须按顺序推进。第一个进行中的阶段之后，
    # 后续阶段一律视为未开始——否则「要求覆盖」会因为还没有质量门而显示已完成，
    # 「正文生成」「交付验收」也会与「证据准备」同时显示进行中。
    if bundle is None:
        first_open = next(
            (index for index, phase in enumerate(phases) if phase["status"] == "active"), None
        )
        if first_open is not None:
            for phase in phases[first_open + 1:]:
                if phase["status"] in {"active", "done", "skipped"}:
                    phase["status"] = "pending"
    current = next(
        (phase for phase in phases if phase["status"] in {"active", "needs_review", "blocked"}),
        phases[-1],
    )
    next_action = current["action"]
    if next_action == "none" and current["status"] == "done":
        next_action = "none"
    return {
        "version": 1,
        "current_phase": current["id"],
        "current_label": current["label"],
        "next_action": next_action,
        "phases": phases,
        "issue_counts": {kind: len(items) for kind, items in by_action.items() if items},
        "issue_total": len(all_issues),
    }
