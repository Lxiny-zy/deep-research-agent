"""Version-bound reading views over existing reviews, source text and evidence IDs."""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from ..guardrails import _normalize_text, _normalize_with_offsets
from ..persistence.repository import RunDetail
from .acceptance_context import _source_id, safe_text
from .attachments import ATTACHMENT_URL_PREFIX, attachments_from_scratch
from .prose_review import stored_review
from .reader import paper_sources, reader_documents
from .support import digest


class ReadingUnitPreview(BaseModel):
    id: str
    unit_id: str
    section: str
    preview: str
    verification_status: str
    evidence_count: int
    kind: str = "prose"
    peer_type: str | None = None
    severity: str | None = None


class ReadingMapPage(BaseModel):
    document_version: str
    source_version: str
    template: str | None
    review_bound: bool
    review_issues: list[str]
    total: int
    offset: int
    limit: int
    units: list[ReadingUnitPreview]
    focus_items: list[dict[str, Any]]
    focus_total: int
    focus_truncated: bool
    peer_review: dict[str, Any] | None = None


class ReadingAnchor(BaseModel):
    id: str
    evidence_id: str
    source_url: str
    document_id: str | None
    pdf_available: bool = False
    page_hint: int | None
    locator: str
    source_hash: str
    start: int
    end: int
    match_kind: str
    quote: str
    quote_truncated: bool
    quote_redacted: bool = False
    quote_total_chars: int
    context_before: str
    context_after: str
    source_binding: str
    coordinate_system: str = "source_text_characters_not_pdf_geometry"


class ReadingUnitDetail(BaseModel):
    document_version: str
    unit: ReadingUnitPreview
    text: str
    text_offset: int
    text_total_chars: int
    text_truncated: bool
    text_redacted: bool = False
    review_bound: bool
    anchors: list[ReadingAnchor]
    anchor_offset: int
    anchor_total: int
    anchor_limit: int
    evidence_status: list[dict[str, Any]]
    evidence_status_total: int = 0
    evidence_status_truncated: bool = False
    peer_item: dict[str, Any] | None = None
    measurement_context: list[dict[str, Any]] = Field(default_factory=list)
    fulltext_passages: list[dict[str, Any]] = Field(default_factory=list)
    fulltext_status: str | None = None
    fulltext_total: int = 0
    fulltext_offset: int = 0
    fulltext_limit: int = 8


def _scratch(detail: RunDetail) -> dict[str, Any]:
    return detail.orchestration.checkpoint.get("scratch", {}) if detail.orchestration else {}


def _review(detail: RunDetail, context: dict[str, Any]) -> dict[str, Any]:
    # context is built by the authenticated, current-version _context helper.
    # Existence of a saved review alone is never enough to expose its bindings.
    return (stored_review(_scratch(detail)) or {}) if context["prose_review_bound"] else {}


def _locations(context: dict[str, Any]) -> list[dict[str, Any]]:
    return [location for location in context["locations"] if location["kind"] == "prose"]


def _selected(location: dict[str, Any], review: dict[str, Any]) -> list[str]:
    unit_id = location["pointer"].get("id", "")
    decision = next((d for d in review.get("decisions", []) if d["unit_id"] == unit_id), None)
    return (
        list(decision.get("evidence_ids", []))
        if decision and decision["verdict"] == "supported"
        else []
    )


def _preview(location: dict[str, Any], review: dict[str, Any]) -> ReadingUnitPreview:
    uid = location["pointer"].get("id", location["id"])
    decision = next((d for d in review.get("decisions", []) if d["unit_id"] == uid), None)
    peer: dict[str, Any] = next(
        (item for item in review.get("peer_review", {}).get("items", []) if item["id"] == uid), {}
    )
    text = location["preview"]
    kind = (
        "formula"
        if re.search(r"\$|\\\(|\\\[", text)
        else "figure"
        if re.search(r"图\s*\d|Fig(?:ure)?\.?\s*\d", text, re.I)
        else "prose"
    )
    if location["kind"] != "prose":
        kind = location["kind"]
    return ReadingUnitPreview(
        id=location["id"],
        unit_id=uid,
        section=location["pointer"].get("section", ""),
        preview=text,
        verification_status=decision["verdict"] if decision else location["verification_status"],
        evidence_count=len(_selected(location, review)),
        kind=kind,
        peer_type=peer.get("type"),
        severity=peer.get("severity"),
    )


