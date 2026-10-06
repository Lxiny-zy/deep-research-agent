"""Read existing requirements, document positions and evidence without model calls."""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..persistence.repository import RunDetail
from ..report.document import ChartBlock, ReportDocument, TableBlock
from .contract import contract_from_scratch
from .coverage_review import (
    contract_issue,
    coverage_hash,
    coverage_issues,
    effective_contract,
    material_bases,
    regions,
)
from .delivery.math_markdown import citation_text
from .delivery_store import _digest
from .prose_review import prose_units, reviewer_for_report, stored_review
from .reader import paper_sources

TASK_WORK = {
    "autoResearch": "N1",
    "litReview": "N2",
    "peerReview": "N3",
    "paperRead": "N4",
    "dataAnalysis": "N5",
    "slides": "N6",
    "mindmap": "N7",
}


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_text(value: str, secrets: tuple[str, ...] = ()) -> str:
    """Redact credential-shaped text even in explicitly selected excerpts."""
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", value)
    value = re.sub(
        r"(?i)((?:api[_ -]?key|access[_ -]?token|password|secret|authorization)\s*[:=]\s*)"
        r"[\"']?[^\s,;\"']+",
        r"\1[REDACTED]",
        value,
    )

    def clean_url(match: re.Match[str]) -> str:
        try:
            url = urlsplit(match[0])
            return urlunsplit((url.scheme, url.hostname or "", url.path, "", ""))
        except ValueError:
            return "[URL REDACTED]"

    return re.sub(r"https?://[^\s<>\"']+", clean_url, value)


def _source_id(url: str, content_hash: str) -> str:
    return _digest(["acceptance-source", url, content_hash])


def next_work(template: str, category: str) -> list[str]:
    if category == "context":
        return ["N8"]
    if category == "performance":
        return ["N10"]
    if category == "interaction":
        return ["N9", "N11"]
    if category == "layout":
        return list(dict.fromkeys([TASK_WORK.get(template, "N11"), "N11"]))
    return [TASK_WORK.get(template, "N1")]


