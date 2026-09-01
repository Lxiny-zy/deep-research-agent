from __future__ import annotations

from deep_research.models import (
    EvidenceVerification,
    ExperimentConditions,
    Finding,
    Quantity,
    ResearchResult,
    SourceIdentity,
)
from deep_research.report import (
    DATASET_PROTOCOL_TABLE_ID,
    EVIDENCE_STRENGTH_TABLE_ID,
    OPTICAL_CODING_TABLE_ID,
    RECONSTRUCTION_TABLE_ID,
    HsiDomainRecord,
    build_hsi_tables,
    hsi_table_schemas,
    hsi_tables_from_results,
)


def test_hsi_schema_exposes_four_stable_tables_and_columns() -> None:
    schemas = hsi_table_schemas()
    assert [table.id for table in schemas] == [
        OPTICAL_CODING_TABLE_ID,
        RECONSTRUCTION_TABLE_ID,
        DATASET_PROTOCOL_TABLE_ID,
        EVIDENCE_STRENGTH_TABLE_ID,
    ]
    assert [column.key for column in schemas[1].columns][:4] == [
        "method",
        "category",
        "dataset",
        "psnr",
    ]


def test_hsi_builder_preserves_missing_cells_and_disagreements() -> None:
    tables = build_hsi_tables(
        [
            HsiDomainRecord(method="MST-L", dataset="KAIST", psnr="38.36", citation=1),
            HsiDomainRecord(method="MST-L", dataset="KAIST", psnr="38.40", citation=2),
        ]
    )
    reconstruction = next(table for table in tables if table.id == RECONSTRUCTION_TABLE_ID)
    row = reconstruction.rows[0]
    assert row.cell("psnr").disputed
    assert row.cell("ssim").reported is False
    assert row.cell("psnr").citations == [1, 2]


def test_hsi_builder_keeps_all_citations_for_matching_values() -> None:
    tables = build_hsi_tables(
        [
            HsiDomainRecord(method="MST-L", dataset="KAIST", psnr="38.36", citation=1),
            HsiDomainRecord(method="MST-L", dataset="KAIST", psnr="38.36", citation=2),
        ]
    )
    reconstruction = next(table for table in tables if table.id == RECONSTRUCTION_TABLE_ID)
    cell = reconstruction.rows[0].cell("psnr")
    assert cell.value == "38.36"
    assert cell.citations == [1, 2]
    assert cell.disputed is False


def test_hsi_adapter_uses_only_report_eligible_findings() -> None:
    conditions = ExperimentConditions(dataset="KAIST", split="test", bands=28)
    finding = Finding(
        statement="MST-L reports 38.36 dB on KAIST",
        source_url="https://example.test/paper",
        evidence_quote="38.36 dB",
        entity="MST-L",
        quantity=Quantity(metric="PSNR", value=38.36, unit="dB", rendered="38.36"),
        conditions=conditions,
        verification=EvidenceVerification(
            status="verified",
            quantity_status="verified",
            semantic_status="supported",
            source_title="Paper A",
        ),
    )
    result = ResearchResult(sub_question="benchmark", findings=[finding])
    tables = hsi_tables_from_results([result], {finding.source_url: 1})
    reconstruction = next(table for table in tables if table.id == RECONSTRUCTION_TABLE_ID)
    assert reconstruction.rows[0].label == "MST-L"
    assert reconstruction.rows[0].cell("psnr").value == "38.36 dB"


def test_hsi_adapter_does_not_project_plain_reconstruction_into_optical_table() -> None:
    finding = Finding(
        statement="MST-L reports 38.36 dB on KAIST",
        source_url="https://example.test/plain-reconstruction",
        evidence_quote="MST-L reports 38.36 dB on KAIST",
        entity="MST-L",
        quantity=Quantity(metric="PSNR", value=38.36, unit="dB", rendered="38.36"),
        conditions=ExperimentConditions(dataset="KAIST", split="test", bands=28),
        verification=EvidenceVerification(
            status="verified",
            quantity_status="verified",
            semantic_status="supported",
        ),
    )
    tables = hsi_tables_from_results(
        [ResearchResult(sub_question="benchmark", findings=[finding])],
        {finding.source_url: 1},
    )
    optical = next(table for table in tables if table.id == OPTICAL_CODING_TABLE_ID)
    assert optical.rows == []


def test_hsi_adapter_preserves_quantity_comparator_and_uncertainty() -> None:
    finding = Finding(
        statement="MST-L exceeds 38.36 dB on KAIST",
        source_url="https://example.test/paper",
        evidence_quote=">38.36 dB",
        entity="MST-L",
        quantity=Quantity(
            metric="PSNR",
            value=38.36,
            unit="dB",
            rendered="38.36",
            comparator=">",
            uncertainty=0.05,
        ),
        verification=EvidenceVerification(
            status="verified", quantity_status="verified", semantic_status="supported"
        ),
    )
    tables = hsi_tables_from_results(
        [ResearchResult(sub_question="benchmark", findings=[finding])],
        {finding.source_url: 1},
    )
    reconstruction = next(table for table in tables if table.id == RECONSTRUCTION_TABLE_ID)
    assert reconstruction.rows[0].cell("psnr").value == ">38.36 dB ± 0.05"