def peer_summary(detail: RunDetail, review: dict[str, Any]) -> dict[str, Any] | None:
    scratch = _scratch(detail)
    if scratch.get("workbench", {}).get("template") != "peerReview":
        return None
    from .peer_coverage import coverage_issues
    from .review_coverage import REVIEW_COVERAGE_KEY

    coverage = scratch.get(REVIEW_COVERAGE_KEY, {})
    coverage = coverage if isinstance(coverage, dict) else {}
    problems = coverage_issues(scratch, detail.results)
    documents = coverage.get("documents", [])
    documents = documents if isinstance(documents, list) else []
    sections = [
        {
            "id": s["id"],
            "title": safe_text(str(s.get("title") or "未记录"))[:200],
            "status": s.get("status") if not problems else "not_bound",
            "missing_topics": [safe_text(str(x))[:300] for x in s.get("missing_topics", [])[:8]]
            if isinstance(s.get("missing_topics", []), list)
            else ["覆盖说明未记录"],
        }
        for d in documents
        if isinstance(d, dict) and isinstance(d.get("sections", []), list)
        for s in d.get("sections", [])
        if isinstance(s, dict) and isinstance(s.get("id"), str)
    ]
    peer = review.get("peer_review", {})
    return {
        "coverage_bound": not problems,
        "coverage_issues": problems[:20],
        "coverage": sections[:100],
        "coverage_total": len(sections),
        "coverage_truncated": len(sections) > 100,
        "score": peer.get("score"),
        "score_status": peer.get("status", "not_bound"),
        "score_ceiling": peer.get("score_ceiling"),
        "score_item_ids": peer.get("score_item_ids", [])[:100],
        "critical_count": peer.get("critical_count"),
        "groups": peer.get("groups", [])[:100],
        "group_total": len(peer.get("groups", [])),
        "scoring_note": "意见用于核对评分；评分仍是评审判断，不是自动加权计算结果。",
    }


def reading_page(
    detail: RunDetail, context: dict[str, Any], *, offset: int = 0, limit: int = 20
) -> ReadingMapPage:
    if offset < 0 or not 1 <= limit <= 50:
        raise ValueError("阅读列表分页范围无效")
    review = _review(detail, context)
    locations = _locations(context)
    return ReadingMapPage(
        document_version=context["document_version"],
        source_version=context["source_version"],
        template=context.get("template"),
        review_bound=context["prose_review_bound"],
        review_issues=context.get("prose_review_issues", [])[:20],
        total=len(locations),
        offset=offset,
        limit=limit,
        units=[_preview(x, review) for x in locations[offset : offset + limit]],
        focus_items=context.get("requirements", [])[:40],
        focus_total=len(context.get("requirements", [])),
        focus_truncated=len(context.get("requirements", [])) > 40,
        peer_review=peer_summary(detail, review),
    )


def _sources(detail: RunDetail) -> list[Any]:
    sources = [*detail.sources, *paper_sources(detail)]
    for result in detail.results:
        if result.extraction_audit:
            sources.extend(result.extraction_audit.sources)
    return list(
        {(s.url, hashlib.sha256(s.content.encode()).hexdigest()): s for s in sources}.values()
    )