def build_context(detail: RunDetail, document: ReportDocument) -> dict[str, Any]:
    scratch = detail.orchestration.checkpoint.get("scratch", {}) if detail.orchestration else {}
    contract = effective_contract(contract_from_scratch(scratch), scratch)
    markdown = detail.report.markdown if detail.report else ""
    citations = detail.report.citations if detail.report else []
    checker = (
        reviewer_for_report(
            None,
            detail.query,
            detail.results,
            citations,
            scratch,
            0,
            sources=detail.sources,
        )
        if detail.report
        else None
    )
    review = stored_review(scratch) or {}
    review = review if isinstance(review, dict) else {}
    bound, review_problems = checker.check(markdown, review) if checker and review else (False, [])
    units, positions = (
        checker.units(markdown)
        if checker
        else prose_units(
            markdown,
            list(range(1, len(citations) + 1)),
        )
    )
    decisions = {item["unit_id"]: item for item in review.get("decisions", [])} if bound else {}
    sources: dict[str, dict[str, Any]] = {}
    by_url: dict[str, list[str]] = {}
    candidates = [*detail.sources, *paper_sources(detail)]
    for result in detail.results:
        if result.extraction_audit:
            candidates.extend(result.extraction_audit.sources)
    for source in candidates:
        content_hash = source.content_hash or text_hash(source.content)
        key = _source_id(source.url, content_hash)
        sources[key] = {
            "id": key,
            "content_hash": content_hash,
            "document_content_hash": source.document_content_hash or None,
            "locator": safe_text(source.locator)[:300],
            "section": safe_text(
                source.section_title or (source.scholarly.section if source.scholarly else "")
            )[:200],
            "part_index": source.document_part_index,
            "part_count": source.document_part_count,
            "reading_status": "text_available" if source.content.strip() else "material_unread",
        }
        by_url.setdefault(source.url, []).append(key)
    evidence: list[dict[str, Any]] = []
    evidence_texts = {}
    for record in document.evidence:
        related = [
            key
            for key in by_url.get(record.source_url, [])
            if not record.content_hash
            or record.content_hash
            in {
                sources[key]["content_hash"],
                sources[key]["document_content_hash"],
            }
        ]
        if not related:
            key = _source_id(record.source_url, record.content_hash)
            sources.setdefault(
                key,
                {
                    "id": key,
                    "content_hash": record.content_hash or None,
                    "document_content_hash": None,
                    "locator": safe_text(record.source_section)[:300],
                    "section": "",
                    "part_index": None,
                    "part_count": None,
                    "reading_status": "evidence_only",
                },
            )
            related = [key]
        evidence_id = record.support_id or _digest([record.citation, record.claim_id, record.quote])
        evidence_texts[evidence_id] = record.quote
        evidence.append(
            {
                "id": evidence_id,
                "citation": record.citation,
                "source_ids": list(dict.fromkeys(related)),
                "verbatim_verified": record.verbatim_verified,
                "semantic_status": record.semantic_status,
                "consistency_status": record.consistency_status,
                "corroboration_status": record.corroboration_status,
                "quote_sha256": text_hash(record.quote),
                "source_binding": "content_hash" if record.content_hash else "url_only",
            }
        )
    by_citation: dict[int, list[dict]] = {}
    for entry in evidence:
        by_citation.setdefault(entry["citation"], []).append(entry)
    locations = []
    texts = {}

    def add_location(
        kind: str, key: str, text: str, pointer: dict, cited: list[int], state: str
    ) -> str:
        identity = _digest([document.content_version, kind, key])
        selected = [e for citation in cited for e in by_citation.get(citation, [])]
        locations.append(
            {
                "id": identity,
                "kind": kind,
                "pointer": pointer,
                "preview": safe_text(text)[:180],
                "text_sha256": text_hash(text),
                "citations": cited,
                "evidence_ids": list(dict.fromkeys(e["id"] for e in selected)),
                "source_ids": list(dict.fromkeys(s for e in selected for s in e["source_ids"])),
                "verification_status": state,
            }
        )
        texts[identity] = text
        return identity

    unit_ids = {}
    for unit, position in zip(units, positions, strict=True):
        decision = decisions.get(unit.id)
        state = (
            "review_failed"
            if review_problems
            else decision["verdict"]
            if decision
            else "not_checked"
        )
        unit_ids[unit.id] = add_location(
            "prose",
            unit.id,
            unit.text,
            {
                "origin": "normalized_report_body",
                **position,
                "section": safe_text(str(position.get("section", "")))[:200],
            },
            unit.citations,
            state,
        )
    region_ids = {}
    for region in regions(markdown):
        cited = sorted(
            {
                int(n)
                for group in re.findall(
                    r"\[(\d+(?:\s*[,，]\s*\d+)*)\]", citation_text(region["text"])
                )
                for n in re.split(r"\s*[,，]\s*", group)
            }
        )
        region_ids[region["id"]] = add_location(
            "coverage_region",
            region["id"],
            region["text"],
            {"origin": "coverage_review", "region_id": region["id"], "kind": region["kind"]},
            cited,
            "not_checked",
        )
    for index, block in enumerate(document.blocks):
        if isinstance(block, TableBlock):
            cited = sorted(
                {
                    c
                    for row in block.rows
                    for cell in row.cells.values()
                    for c in (cell.citations or ([row.citation] if row.citation else []))
                }
            )
            add_location(
                "table",
                block.id,
                block.title,
                {"origin": "document.blocks", "index": index, "table_id": block.id},
                cited,
                "not_checked",
            )
            for row_index, row in enumerate(block.rows):
                for column, cell in row.cells.items():
                    add_location(
                        "table_cell",
                        f"{block.id}/{row_index}/{column}",
                        cell.value,
                        {
                            "origin": "document.blocks",
                            "table_id": block.id,
                            "row": row_index,
                            "column": column,
                        },
                        cell.citations or ([row.citation] if row.citation else []),
                        "disputed" if cell.disputed else "not_checked",
                    )
        elif isinstance(block, ChartBlock):
            table = document.table(block.source_table)
            cited = (
                sorted(
                    {
                        c
                        for row in table.rows
                        for column, cell in row.cells.items()
                        if column in {*block.value_columns, block.x_column}
                        for c in (cell.citations or ([row.citation] if row.citation else []))
                    }
                )
                if table
                else []
            )
            add_location(
                "chart",
                block.id,
                block.caption or block.title,
                {
                    "origin": "document.blocks",
                    "index": index,
                    "chart_id": block.id,
                    "source_table": block.source_table,
                },
                cited,
                "not_checked",
            )
    bases = material_bases(scratch, review if bound else {}, units)
    coverage = review.get("requirements_review", {}) if isinstance(review, dict) else {}
    coverage = coverage if isinstance(coverage, dict) else {}
    coverage_bound = bool(
        contract
        and not contract_issue(contract)
        and coverage.get("input_hash") == coverage_hash(contract, markdown, bases)
    )
    coverage_decisions = (
        {item["requirement_id"]: item for item in coverage.get("decisions", [])}
        if coverage_bound
        else {}
    )
    requirements = []
    for item in contract.requested_items if contract else []:
        decision = coverage_decisions.get(item.id)
        status = decision.get("status", "not_checked") if decision else "not_checked"
        requirements.append(
            {
                "id": item.id,
                "kind": item.kind,
                "label": safe_text(item.label)[:240],
                "status": status,
                "review_bound": coverage_bound,
                "gap_kind": {
                    "missing": "writing_omission",
                    "partial": "partial_coverage",
                    "insufficient": "evidence_insufficient",
                }.get(status),
                "location_ids": list(
                    dict.fromkeys(
                        region_ids[x["region_id"]]
                        for x in decision.get("locations", [])
                        if x["region_id"] in region_ids
                    )
                )
                if decision
                else [],
                "basis_ids": decision.get("basis_ids", []) if decision else [],
            }
        )
    from .presentation_notes import presentation_sections

    for section in presentation_sections(detail):
        add_location(
            "presentation_note", section["id"], section["markdown"],
            {"origin": section["origin"], "section_id": section["id"], "title": section["title"]},
            [], section["verification_status"],
        )
    return {
        "schema_version": 1,
        "run_id": detail.id,
        "document_version": document.content_version,
        "source_version": document.source_version,
        "template": contract.template if contract else None,
        "input_id": contract.requested_input_hash if contract else _digest(detail.query),
        "observed_status": detail.status,
        "human_conclusion": "pending",
        "requirements": requirements,
        "locations": locations,
        "sources": list(sources.values()),
        "evidence": evidence,
        "material_status": "material_unread"
        if not sources and bases
        else "no_material"
        if not sources
        else "material_unread"
        if all(s["reading_status"] == "material_unread" for s in sources.values())
        else "material_available",
        "coverage_review_bound": coverage_bound,
        "prose_review_bound": bound,
        "prose_review_issues": [safe_text(issue)[:300] for issue in review_problems],
        "coverage_issues": [
            safe_text(x)[:300] for x in coverage_issues(contract, markdown, coverage, bases)
        ]
        if contract
        else [],
        "material_limits": [{"id": b["id"], "kind": b["kind"]} for b in bases],
        "_texts": texts,
        "_evidence_texts": evidence_texts,
    }
