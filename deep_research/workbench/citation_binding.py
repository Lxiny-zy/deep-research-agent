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
            if decision and decision["verdict"] == "supported":
                selected = list(
                    dict.fromkeys(
                        eid
                        for eid in decision["evidence_ids"]
                        if eid in evidence and evidence[eid]["citation"] in locations
                    )
                )
                scope = "reviewed_unit" if selected else "unused_location"
            catalog.occurrences.append(
                CitationOccurrence(
                    id=digest(
                        [record["input_hash"], uid, ordinal, document, locations, scope, selected]
                    )[:24],
                    run=ordinal,
                    document=document,
                    locations=locations,
                    unit_id=uid,
                    scope=scope,
                    evidence_ids=selected,
                )
            )
    catalog.binding_status = "bound"
    catalog.body = project_citations(catalog.source_body, catalog)
    return True