def _document(detail: RunDetail, url: str, locator: str) -> tuple[str | None, int | None]:
    documents = reader_documents(detail)
    page = None
    if url.startswith(ATTACHMENT_URL_PREFIX):
        from urllib.parse import parse_qs

        parsed = urlsplit(url)
        attachment_id = parsed.path.rsplit("/", 1)[-1]
        attachment = next(
            (a for a in attachments_from_scratch(_scratch(detail)) if a.id == attachment_id), None
        )
        if attachment is not None:
            try:
                ordinal = int(parse_qs(parsed.query)["chunk"][0]) - 1
                page = next((c.page for c in attachment.chunks if c.ordinal == ordinal), None)
            except (KeyError, ValueError, IndexError):
                pass
            return "att-" + attachment_id, page
    if match := re.search(r"(?:第\s*|\bp(?:age)?\.?\s*)(\d+)\s*(?:页|\b)", locator, re.I):
        page = int(match[1])
    canonical = url.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    matches = [d["id"] for d in documents if d.get("url", "").rstrip("/") == canonical]
    return (matches[0] if len(matches) == 1 else None), page


def reading_unit(
    detail: RunDetail,
    context: dict[str, Any],
    location_id: str,
    *,
    text_offset: int = 0,
    text_length: int = 1600,
    anchor_offset: int = 0,
    anchor_limit: int = 8,
    fulltext_offset: int = 0,
    fulltext_limit: int = 8,
) -> ReadingUnitDetail:
    if (
        min(text_offset, anchor_offset) < 0
        or not 1 <= text_length <= 2400
        or not 1 <= anchor_limit <= 12
        or fulltext_offset < 0
        or not 1 <= fulltext_limit <= 12
    ):
        raise ValueError("阅读详情分页范围无效")
    location = next(
        (
            x
            for x in context["locations"]
            if x["id"] == location_id
            and x["kind"] in {"prose", "coverage_region", "presentation_note"}
        ),
        None,
    )
    if location is None:
        raise KeyError("该版本中不存在所选正文位置")
    review = _review(detail, context)
    selected = _selected(location, review)
    evidence = {e["id"]: e for e in context["evidence"]}
    quotes = context["_evidence_texts"]
    sources = _sources(detail)
    anchors = []
    anchor_total = 0
    statuses = []
    for eid in selected:
        record = evidence.get(eid)
        quote = quotes.get(eid, "")
        count_before = anchor_total
        if record is None or not quote:
            statuses.append(
                {"evidence_id": eid, "status": "evidence_not_available", "candidates": 0}
            )
            continue
        source_ids = set(record["source_ids"])
        allowed = [s for s in context["sources"] if s["id"] in source_ids]
        hashes = {s.get("content_hash") for s in allowed if s.get("content_hash")}
        for source in sources:
            raw_hash = hashlib.sha256(source.content.encode()).hexdigest()
            source_key = _source_id(source.url, source.content_hash or raw_hash)
            if source_key not in source_ids or (
                raw_hash not in hashes and source.document_content_hash not in hashes
            ):
                continue
            normalized, offsets = _normalize_with_offsets(source.content)
            needle = _normalize_text(quote)
            start = 0
            while needle and (found := normalized.find(needle, start)) >= 0:
                left, right = offsets[found], offsets[found + len(needle) - 1] + 1
                anchor_total += 1
                if not anchor_offset <= anchor_total - 1 < anchor_offset + anchor_limit:
                    start = found + max(1, len(needle))
                    continue
                document_id, page_hint = _document(detail, source.url, source.locator)
                anchors.append(
                    ReadingAnchor(
                        id=digest([context["document_version"], eid, raw_hash, left, right]),
                        evidence_id=eid,
                        source_url=safe_text(source.url),
                        document_id=document_id,
                        pdf_available=any(
                            d["id"] == document_id and d.get("pdf")
                            for d in reader_documents(detail)
                        ),
                        page_hint=page_hint,
                        locator=safe_text(source.locator)[:300],
                        source_hash=raw_hash,
                        start=left,
                        end=right,
                        match_kind="exact" if source.content[left:right] == quote else "normalized",
                        quote=safe_text(quote[:2400]),
                        quote_truncated=len(quote) > 2400,
                        quote_redacted=safe_text(quote[:2400]) != quote[:2400],
                        quote_total_chars=len(quote),
                        context_before=safe_text(source.content[max(0, left - 120) : left]),
                        context_after=safe_text(source.content[right : right + 120]),
                        source_binding=record["source_binding"],
                    )
                )
                start = found + max(1, len(needle))
        count = anchor_total - count_before
        statuses.append(
            {
                "evidence_id": eid,
                "status": "ambiguous" if count > 1 else "located" if count else "not_located",
                "candidates": count,
            }
        )
    uid = location["pointer"].get("id", location_id)
    peer = next((i for i in review.get("peer_review", {}).get("items", []) if i["id"] == uid), None)
    text = context["_texts"][location_id]
    from .support import evidence_records

    material = (
        {
            e["id"]: e
            for e in evidence_records(
                detail.results, {u: i for i, u in enumerate(detail.report.citations, 1)}
            )
        }
        if detail.report
        else {}
    )
    fulltext: list[dict[str, Any]] = []
    fulltext_status = None
    decision = next((d for d in review.get("decisions", []) if d["unit_id"] == uid), None)
    if decision and decision.get("fulltext_review") and detail.report:
        from ..report.service import requires_corroboration
        from .fulltext_review import located_passages, validate_fulltext_record
        from .prose_review import reviewer_for_report

        checker = reviewer_for_report(
            None,
            detail.query,
            detail.results,
            detail.report.citations,
            _scratch(detail),
            0,
            corroboration=requires_corroboration(detail),
            sources=detail.sources,
        )
        unit = (
            next((u for u in checker.units(detail.report.markdown)[0] if u.id == uid), None)
            if checker
            else None
        )
        if unit and checker:
            record = decision["fulltext_review"]
            corpus = checker.reviewer.fulltext_corpus
            if validate_fulltext_record(unit, record, corpus) is None:
                fulltext_status = record.get("status")
                fulltext = located_passages(unit, record, corpus)
            else:
                fulltext_status = "not_bound"
    shown_passages = []
    for passage in fulltext[fulltext_offset : fulltext_offset + fulltext_limit]:
        document_id, page_hint = _document(detail, passage["source"], passage["locator"])
        quote = passage["quote"]
        shown_passages.append(
            {
                **{
                    key: passage[key]
                    for key in ("source_hash", "start", "end", "verdict", "review_status")
                },
                "source": safe_text(passage["source"]),
                "locator": safe_text(passage["locator"]),
                "quote": safe_text(quote[:2400]),
                "quote_truncated": len(quote) > 2400,
                "quote_total_chars": len(quote),
                "quote_redacted": safe_text(quote[:2400]) != quote[:2400],
                "document_id": document_id,
                "page_hint": page_hint,
                "pdf_available": any(
                    d["id"] == document_id and d.get("pdf") for d in reader_documents(detail)
                ),
                "coordinate_system": "source_text_characters_not_pdf_geometry",
            }
        )
    return ReadingUnitDetail(
        document_version=context["document_version"],
        unit=_preview(location, review),
        text=safe_text(text[text_offset : text_offset + text_length]),
        text_offset=text_offset,
        text_total_chars=len(text),
        text_truncated=text_offset > 0 or len(text) > text_offset + text_length,
        text_redacted=safe_text(text[text_offset : text_offset + text_length])
        != text[text_offset : text_offset + text_length],
        review_bound=context["prose_review_bound"],
        anchors=anchors,
        anchor_offset=anchor_offset,
        anchor_total=anchor_total,
        anchor_limit=anchor_limit,
        evidence_status=statuses[:50],
        evidence_status_total=len(statuses),
        evidence_status_truncated=len(statuses) > 50,
        peer_item={
            key: peer.get(key)
            for key in (
                "id",
                "type",
                "severity",
                "reason",
                "action",
                "action_ready",
                "evidence_ids",
            )
        }
        if peer
        else None,
        measurement_context=[
            {"evidence_id": eid, **material[eid]["measurement_context"]}
            for eid in dict.fromkeys(anchor.evidence_id for anchor in anchors)
            if eid in material and "measurement_context" in material[eid]
        ],
        fulltext_passages=shown_passages,
        fulltext_status=fulltext_status,
        fulltext_total=len(fulltext),
        fulltext_offset=fulltext_offset,
        fulltext_limit=fulltext_limit,
    )
