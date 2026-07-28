"""Deterministic benchmark regression gates for CI and release checks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RegressionPolicy:
    min_citation_snapshot_coverage: float = 0.95
    max_unsupported_claim_rate: float = 0.05
    max_conflict_rate: float = 0.10
    max_judge_score_drop: float = 0.25
    max_token_increase_rate: float = 0.25
    require_same_dataset: bool = True


@dataclass(frozen=True)
class RegressionFailure:
    scope: str
    metric: str
    actual: float | str
    expected: float | str
    message: str


@dataclass
class RegressionReport:
    failures: list[RegressionFailure] = field(default_factory=list)
    checks: int = 0

    @property
    def passed(self) -> bool:
        return not self.failures


def load_benchmark(path: Path | str) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("unsupported benchmark schema")
    if not isinstance(data.get("rows"), list):
        raise ValueError("benchmark rows must be a list")
    dataset_hash = data.get("dataset_sha256")
    if not isinstance(dataset_hash, str) or len(dataset_hash) != 64:
        raise ValueError("benchmark dataset_sha256 is missing or invalid")
    expected_hash = data.get("rows_sha256")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise ValueError("benchmark rows_sha256 is missing or invalid")
    encoded_rows = json.dumps(data["rows"], ensure_ascii=False, sort_keys=True)
    actual_hash = hashlib.sha256(encoded_rows.encode("utf-8")).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError("benchmark rows_sha256 mismatch")
    return data


def _as_float(value: object, *, field_name: str) -> float:
    if not isinstance(value, int | float):
        raise ValueError(f"benchmark field {field_name} must be numeric")
    return float(value)


def _row_key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("case_id", "")), str(row.get("workflow", ""))


def _unsupported_rate(metrics: dict[str, Any]) -> float:
    total = _as_float(metrics.get("total_findings", 0), field_name="total_findings")
    supported = _as_float(
        metrics.get("semantically_supported", 0), field_name="semantically_supported"
    )
    return max(0.0, (total - supported) / total) if total else 1.0


def _conflict_rate(metrics: dict[str, Any]) -> float:
    total = _as_float(metrics.get("total_findings", 0), field_name="total_findings")
    conflicted = _as_float(metrics.get("conflicted", 0), field_name="conflicted")
    return conflicted / total if total else 0.0


def evaluate_regression(
    candidate: dict[str, Any],
    baseline: dict[str, Any] | None = None,
    *,
    policy: RegressionPolicy | None = None,
) -> RegressionReport:
    policy = policy or RegressionPolicy()
    report = RegressionReport()
    candidate_rows = {
        _row_key(row): row for row in candidate.get("rows", []) if isinstance(row, dict)
    }

    if baseline is not None and policy.require_same_dataset:
        report.checks += 1
        if (
            candidate.get("dataset") != baseline.get("dataset")
            or candidate.get("dataset_sha256") != baseline.get("dataset_sha256")
        ):
            report.failures.append(
                RegressionFailure(
                    scope="benchmark",
                    metric="dataset",
                    actual=str(candidate.get("dataset")),
                    expected=str(baseline.get("dataset")),
                    message="candidate dataset differs from baseline",
                )
            )

    for key, row in candidate_rows.items():
        scope = f"{key[1]}/{key[0]}"
        metrics = row.get("metrics")
        if not isinstance(metrics, dict):
            report.failures.append(
                RegressionFailure(
                    scope=scope,
                    metric="metrics",
                    actual="missing",
                    expected="present",
                    message="deterministic metrics are required for regression gating",
                )
            )
            continue

        coverage = _as_float(
            metrics.get("cited_source_snapshot_coverage", 0),
            field_name="cited_source_snapshot_coverage",
        )
        unsupported = _unsupported_rate(metrics)
        conflict = _conflict_rate(metrics)
        for metric, actual, expected, relation in (
            (
                "citation_snapshot_coverage",
                coverage,
                policy.min_citation_snapshot_coverage,
                "minimum",
            ),
            (
                "unsupported_claim_rate",
                unsupported,
                policy.max_unsupported_claim_rate,
                "maximum",
            ),
            ("conflict_rate", conflict, policy.max_conflict_rate, "maximum"),
        ):
            report.checks += 1
            failed = actual < expected if relation == "minimum" else actual > expected
            if failed:
                report.failures.append(
                    RegressionFailure(
                        scope=scope,
                        metric=metric,
                        actual=round(actual, 4),
                        expected=round(expected, 4),
                        message=f"{metric} violates configured {relation}",
                    )
                )

    if baseline is None:
        return report

    baseline_rows = {
        _row_key(row): row for row in baseline.get("rows", []) if isinstance(row, dict)
    }
    report.checks += 1
    missing = sorted(set(baseline_rows) - set(candidate_rows))
    if missing:
        report.failures.append(
            RegressionFailure(
                scope="benchmark",
                metric="missing_rows",
                actual=str(missing),
                expected="all baseline rows",
                message="candidate is missing baseline workflow/case cells",
            )
        )

    for key in sorted(set(candidate_rows) & set(baseline_rows)):
        scope = f"{key[1]}/{key[0]}"
        candidate_row = candidate_rows[key]
        baseline_row = baseline_rows[key]
        candidate_score = _as_float(candidate_row.get("score_average"), field_name="score_average")
        baseline_score = _as_float(baseline_row.get("score_average"), field_name="score_average")
        score_drop = baseline_score - candidate_score
        report.checks += 1
        if score_drop > policy.max_judge_score_drop:
            report.failures.append(
                RegressionFailure(
                    scope=scope,
                    metric="judge_score_drop",
                    actual=round(score_drop, 4),
                    expected=policy.max_judge_score_drop,
                    message="judge score dropped beyond tolerance",
                )
            )

        candidate_tokens = _as_float(candidate_row.get("tokens"), field_name="tokens")
        baseline_tokens = _as_float(baseline_row.get("tokens"), field_name="tokens")
        if baseline_tokens == 0:
            token_increase = float("inf") if candidate_tokens > 0 else 0.0
        else:
            token_increase = (candidate_tokens - baseline_tokens) / baseline_tokens
        report.checks += 1
        if token_increase > policy.max_token_increase_rate:
            report.failures.append(
                RegressionFailure(
                    scope=scope,
                    metric="token_increase_rate",
                    actual=(
                        "infinite"
                        if token_increase == float("inf")
                        else round(token_increase, 4)
                    ),
                    expected=policy.max_token_increase_rate,
                    message="token use increased beyond tolerance",
                )
            )
    return report


def format_regression(report: RegressionReport) -> str:
    if report.passed:
        return f"PASS: {report.checks} regression checks passed"
    lines = [f"FAIL: {len(report.failures)} of {report.checks} regression checks failed"]
    lines.extend(
        f"- [{failure.scope}] {failure.metric}: actual={failure.actual}, "
        f"expected={failure.expected} ({failure.message})"
        for failure in report.failures
    )
    return "\n".join(lines)
