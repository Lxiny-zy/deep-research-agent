"""Locate QA defects without delaying review of the remaining answer."""

from __future__ import annotations

import re
from typing import Any

from ..report.validation import ReportCheck, _excerpt
from .delivery.markdown import _parser
from .delivery.math_markdown import validation_paragraphs
from .prose_review import ProseReviewer
from .support import SupportUnit, digest


def mechanical_deferrals(
    check: ReportCheck, units: list[SupportUnit], locations: list[dict[str, Any]]
) -> dict[str, str]:
    """Map the validator's paragraph excerpts back to exact original line spans.

    Math validation may join blank lines; whitespace-free offsets preserve the
    original locations. Duplicate excerpts conservatively defer every match.
    """
    if not check.issues:
        return {}
    line_numbers = [
        number
        for number, line in enumerate(check.body.splitlines(), 1)
        for character in line
        if not character.isspace()
    ]
    compact = re.sub(r"\s+", "", check.body)
    paragraphs: list[tuple[str, int, int]] = []
    cursor = 0
    for paragraph in validation_paragraphs(check.body):
        needle = re.sub(r"\s+", "", paragraph)
        if not needle:
            continue
        start = compact.find(needle, cursor)
        if start < 0:
            return {unit.id: "机械问题未能安全定位，需修订后核验" for unit in units}
        cursor = start + len(needle)
        content = "\n".join(
            line for line in paragraph.splitlines() if not line.lstrip().startswith("#")
        )
        paragraphs.append((_excerpt(content), line_numbers[start], line_numbers[cursor - 1]))
    deferred: dict[str, str] = {}
    for code, excerpt, reason in check.problems:
        if code == "invalid_citation":
            affected = {
                unit.id
                for unit in units
                if not set(unit.citations).issubset(check.evidence_by_citation)
            }
        else:
            regions = [
                (start, end) for text, start, end in paragraphs if excerpt and text == excerpt
            ]
            affected = {
                location["id"]
                for location in locations
                if any(
                    start <= location["end_line"] and end >= location["start_line"]
                    for start, end in regions
                )
            }
        if not affected:
            # A global or unlocatable defect must not become a positive review.
            affected = {unit.id for unit in units}
        for uid in affected:
            deferred[uid] = reason
    if not check.problems:
        deferred = {unit.id: "机械检查未通过" for unit in units}
    return deferred


def claim_problems(audit: dict[str, Any]) -> dict[str, str]:
    deferred = set(audit.get("deferred_units", []))
    return {
        decision["unit_id"]: decision["reason"]
        for decision in audit.get("decisions", [])
        if decision["unit_id"] not in deferred
        and decision["verdict"] not in {"supported", "non_factual"}
    }


def draft_rank(check: ReportCheck, audit: dict[str, Any]) -> tuple[int, int, int]:
    """Prefer fewer defects, then more already verified content; keep earlier ties."""
    failures = len(check.problems or check.issues) + len(claim_problems(audit))
    if audit["status"] != "pass" and not failures:
        failures = 1
    approved = {
        decision["unit_id"]
        for decision in audit.get("decisions", [])
        if decision["verdict"] in {"supported", "non_factual"}
        and decision["unit_id"] not in audit.get("deferred_units", [])
    }
    lines = check.body.splitlines(keepends=True)
    length = sum(
        sum(map(len, lines[unit["start_line"] - 1 : unit["end_line"]]))
        for unit in audit.get("units", [])
        if unit["id"] in approved
    )
    # Deleting all checked content is not a quality improvement.
    return int(length == 0), failures, -length


def partial_answer(
    check: ReportCheck, audit: dict[str, Any], reviewer: ProseReviewer
) -> tuple[str, dict[str, Any]] | None:
    """Replace only bound failing regions, without blessing the incomplete answer."""
    bound, issues = reviewer.check(check.body, audit)
    if not bound:
        return None
    units, locations = reviewer.units(check.body)
    bad = set(mechanical_deferrals(check, units, locations)) | set(claim_problems(audit))
    # Extra structural/protocol failures cannot be safely localized by a label.
    if not bad or len(issues) > len(bad):
        return None
    spans = [
        (location["start_line"] - 1, location["end_line"])
        for location in locations
        if location["id"] in bad
    ]
    # A rejected table row must not leave half a Markdown table behind.
    for token in _parser().parse(check.body):
        if token.type == "table_open" and token.map:
            start, end = token.map
            if any(a < end and b > start for a, b in spans):
                spans.append((start, end))
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    retained = {
        location["id"]
        for location in locations
        if not any(
            start < location["end_line"] and end > location["start_line"] - 1
            for start, end in merged
        )
    }
    decisions = {decision["unit_id"]: decision for decision in audit["decisions"]}
    if not any(
        unit.id in retained
        and not unit.text.lstrip().startswith("#")
        and decisions[unit.id]["verdict"] == "supported"
        for unit in units
    ):
        return None
    lines = check.body.splitlines(keepends=True)
    for start, end in reversed(merged):
        original = "".join(lines[start:end])
        trailing = original[len(original.rstrip()) :]
        marker = re.match(r"^[ \t]*(?:[-+*]|\d+[.)])\s+", original)
        prefix = marker[0] if marker else ""
        lines[start:end] = [prefix + "**此段内容未通过核验，暂不作为结论。**" + trailing]
    answer = "".join(lines)
    return answer, {
        "status": "partial",
        "source_input_hash": audit["input_hash"],
        "output_hash": digest(answer),
        "retained_units": sorted(retained),
        "replaced_regions": [{"start_line": a + 1, "end_line": b} for a, b in merged],
    }
