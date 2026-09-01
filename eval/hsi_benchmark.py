"""Deterministic benchmark gates for hyperspectral-imaging (HSI) reviews.

The benchmark deliberately operates on the structured objects produced by the
research pipeline.  It does not inspect prose or ask a judge model to grade a
run.  A small JSON-friendly gold schema makes the four domain risks measurable:
numeric extraction, experimental-condition completeness, duplicate-work
interception, and table-column assignment.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deep_research.independence import cluster_sources
from deep_research.models import ExperimentConditions, Finding, SourceIdentity
from deep_research.quantities import normalize_unit, tolerance_for


@dataclass(frozen=True)
class GoldQuantity:
    """One expected metric value for an entity under a condition signature."""

    entity: str
    metric: str
    value: float
    unit: str = ""
    rendered: str = ""
    comparator: str = ""
    conditions: ExperimentConditions | None = None
    # Provenance is part of a curated annotation, even though the deterministic
    # matcher only needs the structured value.  Keeping the quote and section
    # beside the expected value makes a fixture auditable without pretending
    # that the evaluator can independently prove the paper's result.
    source_url: str = ""
    source_doi: str = ""
    source_section: str = ""
    evidence_quote: str = ""

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> GoldQuantity:
        conditions = raw.get("conditions")
        return cls(
            entity=str(raw.get("entity", "")),
            metric=str(raw.get("metric", "")),
            value=float(raw["value"]),
            unit=str(raw.get("unit", "")),
            rendered=str(raw.get("rendered", "")),
            comparator=str(raw.get("comparator", "")),
            conditions=(
                conditions
                if isinstance(conditions, ExperimentConditions)
                else ExperimentConditions.model_validate(conditions)
                if isinstance(conditions, Mapping)
                else None
            ),
            source_url=str(raw.get("source_url", "")),
            source_doi=str(raw.get("source_doi", "")),
            source_section=str(raw.get("source_section", "")),
            evidence_quote=str(raw.get("evidence_quote", "")),
        )


@dataclass(frozen=True)
class HsiGoldCase:
    """Gold annotations for one source/table fixture."""

    case_id: str
    quantities: tuple[GoldQuantity, ...] = ()
    required_condition_fields: tuple[str, ...] = ()
    # Keys are source keys and values are the expected publication/work group.
    source_groups: Mapping[str, str] = field(default_factory=dict)
    # Expected table column for each (entity, metric) pair.  A condition-aware
    # key may be supplied as ``(entity, metric, condition_signature)``.
    column_assignments: Mapping[str, str] = field(default_factory=dict)
    # ``curated_draft`` means a maintainer checked the cited source text, but
    # the fixture has not yet had a second independent annotation pass.  This
    # distinction keeps release dashboards from presenting one-paper coverage
    # as a domain-wide gold standard.
    annotation_status: str = "unreviewed"
    annotation_note: str = ""
    condition_evidence_quotes: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> HsiGoldCase:
        quantities = tuple(
            item if isinstance(item, GoldQuantity) else GoldQuantity.from_dict(item)
            for item in raw.get("quantities", [])
            if isinstance(item, (GoldQuantity, Mapping))
        )
        groups = raw.get("source_groups", {})
        assignments = raw.get("column_assignments", {})
        return cls(
            case_id=str(raw.get("case_id", raw.get("id", ""))),
            quantities=quantities,
            required_condition_fields=tuple(
                str(x) for x in raw.get("required_condition_fields", [])
            ),
            source_groups={str(k): str(v) for k, v in groups.items()}
            if isinstance(groups, Mapping)
            else {},
            column_assignments={str(k): str(v) for k, v in assignments.items()}
            if isinstance(assignments, Mapping)
            else {},
            annotation_status=str(raw.get("annotation_status", "unreviewed")),
            annotation_note=str(raw.get("annotation_note", "")),
            condition_evidence_quotes=tuple(
                str(quote)
                for quote in (
                    raw.get("condition_evidence_quotes", [])
                    if isinstance(raw.get("condition_evidence_quotes", []), list)
                    else [raw.get("condition_evidence_quote", "")]
                )
                if str(quote)
            ),
        )


@dataclass(frozen=True)
class HsiCaseMetrics:
    case_id: str
    quantity_accuracy: float
    condition_completeness: float
    pseudo_dual_source_interception_rate: float
    table_column_accuracy: float
    quantity_expected: int = 0
    quantity_matched: int = 0
    conditions_expected: int = 0
    conditions_complete: int = 0
    pseudo_pairs: int = 0
    pseudo_pairs_blocked: int = 0
    columns_expected: int = 0
    columns_matched: int = 0


@dataclass(frozen=True)
class HsiBenchmarkMetrics:
    cases: tuple[HsiCaseMetrics, ...]
    quantity_accuracy: float
    condition_completeness: float
    pseudo_dual_source_interception_rate: float
    table_column_accuracy: float

    @property
    def benchmark_numeric_accuracy(self) -> float:
        """Readable alias used by release dashboards."""

        return self.quantity_accuracy

    @property
    def experiment_condition_completeness(self) -> float:
        return self.condition_completeness

    @property
    def false_double_source_interception_rate(self) -> float:
        return self.pseudo_dual_source_interception_rate

    def as_dict(self) -> dict[str, Any]:
        return {
            "quantity_accuracy": self.quantity_accuracy,
            "benchmark_numeric_accuracy": self.benchmark_numeric_accuracy,
            "condition_completeness": self.condition_completeness,
            "experiment_condition_completeness": self.experiment_condition_completeness,
            "pseudo_dual_source_interception_rate": self.pseudo_dual_source_interception_rate,
            "false_double_source_interception_rate": self.false_double_source_interception_rate,
            "table_column_accuracy": self.table_column_accuracy,
            "cases": [case.__dict__ for case in self.cases],
        }


def evaluate_hsi_case(
    gold: HsiGoldCase | Mapping[str, Any],
    findings: Sequence[Finding],
    *,
    source_identities: Mapping[str, SourceIdentity] | None = None,
    predicted_columns: Mapping[str, str] | None = None,
) -> HsiCaseMetrics:
    """Evaluate one case with conservative, deterministic matching."""

    gold_case = gold if isinstance(gold, HsiGoldCase) else HsiGoldCase.from_dict(gold)
    matched = sum(1 for expected in gold_case.quantities if _quantity_match(expected, findings))
    quantity_expected = len(gold_case.quantities)

    condition_total = len(gold_case.quantities)
    condition_complete = sum(
        1
        for expected in gold_case.quantities
        if _condition_complete(
            _find_quantity(expected, findings), gold_case.required_condition_fields
        )
    )

    pseudo_pairs = _pseudo_pairs(gold_case.source_groups)
    blocked = _count_blocked_pairs(pseudo_pairs, source_identities or {})

    assignments = predicted_columns or {}
    column_total = len(gold_case.column_assignments)
    column_matched = sum(
        1
        for key, expected_column in gold_case.column_assignments.items()
        if assignments.get(key) == expected_column
    )

    return HsiCaseMetrics(
        case_id=gold_case.case_id,
        quantity_accuracy=_ratio(matched, quantity_expected),
        condition_completeness=_ratio(condition_complete, condition_total),
        pseudo_dual_source_interception_rate=_ratio(blocked, len(pseudo_pairs)),
        table_column_accuracy=_ratio(column_matched, column_total),
        quantity_expected=quantity_expected,
        quantity_matched=matched,
        conditions_expected=condition_total,
        conditions_complete=condition_complete,
        pseudo_pairs=len(pseudo_pairs),
        pseudo_pairs_blocked=blocked,
        columns_expected=column_total,
        columns_matched=column_matched,
    )


def evaluate_hsi_benchmark(
    gold_cases: Sequence[HsiGoldCase | Mapping[str, Any]],
    predictions: Mapping[str, Sequence[Finding]],
    *,
    source_identities: Mapping[str, Mapping[str, SourceIdentity]] | None = None,
    predicted_columns: Mapping[str, Mapping[str, str]] | None = None,
) -> HsiBenchmarkMetrics:
    """Evaluate all cases and aggregate by annotated item, not by case size."""

    case_metrics = tuple(
        evaluate_hsi_case(
            case,
            predictions.get(_case_id(case), ()),
            source_identities=(source_identities or {}).get(_case_id(case), {}),
            predicted_columns=(predicted_columns or {}).get(_case_id(case), {}),
        )
        for case in gold_cases
    )
    quantity_expected = sum(item.quantity_expected for item in case_metrics)
    quantity_matched = sum(item.quantity_matched for item in case_metrics)
    condition_expected = sum(item.conditions_expected for item in case_metrics)
    condition_complete = sum(item.conditions_complete for item in case_metrics)
    pair_total = sum(item.pseudo_pairs for item in case_metrics)
    pair_blocked = sum(item.pseudo_pairs_blocked for item in case_metrics)
    column_expected = sum(item.columns_expected for item in case_metrics)
    column_matched = sum(item.columns_matched for item in case_metrics)
    return HsiBenchmarkMetrics(
        cases=case_metrics,
        quantity_accuracy=_ratio(quantity_matched, quantity_expected),
        condition_completeness=_ratio(condition_complete, condition_expected),
        pseudo_dual_source_interception_rate=_ratio(pair_blocked, pair_total),
        table_column_accuracy=_ratio(column_matched, column_expected),
    )


def load_hsi_gold(path: str | Path) -> tuple[HsiGoldCase, ...]:
    """Load a JSON object with ``schema_version: 1`` and ``cases``."""

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping) or raw.get("schema_version") != 1:
        raise ValueError("unsupported HSI gold schema")
    cases = raw.get("cases")
    if not isinstance(cases, list):
        raise ValueError("HSI gold cases must be a list")
    parsed = tuple(HsiGoldCase.from_dict(item) for item in cases if isinstance(item, Mapping))
    for case in parsed:
        _validate_curated_provenance(case)
    return parsed


def _validate_curated_provenance(case: HsiGoldCase) -> None:
    """Fail closed when a curated fixture drops its audit trail.

    This checks the annotation's internal contract only.  It does not fetch a
    paper or claim that the cited URL is still available; source verification
    remains the responsibility of the annotation review process.
    """
    if case.annotation_status != "curated_draft":
        return
    if not case.condition_evidence_quotes:
        raise ValueError(f"curated HSI case {case.case_id} has no condition evidence quote")
    for index, quantity in enumerate(case.quantities):
        missing = [
            name
            for name, value in (
                ("source_url", quantity.source_url),
                ("source_doi", quantity.source_doi),
                ("source_section", quantity.source_section),
                ("evidence_quote", quantity.evidence_quote),
            )
            if not value.strip()
        ]
        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"curated HSI case {case.case_id} quantity {index} missing {joined}")
        if "..." in quantity.evidence_quote:
            raise ValueError(f"curated HSI case {case.case_id} quantity {index} uses ellipsis")
        if quantity.rendered and quantity.rendered not in quantity.evidence_quote:
            raise ValueError(
                f"curated HSI case {case.case_id} quantity {index} quote misses rendered value"
            )
        if (
            quantity.metric.strip()
            and quantity.metric.casefold() not in quantity.evidence_quote.casefold()
        ):
            raise ValueError(
                f"curated HSI case {case.case_id} quantity {index} quote misses metric"
            )


def _case_id(case: HsiGoldCase | Mapping[str, Any]) -> str:
    return (
        case.case_id
        if isinstance(case, HsiGoldCase)
        else str(case.get("case_id", case.get("id", "")))
    )


def _quantity_match(expected: GoldQuantity, findings: Sequence[Finding]) -> bool:
    return _find_quantity(expected, findings) is not None


def _find_quantity(expected: GoldQuantity, findings: Sequence[Finding]) -> Finding | None:
    for finding in findings:
        quantity = finding.quantity
        if not quantity or quantity.value is None:
            continue
        if finding.entity.strip().casefold() != expected.entity.strip().casefold():
            continue
        if quantity.metric.strip().casefold() != expected.metric.strip().casefold():
            continue
        if _condition_signature(finding.conditions) != _condition_signature(expected.conditions):
            continue
        expected_unit, scale = normalize_unit(expected.unit)
        actual_unit, actual_scale = normalize_unit(quantity.unit)
        if expected_unit != actual_unit:
            continue
        target = expected.value * scale
        actual = float(quantity.value) * actual_scale
        tolerance = tolerance_for(expected.rendered or str(expected.value), target)
        if math.isfinite(actual) and abs(actual - target) <= tolerance:
            if expected.comparator and quantity.comparator != expected.comparator:
                continue
            return finding
    return None


def _condition_complete(finding: Finding | None, fields: Sequence[str]) -> bool:
    if finding is None:
        return False
    if not fields:
        return finding.conditions is not None and not finding.conditions.is_empty()
    conditions = finding.conditions
    if conditions is None:
        return False
    return all(bool(getattr(conditions, field, "")) for field in fields)


def _condition_signature(conditions: ExperimentConditions | None) -> tuple[Any, ...]:
    if conditions is None or conditions.is_empty():
        return ()
    return (
        conditions.dataset.strip().casefold(),
        conditions.split.strip().casefold(),
        conditions.bands,
        conditions.spatial_size.strip().casefold(),
        conditions.protocol.strip().casefold(),
        conditions.train_data.strip().casefold(),
        conditions.hardware.strip().casefold(),
    )


def _pseudo_pairs(groups: Mapping[str, str]) -> list[tuple[str, str]]:
    by_group: dict[str, list[str]] = {}
    for key, group in groups.items():
        by_group.setdefault(group, []).append(key)
    pairs: list[tuple[str, str]] = []
    for keys in by_group.values():
        pairs.extend((keys[i], keys[j]) for i in range(len(keys)) for j in range(i + 1, len(keys)))
    return pairs


def _count_blocked_pairs(
    pairs: Sequence[tuple[str, str]], identities: Mapping[str, SourceIdentity]
) -> int:
    if not pairs:
        return 0
    graph = cluster_sources(identities)
    return sum(1 for left, right in pairs if graph.same_publisher(left, right))


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 1.0


__all__ = [
    "GoldQuantity",
    "HsiGoldCase",
    "HsiCaseMetrics",
    "HsiBenchmarkMetrics",
    "evaluate_hsi_case",
    "evaluate_hsi_benchmark",
    "load_hsi_gold",
]
