from __future__ import annotations

from pathlib import Path

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
    assert metrics.quantity_accuracy == 1.0
    assert metrics.condition_completeness == 1.0
    assert metrics.pseudo_dual_source_interception_rate == 1.0
    assert metrics.table_column_accuracy == 1.0
    assert metrics.as_dict()["cases"][0]["case_id"] == "empty"


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
