"""Attach per-occurrence evidence selections only from a bound final review."""

from __future__ import annotations

import re
from typing import Any

from ..bibliography import Bibliography, CitationOccurrence, citation_runs, project_citations
from .prose_review import ProseReviewer, body_text
from .support import digest


def bind_review(
    catalog: Bibliography, reviewer: ProseReviewer | None, markdown: str, record: Any
) -> bool:
    catalog.occurrences = []
    catalog.binding_status = "unavailable"
    catalog.body = project_citations(catalog.source_body, catalog)
    if reviewer is None or record is None:
        return False
    bound, issues = reviewer.check(markdown, record)
    if not bound or issues or record.get("status") != "pass":
        catalog.binding_status = "invalid"
        return False
    checked_body = body_text(markdown)
    if body_text(catalog.source_body) != checked_body:
        catalog.binding_status = "invalid"
        return False
    checked_runs = list(citation_runs(checked_body))
    display_runs = list(citation_runs(catalog.source_body))
    if [re.findall(r"\d+", run[0]) for run in checked_runs] != [
        re.findall(r"\d+", run[0]) for run in display_runs
    ]:
        catalog.binding_status = "invalid"
        return False
    offsets = [0]
    for line in checked_body.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    _, positions = reviewer.units(markdown)
    regions = [(offsets[p["start_line"] - 1], offsets[p["end_line"]], p["id"]) for p in positions]
    decisions = {item["unit_id"]: item for item in record["decisions"]}
    evidence = {item["id"]: item for item in reviewer.evidence if item["citation"] > 0}
    documents = {item.index: item.document for item in catalog.locations}
    for ordinal, run in enumerate(checked_runs):
        units = [uid for start, end, uid in regions if start <= run.start() and run.end() <= end]
        uid = units[0] if len(units) == 1 else ""
        decision = decisions.get(uid)
        groups: dict[int, list[int]] = {}
        for location in map(int, re.findall(r"\d+", run[0])):
            if location not in documents:
                catalog.binding_status = "invalid"
                catalog.occurrences = []
                return False
            group = groups.setdefault(documents[location], [])
            if location not in group:
                group.append(location)
        for document, locations in groups.items():
            selected = []
            scope = "source_location"
            review_note = ""
            if decision and decision["verdict"] == "supported":
                selected = list(
                    dict.fromkeys(
                        eid
                        for eid in decision["evidence_ids"]
                        if eid in evidence and evidence[eid]["citation"] in locations
                    )
                )
                scope = "reviewed_unit" if selected else "unused_location"
            fulltext = decision.get("fulltext_review") if decision else None
            if isinstance(fulltext, dict) and fulltext.get("status") in {
                "absence_confirmed",
                "critique_supported",
            }:
                targets = set((fulltext.get("target") or {}).get("document_ids", []))
                if any(
                    reviewer.reviewer.fulltext_corpus.citations.get(location) in targets
                    for location in locations
                ):
                    scope = "fulltext_review"
                    review_note = "全文核查：" + str(fulltext["reason"])
                    passages = list(
                        dict.fromkeys(
                            f"{row['source']} {row['locator']}「{row['quote']}」"
                            for row in fulltext.get("scanned", [])
                            if row["verdict"] == "supports"
                        )
                    )
                    if passages:
                        review_note += "；" + "；".join(passages)
            formula_review = decision.get("formula_review") if decision else None
            if isinstance(formula_review, dict) and formula_review.get("status") == "pass":
                passages = list(
                    dict.fromkeys(
                        str(evidence[row["source_id"]]["source"])
                        + "「"
                        + row["source_quote"]
                        + "」"
                        + (
                            "（条件等价："
                            + row["condition_quote"]
                            + "；"
                            + row["equivalence_explanation"]
                            + "）"
                            if row.get("relation") == "conditional"
                            else ""
                        )
                        for row in formula_review["decisions"]
                        if row["verdict"] == "matched" and row["source_id"] in selected
                    )
                )
                if passages:
                    review_note += (
                        ("；" if review_note else "") + "公式核查原文：" + "；".join(passages)
                    )
            catalog.occurrences.append(
                CitationOccurrence(
                    id=digest(
                        [
                            record["input_hash"],
                            uid,
                            ordinal,
                            document,
                            locations,
                            scope,
                            selected,
                            review_note,
                        ]
                    )[:24],
                    run=ordinal,
                    document=document,
                    locations=locations,
                    unit_id=uid,
                    scope=scope,
                    evidence_ids=selected,
                    review_note=review_note,
                )
            )
    catalog.binding_status = "bound"
    catalog.body = project_citations(catalog.source_body, catalog)
    return True