def test_hsi_adapter_drops_findings_without_report_citations() -> None:
    finding = Finding(
        statement="MST-L reports 38.36 dB",
        source_url="https://example.test/not-cited",
        evidence_quote="38.36 dB",
        entity="MST-L",
        quantity=Quantity(metric="PSNR", value=38.36, unit="dB", rendered="38.36"),
        verification=EvidenceVerification(
            status="verified", quantity_status="verified", semantic_status="supported"
        ),
    )
    tables = hsi_tables_from_results(
        [ResearchResult(sub_question="q", findings=[finding])], index_by_url={}
    )
    assert all(not table.rows for table in tables)


def test_hsi_adapter_projects_resource_metrics_into_reconstruction_table() -> None:
    conditions = ExperimentConditions(dataset="KAIST", split="test", bands=28)
    findings = [
        Finding(
            statement="DAUHST uses 1.2M parameters",
            source_url="https://example.test/paper",
            evidence_quote="1.2M parameters",
            entity="DAUHST",
            quantity=Quantity(metric="parameter count", value=1.2, unit="M", rendered="1.2"),
            conditions=conditions,
            verification=EvidenceVerification(
                status="verified", quantity_status="verified", semantic_status="supported"
            ),
        ),
        Finding(
            statement="DAUHST needs 18.4 GFLOPs",
            source_url="https://example.test/paper",
            evidence_quote="18.4 GFLOPs",
            entity="DAUHST",
            quantity=Quantity(metric="GFLOPs", value=18.4, unit="GFLOPs", rendered="18.4"),
            conditions=conditions,
            verification=EvidenceVerification(
                status="verified", quantity_status="verified", semantic_status="supported"
            ),
        ),
        Finding(
            statement="DAUHST inference time is 12 ms",
            source_url="https://example.test/paper",
            evidence_quote="12 ms",
            entity="DAUHST",
            quantity=Quantity(metric="inference time", value=12, unit="ms", rendered="12"),
            conditions=conditions,
            verification=EvidenceVerification(
                status="verified", quantity_status="verified", semantic_status="supported"
            ),
        ),
    ]
    tables = hsi_tables_from_results(
        [ResearchResult(sub_question="complexity", findings=findings)],
        {findings[0].source_url: 1},
    )
    reconstruction = next(table for table in tables if table.id == RECONSTRUCTION_TABLE_ID)
    row = reconstruction.rows[0]
    assert row.cell("parameters").value == "1.2 M"
    assert row.cell("flops").value == "18.4 GFLOPs"
    assert row.cell("inference_time").value == "12 ms"


def test_assemble_document_keeps_hsi_tables_opt_in() -> None:
    from deep_research.models import Report
    from deep_research.report import assemble_document

    finding = Finding(
        statement="MST-L reports 38.36 dB",
        source_url="https://example.test/paper",
        evidence_quote="38.36 dB",
        entity="MST-L",
        quantity=Quantity(metric="PSNR", value=38.36, unit="dB", rendered="38.36"),
        verification=EvidenceVerification(status="verified", quantity_status="verified"),
    )
    result = ResearchResult(sub_question="q", findings=[finding])
    report = Report(query="q", markdown="text", citations=[finding.source_url])
    assert not any(
        block.id == RECONSTRUCTION_TABLE_ID
        for block in assemble_document(report, [result]).blocks
        if hasattr(block, "id")
    )
    assert any(
        block.id == RECONSTRUCTION_TABLE_ID
        for block in assemble_document(report, [result], include_hsi_tables=True).blocks
        if hasattr(block, "id")
    )


def _eligible_finding(
    *,
    url: str,
    claim_id: str,
    identity: SourceIdentity,
    status: str = "single_source",
    independent_count: int = 1,
    corroborates: list[str] | None = None,
    consistency: str = "clear",
    conditions: ExperimentConditions | None = None,
) -> Finding:
    return Finding(
        statement="CASSI reconstruction reports 38.36 dB",
        source_url=url,
        evidence_quote="38.36 dB",
        entity="DAUHST",
        quantity=Quantity(metric="PSNR", value=38.36, unit="dB", rendered="38.36"),
        conditions=conditions,
        verification=EvidenceVerification(
            status="verified",
            quantity_status="verified",
            semantic_status="supported",
            source_title=identity.title,
            source_identity=identity,
            claim_id=claim_id,
            consistency_status=consistency,
            corroboration_status=status,
            independent_source_count=independent_count,
            corroborates_claim_ids=corroborates or [],
            corroboration_reason=(
                "no_independent_corroboration:same source rejected (same_doi)"
                if status == "single_source" and independent_count == 1 and corroborates is None
                else "independent_sources_corroborate_claim"
            ),
        ),
    )


