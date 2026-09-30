from __future__ import annotations

import math
from pathlib import Path

import pytest

from deep_research.models import (
    EvidenceVerification,
    ExperimentConditions,
    Finding,
    Quantity,
    SourceIdentity,
)
from eval.hsi_benchmark import (
    GoldQuantity,
    HsiGoldCase,
    evaluate_hsi_benchmark,
    evaluate_hsi_case,
    load_hsi_gold,
)


def _finding(
    entity: str,
    metric: str,
    value: float,
    *,
    unit: str = "dB",
    rendered: str = "",
    conditions: ExperimentConditions | None = None,
    url: str = "https://a.test/paper",
) -> Finding:
    return Finding(
        statement=f"{entity} {metric} {rendered or value}",
        source_url=url,
        evidence_quote=rendered or str(value),
        entity=entity,
        quantity=Quantity(metric=metric, value=value, unit=unit, rendered=rendered),
        conditions=conditions,
        verification=EvidenceVerification(status="verified", quantity_status="verified"),
    )


def test_hsi_metrics_cover_numeric_conditions_pseudo_sources_and_columns() -> None:
    conditions = ExperimentConditions(dataset="KAIST", split="10 scenes", bands=28)
    gold = HsiGoldCase(
        case_id="c1",
        quantities=(
            GoldQuantity(
                entity="MST-L",
                metric="PSNR",
                value=38.36,
                unit="dB",
                rendered="38.36",
                conditions=conditions,
            ),
        ),
        required_condition_fields=("dataset", "split", "bands"),
        source_groups={"a": "work-1", "b": "work-1", "c": "work-2"},
        column_assignments={"MST-L|PSNR": "psnr__1"},
    )
    identities = {
        "a": SourceIdentity(doi="10.1/work", domain="arxiv.org"),
        "b": SourceIdentity(doi="10.1/work", domain="opg.optica.org"),
        "c": SourceIdentity(doi="10.1/other", domain="ieee.org"),
    }
    finding = _finding(
        "MST-L", "PSNR", 38.36, rendered="38.36", conditions=conditions, url="https://a.test/paper"
    )
    metrics = evaluate_hsi_case(
        gold,
        [finding],
        source_identities=identities,
        predicted_columns={"MST-L|PSNR": "psnr__1"},
    )
    assert metrics.quantity_accuracy == 1.0
    assert metrics.condition_completeness == 1.0
    assert metrics.pseudo_dual_source_interception_rate == 1.0
    assert metrics.table_column_accuracy == 1.0


def test_wrong_unit_or_missing_condition_is_not_counted() -> None:
    gold = {
        "case_id": "c2",
        "quantities": [
            {
                "entity": "MST-L",
                "metric": "PSNR",
                "value": 38.36,
                "unit": "dB",
                "rendered": "38.36",
                "conditions": {"dataset": "KAIST"},
            }
        ],
        "required_condition_fields": ["dataset", "bands"],
    }
    finding = _finding(
        "MST-L",
        "PSNR",
        38.36,
        unit="",
        rendered="38.36",
        conditions=ExperimentConditions(dataset="KAIST"),
    )
    metrics = evaluate_hsi_case(gold, [finding])
    assert metrics.quantity_accuracy == 0.0
    assert metrics.condition_completeness == 0.0


def test_benchmark_aggregates_item_counts_and_accepts_empty_dimensions() -> None:
    gold = [HsiGoldCase(case_id="empty")]
    metrics = evaluate_hsi_benchmark(gold, {})
    assert metrics.quantity_precision is None
    assert metrics.quantity_recall is None
    assert metrics.quantity_f1 is None
    assert metrics.condition_completeness is None
    assert metrics.pseudo_dual_source_interception_rate is None
    assert metrics.source_false_merge_rate is None
    assert metrics.table_column_accuracy is None
    assert metrics.as_dict()["cases"][0]["case_id"] == "empty"


def test_numeric_metrics_penalize_extra_predictions_and_report_precision_recall() -> None:
    gold = HsiGoldCase(
        case_id="extra",
        quantities=(GoldQuantity(entity="MST-L", metric="PSNR", value=38.36, unit="dB"),),
    )
    findings = [
        _finding("MST-L", "PSNR", 38.36, unit="dB"),
        _finding("MST-L", "PSNR", 39.10, unit="dB"),
    ]
    metrics = evaluate_hsi_case(gold, findings)
    assert metrics.quantity_precision == 0.5
    assert metrics.quantity_recall == 1.0
    assert metrics.quantity_f1 == pytest.approx(2 / 3, abs=1e-6)
    assert metrics.quantity_accuracy == metrics.quantity_f1


def test_condition_signature_covers_acquisition_and_optical_fields() -> None:
    gold_conditions = ExperimentConditions(
        dataset="KAIST",
        bands=28,
        spectral_range="400-700 nm",
        acquisition="real capture",
        coding_mode="CASSI",
        dispersive_element="prism",
    )
    finding_conditions = gold_conditions.model_copy(update={"acquisition": "simulated"})
    gold = HsiGoldCase(
        case_id="conditions",
        quantities=(
            GoldQuantity(
                entity="MST-L", metric="PSNR", value=38.36, unit="dB", conditions=gold_conditions
            ),
        ),
    )
    metrics = evaluate_hsi_case(
        gold, [_finding("MST-L", "PSNR", 38.36, conditions=finding_conditions)]
    )
    assert metrics.quantity_recall == 0.0
    assert metrics.condition_completeness == 0.0


def test_independent_source_pairs_measure_false_merges_separately() -> None:
    gold = HsiGoldCase(
        case_id="sources",
        source_groups={"preprint": "work-a", "publisher": "work-a"},
        independent_source_pairs=(("preprint", "publisher"),),
    )
    identities = {
        "preprint": SourceIdentity(doi="10.1/same", domain="arxiv.org"),
        "publisher": SourceIdentity(doi="10.1/same", domain="opg.optica.org"),
    }
    metrics = evaluate_hsi_case(gold, [], source_identities=identities)
    assert metrics.pseudo_dual_source_interception_rate == 1.0
    assert metrics.source_false_merge_rate == 1.0
    assert metrics.independent_pairs_merged == 1


def test_release_gate_rejects_curated_draft_fixture() -> None:
    path = Path(__file__).parents[1] / "eval" / "baselines" / "hsi_gold.json"
    with pytest.raises(ValueError, match="not release-ready"):
        load_hsi_gold(path, require_release_ready=True)


def test_non_finite_gold_quantity_is_rejected() -> None:
    with pytest.raises(ValueError, match="finite"):
        GoldQuantity(entity="MST-L", metric="PSNR", value=math.nan)


def test_checked_fixture_keeps_paper_provenance_and_draft_status() -> None:
    path = Path(__file__).parents[1] / "eval" / "baselines" / "hsi_gold.json"
    cases = load_hsi_gold(path)

    assert len(cases) == 1
    case = cases[0]
    assert case.annotation_status == "curated_draft"
    assert len(case.condition_evidence_quotes) == 2
    assert all("..." not in quote for quote in case.condition_evidence_quotes)
    assert "KAIST" in case.condition_evidence_quotes[0]
    assert "28 channels" in case.condition_evidence_quotes[1]
    assert all(quantity.source_url.startswith("https://arxiv.org/") for quantity in case.quantities)
    assert all(quantity.source_doi.startswith("https://doi.org/") for quantity in case.quantities)
    assert all(quantity.source_section == "Results" for quantity in case.quantities)
    assert all(
        quantity.evidence_quote and "38.36" in quantity.evidence_quote
        for quantity in case.quantities
    )
