"""Reject obvious mismatches in selected evidence; semantic review remains required."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .delivery.math_markdown import replace_citations
from .support_numbers import normalize_scientific_numbers

_CITATIONS = re.compile(r"\[(\d+(?:\s*[,，]\s*\d+)*)\]")
_CITATION_GROUP = re.compile(r"\[\d+(?:\s*[,，]\s*\d+)*\](?:\s*\[\d+(?:\s*[,，]\s*\d+)*\])*")
_NAMES = re.compile(
    r"(?<![A-Za-z0-9\\])(?:[A-Z]{2,}[A-Za-z0-9]*|[A-Z][a-z]{2,}[A-Za-z0-9]*)(?:\+\+)?"
)
_ORDINARY = frozenset(
    "the this these those each all using from with without where when which while "
    "however therefore hence for and but not our figure fig table equation eq "
    "claim evidence original summary introduction methods method results result "
    "discussion conclusion conclusions limitation limitations data note given "
    "according abstract appendix section chapter performance accuracy training optimization "
    "classification prediction evaluation comparison experimental experiment overall average "
    "total sample group method model network algorithm input output paragraph slide page".split()
)


def _names(text: str, evidence: list[dict[str, Any]]) -> set[str]:
    text = unicodedata.normalize("NFKC", replace_citations(text, lambda _: ""))
    names = {match[0].casefold() for match in _NAMES.finditer(text)} - _ORDINARY
    # Explicit extraction entities also cover non-Latin proper names without
    # treating arbitrary Chinese word overlap as evidence support.
    for record in evidence:
        entity = record.get("entity")
        if isinstance(entity, str) and entity.strip() and entity.casefold() in text.casefold():
            names.add(entity.casefold())
    return names


def _contains(text: str, name: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", text) is not None


def _numeric_text(text: str) -> str:
    from ..report.validation import _citation_text

    text = normalize_scientific_numbers(text)
    text = re.sub(r"(?m)^\s*#{1,6}\s+", "", text)
    # Additional document-position labels, not observations or measured values.
    labels = r"\b(?:paragraph|section|chapter|slide|page)\s+\d+(?:\.\d+)*\b|第\s*\d+\s*页"
    for match in reversed(list(re.finditer(labels, _citation_text(text), re.I))):
        text = text[: match.start()] + " " * len(match[0]) + text[match.end() :]
    return text


def _segments(text: str) -> list[str]:
    from ..report.validation import _citation_text

    # Keep consecutive [1][2] as a joint scope, but do not let the next
    # assertion borrow it merely because a conjunction replaced punctuation.
    boundaries = [0, *(m.end() for m in _CITATION_GROUP.finditer(_citation_text(text))), len(text)]
    parts = [
        part
        for start, end in zip(boundaries, boundaries[1:], strict=False)
        for part in re.split(r"[。！？；\n]|(?<!\d)\.(?=\s|$)", text[start:end])
        if part.strip(" ,，.;；")
    ]
    sentences: list[str] = []
    for part in parts:
        # Citations after sentence-final punctuation still bind that sentence.
        if sentences and not replace_citations(part, lambda _: "").strip():
            sentences[-1] += " " + part
        else:
            sentences.append(part)
    return sentences


def numeric_fact(text: str) -> bool:
    """A cited numerical assertion cannot be exempted as report layout.

    Explicit questions, proposals and subjective scores remain reviewable as
    non-factual. This guard is not a general natural-language fact classifier.
    """
    from ..report.validation import _citation_text, _numbers

    for segment in _segments(text):
        if not _CITATIONS.search(_citation_text(segment)):
            continue
        # A proposal in an adjacent clause must not exempt a numerical fact.
        # Strip citation groups first and retain thousands separators.
        for clause in re.split(
            r"(?<!\d)[,，]|[,，](?!\d)", replace_citations(segment, lambda _: "")
        ):
            if re.search(
                r"是否|能否|建议|考虑|主观|评分\s*[:：]|如果|假设"
                r"|\b(?:whether|should|recommend|suggest|if|assuming)\b",
                _citation_text(clause),
                re.I,
            ):
                continue
            if _numbers(_numeric_text(clause)):
                return True
    return False


def alignment_issue(
    text: str,
    citations: list[int],
    ids: list[str],
    evidence: list[dict[str, Any]],
    *,
    check_numbers: bool = True,
    anchored_ids: set[str] | None = None,
) -> str | None:
    """Check actual selected quotes, never other findings or the proposed statement.

    Numbers use the same citation/figure masking and explicit arithmetic policy
    as final-report validation. Each selected quote needs a number/name anchor
    when the referring sentence has one. Anchor-free prose remains model-reviewed;
    lexical matching is not a substitute for entailment or cross-language NER.
    """
    from ..report.validation import _computed_numbers, _numbers

    allowed = {record["id"] for record in evidence if record["citation"] in citations}
    if not ids or not set(ids).issubset(allowed):
        return "核验未提供本单元引用范围内的有效依据"
    selected = [record for record in evidence if record["id"] in ids]
    sentences = _segments(text)
    anchored: set[str] = set(anchored_ids or ())
    requires_anchor: set[str] = set()
    for sentence in sentences:
        explicit = {
            int(n) for match in _CITATIONS.finditer(sentence) for n in re.findall(r"\d+", match[1])
        }
        scope = explicit or set(citations)
        records = [record for record in selected if record["citation"] in scope]
        numeric_sentence = _numeric_text(sentence)
        names, numbers = _names(sentence, evidence), _numbers(numeric_sentence)
        support_numbers = set().union(
            *(
                _numbers(normalize_scientific_numbers(str(record.get("quote", ""))))
                for record in records
            )
        )
        missing, calculations = _computed_numbers(numeric_sentence, support_numbers)
        if (check_numbers and missing) or calculations:
            values = "、".join(str(n) for n in sorted(missing))
            return "所选依据不支持句中数值或显式计算" + (f"：{values}" if values else "")
        for record in records:
            raw_quote = str(record.get("quote", ""))
            quote = unicodedata.normalize(
                "NFKC", normalize_scientific_numbers(raw_quote)
            ).casefold()
            key = record["id"]
            if not numbers and not names:
                continue
            requires_anchor.add(key)
            name_match = any(_contains(quote, name) for name in names)
            # Coincidentally equal values must not hide an explicit subject
            # mismatch. Unnamed excerpts still rely on semantic review.
            if names and _names(raw_quote, evidence) and not name_match:
                continue
            if numbers.intersection(_numbers(quote)) or name_match:
                anchored.add(key)
    if requires_anchor - anchored:
        return "所选摘录缺少对应句中的数值或专名，请重新选择实际支持该句的依据"
    return None
