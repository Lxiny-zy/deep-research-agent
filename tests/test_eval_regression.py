from __future__ import annotations

import hashlib
import json

import pytest

from eval.regression import (
    RegressionPolicy,
    evaluate_regression,
    format_regression,
    load_benchmark,
)


def _row(
    *,
    score: float = 4.0,
    tokens: int = 100,
    coverage: float = 1.0,
    total: int = 10,
    supported: int = 10,
    conflicted: int = 0,
) -> dict:
    return {
        "case_id": "case-1",
        "workflow": "deep",
        "score_average": score,
        "tokens": tokens,
        "metrics": {
            "cited_source_snapshot_coverage": coverage,
            "total_findings": total,
            "semantically_supported": supported,
            "conflicted": conflicted,
        },
    }


def _payload(row: dict | None = None) -> dict:
    payload = {
        "schema_version": 1,
        "dataset": ["case-1"],
        "rows": [row or _row()],
    }
    encoded_rows = json.dumps(payload["rows"], ensure_ascii=False, sort_keys=True)
    payload["rows_sha256"] = hashlib.sha256(encoded_rows.encode("utf-8")).hexdigest()
    payload["dataset_sha256"] = hashlib.sha256(b"dataset").hexdigest()
    return payload


def test_absolute_quality_gates_pass() -> None:
    report = evaluate_regression(_payload())
    assert report.passed
    assert report.checks == 3
    assert format_regression(report).startswith("PASS")


def test_absolute_quality_gates_report_all_failures() -> None:
    report = evaluate_regression(
        _payload(_row(coverage=0.5, total=10, supported=7, conflicted=2))
    )
    metrics = {failure.metric for failure in report.failures}
    assert metrics == {
        "citation_snapshot_coverage",
        "unsupported_claim_rate",
        "conflict_rate",
    }
    assert "FAIL" in format_regression(report)


def test_empty_findings_fail_even_when_citation_coverage_is_disabled() -> None:
    candidate = _payload(_row(coverage=0.0, total=0, supported=0, conflicted=0))
    report = evaluate_regression(
        candidate,
        policy=RegressionPolicy(min_citation_snapshot_coverage=0.0),
    )
    assert "unsupported_claim_rate" in {failure.metric for failure in report.failures}


def test_baseline_gates_detect_score_and_cost_regression() -> None:
    baseline = _payload(_row(score=4.5, tokens=100))
    candidate = _payload(_row(score=4.0, tokens=140))
    report = evaluate_regression(candidate, baseline)
    metrics = {failure.metric for failure in report.failures}
    assert "judge_score_drop" in metrics
    assert "token_increase_rate" in metrics


def test_zero_token_baseline_rejects_positive_candidate_cost() -> None:
    baseline = _payload(_row(tokens=0))
    candidate = _payload(_row(tokens=1))
    report = evaluate_regression(candidate, baseline)
    failure = next(
        failure for failure in report.failures if failure.metric == "token_increase_rate"
    )
    assert failure.actual == "infinite"


def test_dataset_fingerprint_drift_fails_with_same_ids() -> None:
    baseline = _payload()
    candidate = _payload()
    candidate["dataset_sha256"] = hashlib.sha256(b"changed").hexdigest()
    report = evaluate_regression(candidate, baseline)
    assert "dataset" in {failure.metric for failure in report.failures}


def test_custom_policy_allows_known_tradeoff() -> None:
    baseline = _payload(_row(score=4.5, tokens=100))
    candidate = _payload(_row(score=4.0, tokens=140))
    report = evaluate_regression(
        candidate,
        baseline,
        policy=RegressionPolicy(max_judge_score_drop=0.5, max_token_increase_rate=0.4),
    )
    assert report.passed


def test_missing_metrics_and_rows_fail_closed() -> None:
    baseline = _payload()
    candidate = _payload({"case_id": "other", "workflow": "deep", "metrics": None})
    candidate["dataset"] = ["other"]
    report = evaluate_regression(candidate, baseline)
    metrics = {failure.metric for failure in report.failures}
    assert {"dataset", "metrics", "missing_rows"} <= metrics


def test_load_benchmark_validates_schema(tmp_path) -> None:
    valid = tmp_path / "valid.json"
    valid.write_text(json.dumps(_payload()), encoding="utf-8")
    assert load_benchmark(valid)["dataset"] == ["case-1"]

    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"schema_version": 2, "rows": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported benchmark schema"):
        load_benchmark(invalid)


def test_load_benchmark_rejects_modified_rows(tmp_path) -> None:
    payload = _payload()
    payload["rows"][0]["tokens"] = 999
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="rows_sha256 mismatch"):
        load_benchmark(path)
