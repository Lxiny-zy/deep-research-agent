"""Deterministic final-output checks, separate from input evidence verification."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from ..guardrails import report_eligible
from ..models import Report, ResearchResult

_CITATION = re.compile(r"\[(\d+(?:\s*[,，]\s*\d+)*)\]")
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])[-+]?\d+(?:,\d{3})*(?:\.\d+)?")
_REFERENCES = re.compile(r"\n#{1,3}\s*(?:参考来源|参考文献|References)\s*\n.*\Z", re.S | re.I)


@dataclass(frozen=True)
class ReportCheck:
    body: str
    issues: tuple[str, ...]
    evidence_by_citation: dict[int, list[str]]


def _numbers(text: str) -> set[Decimal]:
    text = _CITATION.sub("", text)
    text = re.sub(r"(?m)^\s*\d+[.)、]\s+", "", text)
    values = set()
    for raw in _NUMBER.findall(text):
        try:
            values.add(Decimal(raw.replace(",", "")))
        except InvalidOperation:
            pass
    return values


def validate_body(
    body: str,
    results: list[ResearchResult],
    url_to_idx: dict[str, int],
    *,
    require_corroboration: bool = False,
) -> ReportCheck:
    evidence: dict[int, list[str]] = {}
    safe_statements: list[str] = []
    for result in results:
        for finding in result.findings:
            index = url_to_idx.get(finding.source_url)
            if index is None or not report_eligible(
                finding, require_corroboration=require_corroboration
            ):
                continue
            evidence.setdefault(index, []).append(f"{finding.statement}\n{finding.evidence_quote}")
            statement = _CITATION.sub("", finding.statement).strip()
            safe_statements.append(f"- {statement} [{index}]")
    if not evidence:
        return ReportCheck(
            "没有通过证据门禁的可用素材，无法生成事实性结论。", ("no_eligible_evidence",), {}
        )
    body = _REFERENCES.sub("", body).strip()
    issues: list[str] = []
    all_indices = {
        int(number)
        for match in _CITATION.findall(body)
        for number in re.split(r"\s*[,，]\s*", match)
    }
    if not all_indices.issubset(evidence):
        issues.append("invalid_citation")
    for paragraph in re.split(r"\n\s*\n", body):
        content = "\n".join(
            line for line in paragraph.splitlines() if not line.lstrip().startswith("#")
        )
        if not content.strip() or not re.search(r"[\w\u4e00-\u9fff]", content):
            continue
        indices = {
            int(number)
            for match in _CITATION.findall(content)
            for number in re.split(r"\s*[,，]\s*", match)
        }
        if not indices:
            issues.append("uncited_paragraph")
            continue
        if not indices.issubset(evidence):
            issues.append("invalid_citation")
            continue
        support = "\n".join(text for index in indices for text in evidence[index])
        if not _numbers(content).issubset(_numbers(support)):
            issues.append("unsupported_number")
    if not body:
        issues.append("empty_report")
    if issues:
        # Preserve verified material instead of returning invented citations or altered values.
        body = "## 已验证素材摘要\n\n" + "\n".join(dict.fromkeys(safe_statements))
    return ReportCheck(body, tuple(dict.fromkeys(issues)), evidence)


def finalize_report(
    report: Report, results: list[ResearchResult], *, require_corroboration: bool = False
) -> tuple[Report, ReportCheck]:
    """Apply the same checks after every terminal research role, including custom roles."""
    eligible = {
        finding.source_url: finding.verification.source_reference or finding.source_url
        for result in results
        for finding in result.findings
        if report_eligible(finding, require_corroboration=require_corroboration)
    }
    mapping = {url: index for index, url in enumerate(report.citations, 1)}
    # Missing source mapping cannot establish what the writer's numbers refer to.
    body = report.markdown
    if not mapping and eligible:
        mapping = {url: index for index, url in enumerate(eligible, 1)}
        body = ""
    check = validate_body(body, results, mapping, require_corroboration=require_corroboration)
    included = [
        (url, index) for url, index in mapping.items() if index in check.evidence_by_citation
    ]
    renumber = {old: new for new, (_, old) in enumerate(included, 1)}

    def replace_citation(match: re.Match[str]) -> str:
        return (
            "["
            + ", ".join(str(renumber[int(number)]) for number in re.split(r"\s*[,，]\s*", match[1]))
            + "]"
        )

    checked_body = _CITATION.sub(replace_citation, check.body)
    references = "\n".join(
        f"[{index}] {eligible[url]}" for index, (url, _) in enumerate(included, 1)
    )
    return report.model_copy(
        update={
            "markdown": f"{checked_body}\n\n## 参考来源\n{references}\n",
            "citations": [url for url, _ in included],
        }
    ), check
