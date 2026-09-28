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
    # 每个问题落在哪一段：(issue 代码, 段落摘录, 说明)，供写作者返工时逐段修改。
    problems: tuple[tuple[str, str, str], ...] = ()
    # 写作者的原始正文（去掉参考来源段）；回退时 body 是素材摘要，这里仍保留原稿。
    draft: str = ""


def _excerpt(text: str) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    return flat if len(flat) <= 60 else flat[:60] + "…"


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
    fallback: bool = True,
    uncited_sections: tuple[str, ...] = (),
) -> ReportCheck:
    """复核正文的引用与数值。

    ``fallback=True``（默认）时任何问题都让正文回退为已核验素材摘要——这是终态的
    安全网。写作者的返工循环用 ``fallback=False`` 拿到逐段问题，先让模型修订，
    修订用尽仍不合格才回退。

    ``uncited_sections``：标题含这些词的章节（如「摘要」）按交付规范不带引用角标，
    其段落免于「必须引用」，但其中的数字仍必须出现在已核验素材里。
    """
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
    problems: list[tuple[str, str, str]] = []
    all_indices = {
        int(number)
        for match in _CITATION.findall(body)
        for number in re.split(r"\s*[,，]\s*", match)
    }
    if not all_indices.issubset(evidence):
        issues.append("invalid_citation")
        unknown = sorted(all_indices - set(evidence))
        problems.append(("invalid_citation", "", f"引用了不存在的素材编号 {unknown}"))
    all_support = _numbers("\n".join(text for items in evidence.values() for text in items))
    heading = ""
    for paragraph in re.split(r"\n\s*\n", body):
        headings = [
            line.lstrip("# ").strip()
            for line in paragraph.splitlines()
            if line.lstrip().startswith("#")
        ]
        if headings:
            heading = headings[-1]
        exempt = bool(uncited_sections) and any(
            key.casefold() in heading.casefold() for key in uncited_sections
        )
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
        if not indices and exempt:
            unsupported = sorted(_numbers(content) - all_support)
            if unsupported:
                issues.append("unsupported_number")
                shown = "、".join(format(v.normalize(), "f") for v in unsupported[:5])
                problems.append(
                    ("unsupported_number", _excerpt(content), f"数字 {shown} 在素材中找不到")
                )
            continue
        if not indices:
            issues.append("uncited_paragraph")
            problems.append(("uncited_paragraph", _excerpt(content), "这一段没有任何引用角标"))
            continue
        if not indices.issubset(evidence):
            issues.append("invalid_citation")
            continue
        support = "\n".join(text for index in indices for text in evidence[index])
        missing = sorted(_numbers(content) - _numbers(support))
        if missing:
            issues.append("unsupported_number")
            shown = "、".join(format(value.normalize(), "f") for value in missing[:5])
            problems.append(
                ("unsupported_number", _excerpt(content), f"数字 {shown} 在所引素材中找不到")
            )
    if not body:
        issues.append("empty_report")
        problems.append(("empty_report", "", "正文为空"))
    draft = body
    if issues and fallback:
        # Preserve verified material instead of returning invented citations or altered values.
        body = "## 已验证素材摘要\n\n" + "\n".join(dict.fromkeys(safe_statements))
    return ReportCheck(
        body, tuple(dict.fromkeys(issues)), evidence, tuple(dict.fromkeys(problems)), draft
    )


def describe_problems(check: ReportCheck, limit: int = 12) -> list[str]:
    """把复核问题写成可读的返工条目。"""
    return [
        message + (f"：「{where}」" if where else "")
        for _code, where, message in check.problems[:limit]
    ]


def finalize_report(
    report: Report,
    results: list[ResearchResult],
    *,
    require_corroboration: bool = False,
    uncited_sections: tuple[str, ...] = (),
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
    check = validate_body(
        body,
        results,
        mapping,
        require_corroboration=require_corroboration,
        uncited_sections=uncited_sections,
    )
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
