"""Aggregate recorded execution outcomes, keeping unknown usage explicitly unknown."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

SCENARIOS = {
    "autoResearch", "litReview", "peerReview", "paperRead", "dataAnalysis", "slides", "mindmap",
}
GATE_NAMES = {
    "markdown", "structure", "length", "consistency", "file_readability", "node_evidence",
    "prose_evidence", "analysis", "review", "requested_content", "requested_content_evidence",
    "slides", "figures", "math", "citations", "sources", "abstract", "bibliography",
}


def _percentile(values: list[float], percentile: float) -> float | None:
    values = sorted(value for value in values if math.isfinite(value) and value >= 0)
    if not values:
        return None
    return round(values[max(0, math.ceil(percentile * len(values)) - 1)], 3)


def _count(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= 2**53 - 1 else None


def summarize(snapshot: dict[str, Any], *, since: datetime, until: datetime) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    daily: dict[str, Counter[str]] = defaultdict(Counter)
    issues: Counter[str] = Counter()
    run_durations: list[float] = []
    unknown_first_results = 0
    for kind, rows in (("research", snapshot["runs"]), ("qa", snapshot["qa"])):
        for row in rows:
            scenario = row.get("scenario")
            if kind == "qa":
                scenario = "bound_qa" if row.get("run_id") else "qa"
                origin = "continuation" if row.get("parent") or row.get("revision") else (
                    "initial" if row.get("request_id") else "unknown"
                )
            else:
                scenario = (
                    scenario if isinstance(scenario, str) and scenario in SCENARIOS else "other"
                )
                origin = "continuation" if row.get("parent") else (
                    "recovered" if (row.get("attempt") or 1) > 1 else
                    "initial" if row.get("attempt") is not None else "unknown"
                )
                if row["status"] in {"done", "needs_review", "error", "cancelled"}:
                    elapsed = row.get("elapsed")
                    if isinstance(elapsed, (int, float)) and elapsed > 0:
                        run_durations.append(float(elapsed))
            group = groups.setdefault(scenario, {
                "scenario": scenario, "total": 0, "statuses": Counter(),
                "origins": Counter(), "initial_done": 0,
            })
            group["total"] += 1
            group["statuses"][row["status"]] += 1
            group["origins"][origin] += 1
            if kind == "qa":
                if origin == "initial" and row["status"] == "done":
                    group["initial_done"] += 1
            else:
                completion = row.get("completion")
                completion = completion if isinstance(completion, dict) else {}
                first = completion.get("first_result")
                if not (
                    isinstance(first, dict) and first.get("version") == 1
                    and first.get("run_id") == row["id"]
                    and first.get("status") in {"done", "needs_review"}
                ):
                    unknown_first_results += 1
                elif origin == "initial" and first["status"] == "done":
                    group["initial_done"] += 1
            created = row.get("created_at")
            if isinstance(created, datetime):
                if created.tzinfo is None:
                    created = created.replace(tzinfo=UTC)
                daily[created.astimezone(UTC).date().isoformat()][row["status"]] += 1
            if row["status"] == "needs_review":
                completion = row.get("completion")
                completion = completion if isinstance(completion, dict) else {}
                reasons = set()
                for gate in completion.get("gates", []):
                    if isinstance(gate, dict) and (
                        gate.get("status") == "fail" or gate.get("blocking_issues")
                    ):
                        name = gate.get("name")
                        reasons.add(
                            name if isinstance(name, str) and name in GATE_NAMES else "other"
                        )
                issues.update(reasons or {"unspecified"})
    calls: dict[str, dict[str, Any]] = {}
    for call in snapshot["calls"]:
        if not isinstance(call, dict) or not isinstance(call.get("call_id"), str):
            continue
        if not isinstance(call.get("status"), str) or call["status"] not in {
            "started", "succeeded", "failed", "cancelled",
        }:
            continue
        previous = calls.get(call["call_id"])
        if previous and previous["status"] != "started" and call["status"] == "started":
            continue
        calls[call["call_id"]] = call
    usage = {}
    for field in ("input_tokens", "output_tokens", "reasoning_tokens", "total_tokens"):
        values = [
            value for call in calls.values()
            if isinstance(call.get("usage"), dict)
            and (value := _count(call["usage"].get(field))) is not None
        ]
        usage[field] = {
            "known_total": sum(values) if values else None,
            "reported_calls": len(values), "unknown_calls": len(calls) - len(values),
        }
    rendering = snapshot.get("rendering")
    return {
        "as_of": until.isoformat(), "since": since.isoformat(),
        "date_basis": "task_creation_utc", "backend": snapshot["backend"],
        "coverage": {
            "research_records": len(snapshot["runs"]), "qa_records": len(snapshot["qa"]),
            "truncated": snapshot["truncated"], "unknown_date_records": snapshot["unknown_dates"],
            "unknown_research_first_results": unknown_first_results,
        },
        "scenarios": [dict(value) for _, value in sorted(groups.items())],
        "daily": [{"date": day, "statuses": dict(values)} for day, values in sorted(daily.items())],
        "needs_review_reasons": dict(issues),
        "research_duration": {
            "observations": len(run_durations), "p50_seconds": _percentile(run_durations, 0.5),
            "p95_seconds": _percentile(run_durations, 0.95),
        },
        "model_calls": {
            "recorded_attempts": len(calls),
            "statuses": dict(Counter(call["status"] for call in calls.values())),
            "retries": sum(bool(call.get("retry_reason")) for call in calls.values()),
            "usage": usage,
            "duration_observations": sum(type(c.get("duration_ms")) is int for c in calls.values()),
            "p95_seconds": _percentile([
                call["duration_ms"] / 1000 for call in calls.values()
                if type(call.get("duration_ms")) is int and call["duration_ms"] >= 0
            ], 0.95),
            "cost": None, "cost_basis": "no_verified_price_configuration",
        },
        "rendering": None if rendering is None else {
            "records": len(rendering),
            "statuses": dict(Counter(row["status"] for row in rendering)),
            "retried": sum((row.get("attempts") or 0) > 1 for row in rendering),
            "stalled": sum((row.get("stalls") or 0) > 0 for row in rendering),
            "oldest_pending_seconds": max([
                max(0.0, until.timestamp() - row["queued_at"])
                for row in rendering if row["status"] == "pending"
            ], default=None),
        },
        "limitations": [
            "系统完成状态不是人工科研质量结论。",
            "按任务创建时间统计；没有请求记录的历史任务不能视为零调用。",
            "仅汇总提供方已返回的用量字段，缺失值与价格保持未知。",
        ],
    }