def test_hsi_adapter_projects_conditions_and_preserves_unknown_fields() -> None:
    url = "https://arxiv.org/abs/2205.10102"
    finding = _eligible_finding(
        url=url,
        claim_id="claim-a",
        identity=SourceIdentity(
            title="DAUHST",
            work_id="arxiv:2205.10102",
            domain="arxiv.org",
        ),
        conditions=ExperimentConditions(
            dataset="KAIST",
            split="10 scenes test",
            bands=28,
            spatial_size="256x256",
            protocol="simulation",
            train_data="CAVE",
            hardware="GPU",
            notes="fixed mask",
        ),
    )
    tables = hsi_tables_from_results(
        [ResearchResult(sub_question="q", findings=[finding])], {url: 1}
    )

    reconstruction = next(table for table in tables if table.id == RECONSTRUCTION_TABLE_ID)
    assert reconstruction.rows[0].cell("protocol").value == (
        "simulation; train_data: CAVE; hardware: GPU; notes: fixed mask"
    )
    dataset = next(table for table in tables if table.id == DATASET_PROTOCOL_TABLE_ID)
    row = dataset.rows[0]
    assert row.cell("scenes").value == "10 scenes test"
    assert row.cell("acquisition").value == "simulated"
    assert row.cell("spectral_range").reported is False

    evidence = next(table for table in tables if table.id == EVIDENCE_STRENGTH_TABLE_ID)
    evidence_row = evidence.rows[0]
    assert evidence_row.cell("preprint").value == "yes"
    assert evidence_row.cell("peer_reviewed").value == "unknown"
    assert evidence_row.cell("independent_works").value == "1"
    assert evidence_row.cell("same_team").value == "yes"
    assert evidence_row.cell("conflict").value == "no"


def test_hsi_adapter_preserves_peer_reviewed_tristate() -> None:
    cases = ((True, "yes"), (False, "no"), (None, "unknown"))
    for index, (peer_reviewed, expected) in enumerate(cases, start=1):
        url = f"https://example.test/paper-{index}"
        finding = _eligible_finding(
            url=url,
            claim_id=f"peer-{index}",
            identity=SourceIdentity(
                title=f"Paper {index}",
                domain="example.test",
                peer_reviewed=peer_reviewed,
            ),
        )
        tables = hsi_tables_from_results(
            [ResearchResult(sub_question="q", findings=[finding])], {url: 1}
        )
        evidence = next(table for table in tables if table.id == EVIDENCE_STRENGTH_TABLE_ID)
        assert evidence.rows[0].cell("peer_reviewed").value == expected


def test_hsi_adapter_prefers_explicit_structured_condition_fields() -> None:
    url = "https://example.test/cassi"
    conditions = ExperimentConditions(
        dataset="KAIST",
        split="test",
        bands=28,
        spectral_range="400-700 nm",
        scenes="S1-S10",
        acquisition="real capture",
        protocol="2px shift",
    )
    finding = _eligible_finding(
        url=url,
        claim_id="claim-structured",
        identity=SourceIdentity(title="CASSI work", domain="example.test"),
        conditions=conditions,
    )
    finding = finding.model_copy(
        update={
            "statement": "CASSI reconstruction reports 38.36 dB",
            "evidence_quote": "CASSI reconstruction reports 38.36 dB",
        }
    )

    tables = hsi_tables_from_results(
        [ResearchResult(sub_question="q", findings=[finding])], {url: 1}
    )
    dataset = next(table for table in tables if table.id == DATASET_PROTOCOL_TABLE_ID)
    row = dataset.rows[0]
    assert row.cell("spectral_range").value == "400-700 nm"
    assert row.cell("scenes").value == "S1-S10"
    assert row.cell("acquisition").value == "real capture"

    optical = next(table for table in tables if table.id == OPTICAL_CODING_TABLE_ID)
    assert optical.rows[0].cell("spectral_range").value == "400-700 nm"


def test_researcher_prompt_requires_hsi_condition_fields() -> None:
    from deep_research.agents.researcher import SYSTEM

    assert all(field in SYSTEM for field in ("spectral_range", "scenes", "acquisition"))


def test_hsi_adapter_uses_clustered_independence_and_conflict_flags() -> None:
    first_url = "https://arxiv.org/abs/1"
    second_url = "https://example.org/paper"
    first = _eligible_finding(
        url=first_url,
        claim_id="claim-a",
        identity=SourceIdentity(title="Work A", work_id="arxiv:1", domain="arxiv.org"),
        status="corroborated",
        independent_count=2,
        corroborates=["claim-b"],
    )
    second = _eligible_finding(
        url=second_url,
        claim_id="claim-b",
        identity=SourceIdentity(title="Work B", doi="10.1/b", domain="example.org"),
        status="disputed",
        independent_count=2,
        corroborates=["claim-a"],
        consistency="conflicted",
    )
    tables = hsi_tables_from_results(
        [ResearchResult(sub_question="q", findings=[first, second])],
        {first_url: 1, second_url: 2},
    )
    evidence = next(table for table in tables if table.id == EVIDENCE_STRENGTH_TABLE_ID)
    by_label = {row.label: row for row in evidence.rows}
    assert by_label["Work A"].cell("independent_works").value == "2"
    assert by_label["Work A"].cell("same_team").value == "no"
    assert by_label["Work A"].cell("conflict").value == "no"
    assert by_label["Work B"].cell("conflict").value == "yes"
