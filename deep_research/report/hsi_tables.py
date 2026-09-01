"""Structured HSI domain table projections.

The four schemas are deliberately code-owned.  LLM prose can describe a
table, but it cannot invent columns or fill missing cells.  Callers may pass
curated :class:`HsiDomainRecord` values, or use the conservative adapter from
existing ``Finding`` objects for the metrics the pipeline already knows how to
verify.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from ..guardrails import publisher_identity, report_eligible
from ..independence import IndependenceGraph, cluster_sources
from ..models import Finding, ResearchResult, SourceIdentity
from .document import TableBlock, TableCell, TableColumn, TableRow

OPTICAL_CODING_TABLE_ID = "hsi_optical_coding"
RECONSTRUCTION_TABLE_ID = "hsi_reconstruction"
DATASET_PROTOCOL_TABLE_ID = "hsi_dataset_protocol"
EVIDENCE_STRENGTH_TABLE_ID = "hsi_evidence_strength"


class HsiDomainRecord(BaseModel):
    """A normalized row-level record for one of the four HSI tables."""

    model_config = ConfigDict(extra="ignore")

    method: str = ""
    coding_mode: str = ""
    dispersive_element: str = ""
    bands: str = ""
    spectral_range: str = ""
    spatial_resolution: str = ""
    calibration: str = ""
    prototype_validation: str = ""
    category: str = ""
    dataset: str = ""
    psnr: str = ""
    ssim: str = ""
    sam: str = ""
    parameters: str = ""
    flops: str = ""
    inference_time: str = ""
    protocol: str = ""
    scenes: str = ""
    acquisition: str = ""
    split: str = ""
    paper: str = ""
    independent_works: str = ""
    same_team: str = ""
    preprint: str = ""
    peer_reviewed: str = ""
    conflict: str = ""
    citation: int | None = Field(default=None, ge=1)


def hsi_table_schemas() -> tuple[TableBlock, ...]:
    """Return the four stable schemas with no rows.

    This is useful to UI/report planners that need to render a predictable
    set of tabs even when a run has not extracted a row for every domain view.
    """

    return (
        _table(
            OPTICAL_CODING_TABLE_ID,
            "Optical coding schemes",
            (
                ("method", "Method", ""),
                ("coding_mode", "Coding mode", ""),
                ("dispersive_element", "Dispersive element", ""),
                ("bands", "Bands", ""),
                ("spectral_range", "Spectral range", ""),
                ("spatial_resolution", "Spatial resolution", ""),
                ("calibration", "Calibration", ""),
                ("prototype_validation", "Prototype validation", ""),
            ),
        ),
        _table(
            RECONSTRUCTION_TABLE_ID,
            "Reconstruction algorithms",
            (
                ("method", "Method", ""),
                ("category", "Category", ""),
                ("dataset", "Dataset", ""),
                ("psnr", "PSNR", "dB"),
                ("ssim", "SSIM", ""),
                ("sam", "SAM", ""),
                ("parameters", "Parameters", ""),
                ("flops", "FLOPs", ""),
                ("inference_time", "Inference time", ""),
                ("protocol", "Protocol", ""),
            ),
        ),
        _table(
            DATASET_PROTOCOL_TABLE_ID,
            "Datasets and evaluation protocols",
            (
                ("dataset", "Dataset", ""),
                ("bands", "Bands", ""),
                ("spectral_range", "Spectral range", ""),
                ("scenes", "Scenes", ""),
                ("acquisition", "Simulated / real", ""),
                ("split", "Split", ""),
                ("protocol", "Protocol", ""),
            ),
        ),
        _table(
            EVIDENCE_STRENGTH_TABLE_ID,
            "Evidence strength",
            (
                ("paper", "Paper", ""),
                ("independent_works", "Independent works", ""),
                ("same_team", "Same team", ""),
                ("preprint", "Preprint", ""),
                ("peer_reviewed", "Peer reviewed", ""),
                ("conflict", "Conflict", ""),
            ),
        ),
    )


def build_hsi_tables(records: Iterable[HsiDomainRecord | Mapping[str, object]]) -> list[TableBlock]:
    """Build all four tables from normalized records.

    Empty rows are retained as ``Not reported`` cells by the regular report
    renderers; no zero or fabricated value is inserted here.
    """

    normalized = [
        record if isinstance(record, HsiDomainRecord) else HsiDomainRecord.model_validate(record)
        for record in records
    ]
    schemas = list(hsi_table_schemas())
    groups = (
        # Reconstruction records also carry ``method``.  Requiring an optical
        # coding/validation field prevents every ordinary method row from
        # leaking into the optical-coding table merely because it has a name.
        (
            0,
            lambda record: bool(
                record.coding_mode
                or record.dispersive_element
                or record.calibration
                or record.prototype_validation
            ),
        ),
        (
            1,
            lambda record: bool(
                record.method
                and any(
                    getattr(record, field)
                    for field in (
                        "psnr",
                        "ssim",
                        "sam",
                        "parameters",
                        "flops",
                        "inference_time",
                    )
                )
            ),
        ),
        (2, lambda record: bool(record.dataset)),
        (3, lambda record: bool(record.paper)),
    )
    for index, predicate in groups:
        rows = _rows_for_schema(
            schemas[index], [record for record in normalized if predicate(record)]
        )
        schemas[index] = schemas[index].model_copy(update={"rows": rows})
    return schemas


def hsi_tables_from_results(
    results: Sequence[ResearchResult],
    index_by_url: Mapping[str, int],
    *,
    require_corroboration: bool = False,
) -> list[TableBlock]:
    """Conservatively adapt verified findings into the domain schemas."""

    findings = [finding for result in results for finding in result.findings]
    eligible_findings = [
        finding
        for finding in findings
        if (
            report_eligible(finding, require_corroboration=require_corroboration)
            and finding.source_url in index_by_url
        )
    ]
    records: list[HsiDomainRecord] = []
    # Keep identity and claim indexes separate from the table rows.  The
    # corroboration verifier stores claim ids, while the table is keyed by
    # source URL; these indexes let us recover the team cluster without adding
    # another persistence field.
    display_identities: dict[str, SourceIdentity] = {}
    cluster_identities: dict[str, SourceIdentity] = {}
    claim_to_finding: dict[str, Finding] = {}
    for finding in eligible_findings:
        url = finding.source_url
        identity = finding.verification.source_identity
        if identity is not None and not identity.is_empty():
            display_identities[url] = _merge_source_identity(display_identities.get(url), identity)
            cluster_identities[url] = _merge_source_identity(cluster_identities.get(url), identity)
        else:
            # Legacy findings have no identity snapshot.  Preserve the old
            # domain-only clustering fallback and use source_title only for
            # display, never as a new clustering signal.
            display_identities.setdefault(
                url,
                SourceIdentity(
                    title=finding.verification.source_title,
                    domain=publisher_identity(url),
                ),
            )
            cluster_identities.setdefault(url, SourceIdentity(domain=publisher_identity(url)))
        claim_id = finding.verification.claim_id
        if claim_id:
            claim_to_finding.setdefault(claim_id, finding)

    independence = cluster_sources(cluster_identities)
    claim_urls = {claim_id: finding.source_url for claim_id, finding in claim_to_finding.items()}
    evidence_by_url: dict[str, HsiDomainRecord] = {}

    for finding in eligible_findings:
        citation = index_by_url.get(finding.source_url)
        quantity = finding.quantity
        conditions = finding.conditions
        metric = _metric_kind(quantity)
        rendered = _quantity_text(quantity)
        identity = display_identities.get(finding.source_url)
        records.append(
            HsiDomainRecord(
                method=finding.entity,
                category=_category(finding.statement),
                dataset=conditions.dataset if conditions else "",
                bands=str(conditions.bands) if conditions and conditions.bands else "",
                spectral_range=conditions.spectral_range if conditions else "",
                spatial_resolution=conditions.spatial_size if conditions else "",
                protocol=_protocol_text(conditions),
                split=conditions.split if conditions else "",
                psnr=rendered if metric == "psnr" else "",
                ssim=rendered if metric == "ssim" else "",
                sam=rendered if metric == "sam" else "",
                parameters=rendered if metric == "parameters" else "",
                flops=rendered if metric == "flops" else "",
                inference_time=rendered if metric == "inference_time" else "",
                paper=_paper_label(finding, identity),
                citation=citation,
            )
        )
        if _looks_optical(finding):
            optical_text = f"{finding.statement} {finding.evidence_quote}"
            records.append(
                HsiDomainRecord(
                    method=finding.entity,
                    coding_mode=(conditions.coding_mode if conditions else "")
                    or _coding_mode(optical_text),
                    dispersive_element=(conditions.dispersive_element if conditions else "")
                    or _dispersive_element(optical_text),
                    bands=str(conditions.bands) if conditions and conditions.bands else "",
                    spectral_range=conditions.spectral_range if conditions else "",
                    spatial_resolution=conditions.spatial_size if conditions else "",
                    calibration=(conditions.calibration if conditions else "")
                    or _reported_flag(optical_text, "calibrat"),
                    prototype_validation=(conditions.prototype_validation if conditions else "")
                    or _prototype_validation(optical_text),
                    citation=citation,
                )
            )
        if conditions and conditions.dataset:
            records.append(
                HsiDomainRecord(
                    dataset=conditions.dataset,
                    bands=str(conditions.bands) if conditions.bands else "",
                    spectral_range=conditions.spectral_range,
                    spatial_resolution=conditions.spatial_size,
                    split=conditions.split,
                    protocol=_protocol_text(conditions),
                    # New runs should fill these fields directly from the
                    # quoted source.  Keep the old split/protocol heuristics
                    # only as a compatibility fallback for legacy records.
                    scenes=conditions.scenes or _scenes_text(conditions.split),
                    acquisition=conditions.acquisition or _acquisition_text(conditions),
                    citation=citation,
                )
            )

        # Aggregate one evidence row per cited source.  A paper may support
        # several metrics, but its provenance and team relationship should not
        # be repeated as conflicting rows merely because it has several claims.
        if citation is not None:
            evidence = _evidence_record(
                finding,
                identity,
                citation,
                independence,
                claim_urls,
            )
            previous = evidence_by_url.get(finding.source_url)
            evidence_by_url[finding.source_url] = (
                _merge_evidence_records(previous, evidence) if previous else evidence
            )

    records.extend(evidence_by_url.values())
    return build_hsi_tables(records)


def _table(
    table_id: str,
    title: str,
    columns: Sequence[tuple[str, str, str]],
) -> TableBlock:
    return TableBlock(
        id=table_id,
        title=title,
        columns=[TableColumn(key=key, label=label, unit=unit) for key, label, unit in columns],
        rows=[],
    )


def _rows_for_schema(table: TableBlock, records: Sequence[HsiDomainRecord]) -> list[TableRow]:
    rows: list[TableRow] = []
    seen: dict[str, int] = {}
    for record in records:
        label = _row_label(table.id, record)
        if not label:
            continue
        if label not in seen:
            seen[label] = len(rows)
            rows.append(TableRow(label=label, citation=record.citation))
        row = rows[seen[label]]
        for column in table.columns:
            value = str(getattr(record, column.key, "") or "").strip()
            if not value:
                continue
            current = row.cells.get(column.key)
            citation_list = [record.citation] if record.citation else []
            if not current or not current.reported:
                row.cells[column.key] = TableCell(
                    value=value,
                    citations=citation_list,
                    disputed=False,
                )
                continue

            # A repeated value from another source is corroboration, not a
            # replacement.  Keep every citation on the cell so the table can
            # faithfully expose multi-source evidence.
            citations = list(dict.fromkeys([*current.citations, *citation_list]))
            if not current.disputed and current.value == value:
                row.cells[column.key] = current.model_copy(update={"citations": citations})
                continue

            # Preserve disagreements instead of choosing a winner.  Avoid
            # appending the same display value twice when a disputed cell gets
            # another source that agrees with one of its existing values.
            values = [part.strip() for part in current.value.split(" / ")]
            if value not in values:
                values.append(value)
            row.cells[column.key] = TableCell(
                value=" / ".join(values),
                citations=citations,
                disputed=True,
            )
    return rows


def _row_label(table_id: str, record: HsiDomainRecord) -> str:
    if table_id == OPTICAL_CODING_TABLE_ID or table_id == RECONSTRUCTION_TABLE_ID:
        return record.method.strip()
    if table_id == DATASET_PROTOCOL_TABLE_ID:
        return record.dataset.strip()
    return record.paper.strip()


def _quantity_text(quantity: object) -> str:
    if quantity is None:
        return ""
    rendered = getattr(quantity, "rendered", "") or ""
    value = getattr(quantity, "value", None)
    unit = getattr(quantity, "unit", "") or ""
    comparator = getattr(quantity, "comparator", "") or ""
    uncertainty = getattr(quantity, "uncertainty", None)
    if not rendered and value is None:
        return ""
    text = rendered or f"{value:g}"
    # Keep lower/upper-bound claims distinct from exact values in HSI tables.
    # ``rendered`` is the numeric spelling, so the comparator is added here
    # just as it is in the quantitative comparison table.
    if comparator not in ("", "=") and not text.startswith(comparator):
        text = f"{comparator}{text}"
    if unit and text.casefold().endswith(unit.casefold()):
        formatted = text
    else:
        formatted = f"{text} {unit}".strip()
    if uncertainty is not None:
        formatted += f" ± {uncertainty:g}"
    return formatted


def _metric_kind(quantity: object) -> str:
    metric = str(getattr(quantity, "metric", "") or "").strip().casefold()
    aliases = {
        "peak signal-to-noise ratio": "psnr",
        "peak signal to noise ratio": "psnr",
        "structural similarity": "ssim",
        "spectral angle mapper": "sam",
        "parameter": "parameters",
        "parameter count": "parameters",
        "parameters": "parameters",
        "model parameters": "parameters",
        "参数": "parameters",
        "参数量": "parameters",
        "floating point operations": "flops",
        "floating-point operations": "flops",
        "flop": "flops",
        "flops": "flops",
        "mac": "flops",
        "macs": "flops",
        "计算量": "flops",
        "inference time": "inference_time",
        "inference-time": "inference_time",
        "runtime": "inference_time",
        "latency": "inference_time",
        "推理时间": "inference_time",
    }
    if metric in aliases:
        return aliases[metric]
    # Preserve a small, deterministic vocabulary for common model-output
    # variants (e.g. ``GFLOPs`` or ``number of parameters``) without trying
    # to infer arbitrary user-defined metrics.
    if "parameter" in metric or "参数" in metric:
        return "parameters"
    if metric in {"params", "param", "#params", "模型规模"}:
        return "parameters"
    if "flop" in metric or metric in {"mac", "macs", "计算量"}:
        return "flops"
    if any(token in metric for token in ("inference", "latency", "runtime", "推理", "延迟")):
        return "inference_time"
    return metric


def _merge_source_identity(
    previous: SourceIdentity | None,
    current: SourceIdentity,
) -> SourceIdentity:
    """Combine snapshots for one URL while preserving explicit metadata."""
    if previous is None:
        return current
    authors = list(dict.fromkeys([*previous.authors, *current.authors]))
    retracted: bool | None
    if previous.retracted is True or current.retracted is True:
        retracted = True
    elif previous.retracted is False or current.retracted is False:
        retracted = False
    else:
        retracted = None
    peer_reviewed_values = {
        value for value in (previous.peer_reviewed, current.peer_reviewed) if value is not None
    }
    peer_reviewed: bool | None = (
        next(iter(peer_reviewed_values)) if len(peer_reviewed_values) == 1 else None
    )
    return previous.model_copy(
        update={
            "doi": previous.doi or current.doi,
            "work_id": previous.work_id or current.work_id,
            "title": previous.title or current.title,
            "authors": authors,
            "domain": previous.domain or current.domain,
            "peer_reviewed": peer_reviewed,
            "retracted": retracted,
            "section": previous.section or current.section,
        },
        deep=True,
    )


def _protocol_text(conditions: object) -> str:
    """Render explicitly supplied protocol context without inventing values."""
    if conditions is None:
        return ""
    parts: list[str] = []
    for label, value in (
        ("protocol", getattr(conditions, "protocol", "")),
        ("train_data", getattr(conditions, "train_data", "")),
        ("hardware", getattr(conditions, "hardware", "")),
        ("notes", getattr(conditions, "notes", "")),
    ):
        text = str(value or "").strip()
        if text:
            parts.append(text if label == "protocol" else f"{label}: {text}")
    return "; ".join(parts)


def _scenes_text(split: str) -> str:
    text = split.strip()
    lowered = text.casefold()
    return text if "scene" in lowered or "场景" in text else ""


def _acquisition_text(conditions: object) -> str:
    if conditions is None:
        return ""
    text = " ".join(
        str(getattr(conditions, name, "") or "") for name in ("protocol", "notes")
    ).casefold()
    if any(token in text for token in ("simulat", "synthetic", "仿真", "合成")):
        return "simulated"
    if any(token in text for token in ("real-world", "real world", "real capture", "真实", "实拍")):
        return "real"
    return "unspecified"


def _reported_flag(text: str, token: str) -> str:
    return "reported" if token.casefold() in text.casefold() else ""


def _prototype_validation(text: str) -> str:
    lowered = text.casefold()
    return (
        "reported"
        if any(
            token in lowered
            for token in (
                "prototype",
                "real-world system",
                "real world system",
                "hardware validation",
                "experimental setup",
                "原型",
                "真实系统",
            )
        )
        else ""
    )


def _is_arxiv(identity: SourceIdentity | None) -> bool:
    if identity is None:
        return False
    domain = identity.domain.strip().casefold()
    work_id = identity.work_id.strip().casefold()
    return domain == "arxiv.org" or domain.endswith(".arxiv.org") or work_id.startswith("arxiv:")


def _peer_reviewed_text(identity: SourceIdentity | None) -> str:
    if identity is None or identity.peer_reviewed is None:
        return "unknown"
    return "yes" if identity.peer_reviewed else "no"


def _paper_label(finding: Finding, identity: SourceIdentity | None) -> str:
    if identity is not None and identity.title.strip():
        return identity.title.strip()
    if finding.verification.source_title.strip():
        return finding.verification.source_title.strip()
    if identity is not None and identity.doi.strip():
        return identity.doi.strip()
    if finding.verification.source_reference.strip():
        return finding.verification.source_reference.strip()
    return finding.source_url.strip()


def _evidence_record(
    finding: Finding,
    identity: SourceIdentity | None,
    citation: int,
    independence: IndependenceGraph,
    claim_urls: Mapping[str, str],
) -> HsiDomainRecord:
    verification = finding.verification
    related_urls = [finding.source_url]
    for other_claim in verification.corroborates_claim_ids:
        other_url = claim_urls.get(other_claim)
        if other_url and other_url not in related_urls:
            related_urls.append(other_url)
    return HsiDomainRecord(
        paper=_paper_label(finding, identity),
        independent_works=_independent_work_text(
            verification.corroboration_status,
            verification.independent_source_count,
            related_urls,
            independence,
        ),
        same_team=_same_team_text(finding, related_urls, independence),
        preprint="yes" if _is_arxiv(identity) else "unknown",
        peer_reviewed=_peer_reviewed_text(identity),
        conflict=_conflict_text(finding),
        citation=citation,
    )


def _independent_work_text(
    status: str,
    stored_count: int,
    related_urls: Sequence[str],
    independence: IndependenceGraph,
) -> str:
    if status == "not_checked":
        return "unknown"
    computed = independence.count(list(related_urls))
    if computed > 0:
        return str(computed)
    if stored_count > 0:
        return str(stored_count)
    if status == "single_source":
        return "1"
    return "unknown"


def _same_team_text(
    finding: Finding,
    related_urls: Sequence[str],
    independence: IndependenceGraph,
) -> str:
    verification = finding.verification
    reason = verification.corroboration_reason.casefold()
    if any(
        signal in reason
        for signal in (
            "same_doi",
            "same_work_id",
            "same_title",
            "same_publisher_domain",
            "shared_authors:",
            "same_publisher_cluster",
        )
    ):
        return "yes"
    if not verification.corroborates_claim_ids:
        return "unknown"
    current = related_urls[0] if related_urls else ""
    if not current or current in independence.unidentifiable:
        return "unknown"
    relations = [
        independence.same_publisher(current, other)
        for other in related_urls[1:]
        if other not in independence.unidentifiable
    ]
    if not relations:
        return "unknown"
    if all(relations):
        return "yes"
    if not any(relations):
        return "no"
    return "mixed"


def _conflict_text(finding: Finding) -> str:
    verification = finding.verification
    if (
        verification.consistency_status == "conflicted"
        or verification.corroboration_status == "disputed"
    ):
        return "yes"
    if verification.consistency_status == "clear":
        return "no"
    return "unknown"


def _merge_evidence_records(
    previous: HsiDomainRecord,
    current: HsiDomainRecord,
) -> HsiDomainRecord:
    merged = previous.model_copy(deep=True)
    for field in (
        "independent_works",
        "same_team",
        "preprint",
        "peer_reviewed",
        "conflict",
    ):
        old = str(getattr(merged, field) or "").strip()
        new = str(getattr(current, field) or "").strip()
        if not new or old == new:
            continue
        if not old or old == "unknown":
            setattr(merged, field, new)
        elif new != "unknown" and new not in old.split(" / "):
            setattr(merged, field, f"{old} / {new}")
    return merged


def _category(statement: str) -> str:
    lowered = statement.casefold()
    if any(token in lowered for token in ("diffusion", "generative prior", "生成先验")):
        return "generative prior"
    if any(token in lowered for token in ("unfold", "unrolled", "deep unfolding", "深度展开")):
        return "deep unfolding"
    if any(token in lowered for token in ("transformer", "cnn", "network", "deep")):
        return "model-driven"
    if any(token in lowered for token in ("optimization", "prior", "model-based")):
        return "model-based"
    return "unspecified"


def _looks_optical(finding: Finding) -> bool:
    conditions = finding.conditions
    if conditions is not None and any(
        getattr(conditions, field, "").strip()
        for field in (
            "coding_mode",
            "dispersive_element",
            "calibration",
            "prototype_validation",
        )
    ):
        return True
    text = f"{finding.statement} {finding.evidence_quote}".casefold()
    return any(
        token in text
        for token in (
            "cassi",
            "coded aperture",
            "coded-aperture",
            "coding mask",
            "spectral coding",
            "dispersive",
            "doe",
            "光谱编码",
            "编码掩膜",
        )
    )


def _coding_mode(statement: str) -> str:
    lowered = statement.casefold()
    if "cassi" in lowered:
        return "CASSI"
    if "mask" in lowered or "掩膜" in statement:
        return "coded mask"
    return "unspecified"


def _dispersive_element(statement: str) -> str:
    lowered = statement.casefold()
    for token in ("prism", "grating", "衍射光栅", "棱镜"):
        if token in lowered or token in statement:
            return token
    return ""


__all__ = [
    "OPTICAL_CODING_TABLE_ID",
    "RECONSTRUCTION_TABLE_ID",
    "DATASET_PROTOCOL_TABLE_ID",
    "EVIDENCE_STRENGTH_TABLE_ID",
    "HsiDomainRecord",
    "hsi_table_schemas",
    "build_hsi_tables",
    "hsi_tables_from_results",
]
