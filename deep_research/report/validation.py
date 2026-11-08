"""Deterministic final-output checks, separate from input evidence verification."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, DecimalException, Inexact, InvalidOperation, Underflow, localcontext

from ..guardrails import report_eligible
from ..models import Report, ResearchResult
from ..workbench.delivery.markdown import framing_paragraphs
from ..workbench.delivery.math_markdown import (
    citation_text,
    only_math,
    replace_citations,
    validation_paragraphs,
)

_CITATION = re.compile(r"\[(\d+(?:\s*[,，]\s*\d+)*)\]")
# 紧跟公式结束符 $ 或 } 的减号是名称连字符（RDLUF-MixS$^2$-9stg），不是负号：
# 第十三轮线上幻灯片据此报「数字 -9 在所引素材中找不到」，整份回退为证据摘录
_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_.])(?:(?<![$}])[-+])?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?"
)
_OPERAND = r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?"
_CALCULATION = re.compile(
    rf"(?<![A-Za-z0-9_.])(?P<left>{_OPERAND})\s*(?P<op>[+*/×÷-])\s*"
    rf"(?P<right>{_OPERAND})\s*(?P<relation>=|≈)\s*(?P<result>{_OPERAND})(?![\d.])"
)
_STRUCTURAL_REF = re.compile(
    r"(?<![A-Za-z])(?:图|表|公式|式|Figure|Fig\.?|Table|Equation|Eq\.?)"
    r"\s*\(?[A-Z]?\d+(?:[.-]\d+)*[a-z]?\)?",
    re.I,
)
_TABLE_NOTE_REF = re.compile(r"[（(]\s*注\s*\d+\s*[）)]")
_NUMBER_WORDS = {
    word: index
    for index, word in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
        "fifteen sixteen seventeen eighteen nineteen twenty".split()
    )
} | {"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
     "ninety": 90, "hundred": 100}
_NUMBER_WORD = re.compile(
    r"\b(" + "|".join(_NUMBER_WORDS) + r")(?:-(one|two|three|four|five|six|seven|eight|nine))?\b",
    re.I,
)
_CHINESE_DIGITS = {"两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
                   "十": 10}
_CHINESE_COUNT = re.compile(
    r"([两二三四五六七八九十])\s*(?:个|种|组|幅|层|类|项|次|名|篇|张|帧|步|轮|段|维|阶段|通道|波段)"
)
# 维度与卷积核写作 3×3、$conv3\times3$；乘号两侧的数是独立的数值，不是标识符的一部分。
_FACTOR = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?=(?:\\times|×)\s*\d)|(?:\\times|×)\s*(\d+(?:\.\d+)?)(?![\d.]|\s*\^)"
)


@dataclass(frozen=True)
class ReportCheck:
    body: str
    issues: tuple[str, ...]
    evidence_by_citation: dict[int, list[str]]
    # 每个问题落在哪一段：(issue 代码, 段落摘录, 说明)，供写作者返工时逐段修改。
    problems: tuple[tuple[str, str, str], ...] = ()
    # 写作者的原始正文（去掉参考来源段）；回退时 body 是素材摘要，这里仍保留原稿。
    draft: str = ""
    partial: bool = False
    # 只有可修订问题（缺引用角标、段内数值均有依据）而原文保留的段落：
    # (issue 代码, 段落原文, 说明)，交付时在该段末尾标注〔待核：…〕。
    flagged: tuple[tuple[str, str, str], ...] = ()


def _excerpt(text: str) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    return flat if len(flat) <= 60 else flat[:60] + "…"


def _numbers(text: str) -> set[Decimal]:
    # 先去引用再归一化：「[8]（存疑）」归一化后成了链接写法 [8](存疑)，角标不再被认作引用
    text = replace_citations(text, lambda _: "")
    text = unicodedata.normalize("NFKC", text).replace("−", "-")
    text = replace_citations(text, lambda _: "")
    text = re.sub(r"(?m)^\s*(?:\*\*|__)?\d+[.)、]\s+", "", text)
    # Figure/table/equation references are document labels, not measurements.
    # Mask only prose positions so numerical expressions inside math stay checked.
    markers = [
        *list(_STRUCTURAL_REF.finditer(citation_text(text))),
        *list(_TABLE_NOTE_REF.finditer(citation_text(text))),
    ]
    for match in sorted(markers, key=lambda item: item.start(), reverse=True):
        text = text[: match.start()] + " " * len(match[0]) + text[match.end() :]
    values = set()
    for raw in _NUMBER.findall(text):
        try:
            values.add(Decimal(raw.replace(",", "")))
        except InvalidOperation:
            pass
    return values


def evidence_numbers(text: str) -> set[Decimal]:
    """依据原文写出的数值，含英文数词、中文量词前的数字和乘号两侧的因子。

    论文写 "Five real HSIs"、"$conv3\\times3$"，报告写 5 个、3×3；只认阿拉伯数字时
    这些忠实转述都被判为无依据。只用于依据一侧，报告正文仍按原规则逐个取数。
    """
    values = _numbers(text)
    normalized = unicodedata.normalize("NFKC", text)
    for left, right in _FACTOR.findall(normalized):
        values.add(Decimal(left or right))
    for word, unit in _NUMBER_WORD.findall(normalized):
        value = _NUMBER_WORDS[word.casefold()]
        if unit and 20 <= value < 100:
            value += _NUMBER_WORDS[unit.casefold()]
        elif unit:
            values.add(Decimal(_NUMBER_WORDS[unit.casefold()]))
        values.add(Decimal(value))
    values.update(Decimal(_CHINESE_DIGITS[digit]) for digit in _CHINESE_COUNT.findall(normalized))
    return values


def _citation_text(text: str) -> str:
    return citation_text(text)


def _computed_numbers(text: str, support: set[Decimal]) -> tuple[set[Decimal], list[str]]:
    """Check explicit binary arithmetic; operands still need cited evidence.

    Only the result at the equation's position is accounted for, so it cannot
    justify an unrelated claim containing the same number. Method, metric,
    units and condition comparability still require final prose review.
    """
    normalized = unicodedata.normalize(
        "NFKC", replace_citations(text, lambda _: " ")
    ).replace("−", "-")
    for latex, symbol in ((r"\times", "×"), (r"\cdot", "*"), (r"\div", "÷"), (r"\approx", "≈")):
        normalized = normalized.replace(latex, symbol)
    invalid = []
    for match in reversed(list(_CALCULATION.finditer(normalized))):
        before = normalized[: match.start()].rstrip()
        after = normalized[match.end() :].lstrip()
        bullet = before.rsplit("\n", 1)[-1].strip() in {"-", "*", "+"}
        if (before and before[-1] in "+-*/×÷" and not bullet) or (after and after[0] in "+-*/×÷="):
            invalid.append("复杂算式需拆为逐步可核对的二元计算")
            continue
        operands = [match[key].replace(",", "") for key in ("left", "right", "result")]
        if any(len(value) > 64 for value in operands):
            invalid.append("算式数值过长，未验证")
            continue
        try:
            left, right, reported = map(Decimal, operands)
            with localcontext() as context:
                context.prec, context.Emax, context.Emin = 80, 9999, -9999
                context.traps[Underflow] = True
                context.traps[Inexact] = match["relation"] == "="
                operator = match["op"]
                if operator == "+":
                    value = left + right
                elif operator == "-":
                    value = left - right
                elif operator in {"*", "×"}:
                    value = left * right
                else:
                    value = left / right
                if match["relation"] == "≈":
                    exponent = reported.as_tuple().exponent
                    if not isinstance(exponent, int):
                        raise InvalidOperation("non-finite calculation result")
                    value = value.quantize(Decimal(1).scaleb(exponent))
                valid = value.is_finite() and value == reported
        except DecimalException:
            valid = False
        if not valid:
            invalid.append(f"算式结果不正确或无法精确验证：{match[0]}")
        elif left in support and right in support:
            start, end = match.span("result")
            normalized = normalized[:start] + " " * (end - start) + normalized[end:]
    return _numbers(normalized) - support, invalid


def validate_body(
    body: str,
    results: list[ResearchResult],
    url_to_idx: dict[str, int],
    *,
    require_corroboration: bool = False,
    fallback: bool = True,
    preserve_valid: bool = False,
    uncited_sections: tuple[str, ...] = (),
    section_support: dict[str, str] | None = None,
    allow_uncited: bool = False,
    keep_revisable: bool = False,
) -> ReportCheck:
    """复核正文的引用与数值。

    ``fallback=True``（默认）时任何问题都让正文回退为已核验素材摘要——这是终态的
    安全网。写作者的返工循环用 ``fallback=False`` 拿到逐段问题，先让模型修订，
    修订用尽仍不合格才回退。

    ``uncited_sections``：标题含这些词的章节（如「摘要」）按交付规范不带引用角标，
    其段落免于「必须引用」，但其中的数字仍必须出现在已核验素材里。

    ``keep_revisable``：终态复核时，只有可修订问题（见 ``issue_tiers``）的段落不删不换，
    原文保留并记入 ``flagged``；引用越界、无依据数值与错误算式仍按原规则处理。
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
            statement = replace_citations(finding.statement, lambda _: "").strip()
            safe_statements.append(f"- {statement} [{index}]")
    if not evidence:
        return ReportCheck(
            "没有通过证据门禁的可用素材，无法生成事实性结论。", ("no_eligible_evidence",), {}
        )
    from ..bibliography import source_body

    body = source_body(body)
    issues: list[str] = []
    problems: list[tuple[str, str, str]] = []
    all_indices = {
        int(number)
        for match in _CITATION.findall(_citation_text(body))
        for number in re.split(r"\s*[,，]\s*", match)
    }
    if not all_indices.issubset(evidence):
        issues.append("invalid_citation")
        unknown = sorted(all_indices - set(evidence))
    all_support = evidence_numbers(
        "\n".join(text for items in evidence.values() for text in items)
    )
    frames = set(framing_paragraphs(body).values())
    heading = ""
    paragraphs = validation_paragraphs(body)
    rejected: set[int] = set()
    codes_at: dict[int, list[str]] = {}
    for position, paragraph in enumerate(paragraphs):
        # 未渲染的表格规格不能交付；其 JSON 中的编号片段不是正文数值，不按数字报错。
        if paragraph.lstrip().startswith("```evidence-table"):
            rejected.add(position)
            issues.append("unrendered_table")
            codes_at.setdefault(position, []).append("unrendered_table")
            problems.append((
                "unrendered_table", _excerpt(paragraph),
                "表格规格未能渲染，规格原文不能交付；按表格检查给出的原因修正或删除该表",
            ))
            continue
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
        exempt = exempt or "\n".join(paragraph.splitlines()).strip() in frames
        content = "\n".join(
            line for line in paragraph.splitlines() if not line.lstrip().startswith("#")
        )
        if not content.strip() or not re.search(r"[\w\u4e00-\u9fff]", content):
            continue
        indices = {
            int(number)
            for match in _CITATION.findall(_citation_text(content))
            for number in re.split(r"\s*[,，]\s*", match)
        }
        dedicated = next(
            (
                text
                for key, text in (section_support or {}).items()
                if key.casefold() in heading.casefold()
            ),
            None,
        )
        if dedicated is not None:
            unsupported = sorted(_numbers(content) - evidence_numbers(dedicated))
            if unsupported:
                rejected.add(position)
                issues.append("unsupported_number")
                codes_at.setdefault(position, []).append("unsupported_number")
                shown = "、".join(format(v.normalize(), "f") for v in unsupported[:5])
                problems.append(
                    ("unsupported_number", _excerpt(content), f"数字 {shown} 在本节原文中找不到")
                )
            continue
        if not indices and not exempt and not allow_uncited:
            exempt = only_math(content)
        if not indices and (exempt or allow_uncited):
            absent, invalid = _computed_numbers(content, all_support)
            if invalid:
                rejected.add(position)
                issues.append("invalid_calculation")
                codes_at.setdefault(position, []).append("invalid_calculation")
                problems.extend(
                    ("invalid_calculation", _excerpt(content), item) for item in invalid
                )
            unsupported = sorted(absent)
            if unsupported:
                rejected.add(position)
                issues.append("unsupported_number")
                codes_at.setdefault(position, []).append("unsupported_number")
                shown = "、".join(format(v.normalize(), "f") for v in unsupported[:5])
                problems.append(
                    ("unsupported_number", _excerpt(content), f"数字 {shown} 在素材中找不到")
                )
            continue
        if not indices:
            rejected.add(position)
            issues.append("uncited_paragraph")
            codes_at.setdefault(position, []).append("uncited_paragraph")
            problems.append(("uncited_paragraph", _excerpt(content), "这一段没有任何引用角标"))
            continue
        if not indices.issubset(evidence):
            rejected.add(position)
            issues.append("invalid_citation")
            codes_at.setdefault(position, []).append("invalid_citation")
            problems.append((
                "invalid_citation", _excerpt(content),
                f"引用了不存在的素材编号 {sorted(indices - set(evidence))}",
            ))
            continue
        support = "\n".join(text for index in indices for text in evidence[index])
        absent, invalid = _computed_numbers(content, evidence_numbers(support))
        if invalid:
            rejected.add(position)
            issues.append("invalid_calculation")
            codes_at.setdefault(position, []).append("invalid_calculation")
            problems.extend(("invalid_calculation", _excerpt(content), item) for item in invalid)
        missing = sorted(absent)
        if missing:
            rejected.add(position)
            issues.append("unsupported_number")
            codes_at.setdefault(position, []).append("unsupported_number")
            shown = "、".join(format(value.normalize(), "f") for value in missing[:5])
            problems.append(
                ("unsupported_number", _excerpt(content), f"数字 {shown} 在所引素材中找不到")
            )
    if not body:
        issues.append("empty_report")
        problems.append(("empty_report", "", "正文为空"))
    draft = body
    partial = False
    if "invalid_citation" in issues and not any(
        code == "invalid_citation" for code, _, _ in problems
    ):
        problems.append(("invalid_citation", "", f"引用了不存在的素材编号 {unknown}"))
    flagged: list[tuple[str, str, str]] = []
    if fallback and keep_revisable:
        from ..workbench.issue_tiers import REVISABLE_VALIDATION_CODES

        for position, codes in sorted(codes_at.items()):
            if not set(codes) <= REVISABLE_VALIDATION_CODES:
                continue
            content = "\n".join(
                line
                for line in paragraphs[position].splitlines()
                if not line.lstrip().startswith("#")
            ).strip()
            # 保留未引用段落的前提：段内每个数值都能在已核验素材中找到。
            absent, invalid = _computed_numbers(content, all_support)
            if absent or invalid:
                issues.append("unsupported_number")
                shown = "、".join(format(v.normalize(), "f") for v in sorted(absent)[:5])
                problems.append((
                    "unsupported_number", _excerpt(content),
                    f"未引用段落中的数字 {shown} 在素材中找不到" if shown else invalid[0],
                ))
                continue
            rejected.discard(position)
            for code in codes:
                issues.remove(code)
                problems.remove((code, _excerpt(content), "这一段没有任何引用角标"))
            flagged.append((codes[0], content, "这一段没有任何引用角标"))
    if issues and fallback:
        candidate = "\n\n".join(p for index, p in enumerate(paragraphs) if index not in rejected)
        content = "\n".join(
            line for line in candidate.splitlines() if not line.lstrip().startswith("#")
        )
        if preserve_valid and rejected and re.search(r"[\w\u4e00-\u9fff]", content):
            # 可修订段落在候选稿中原样保留，复核时同样只按硬问题判断。
            checked = validate_body(
                candidate, results, url_to_idx, fallback=keep_revisable,
                require_corroboration=require_corroboration,
                uncited_sections=uncited_sections, section_support=section_support,
                allow_uncited=allow_uncited, keep_revisable=keep_revisable,
            )
            if not checked.issues:
                body, partial = candidate, True
        if not partial:
            if preserve_valid and rejected:
                # Preserve the requested report shape after repair exhaustion;
                # replace unsafe paragraphs with a neutral, explicit marker
                # instead of collapsing the entire document into an excerpt.
                body = "\n\n".join(
                    "本段结论待进一步核验，当前不作确定性判断。"
                    if index in rejected else paragraph
                    for index, paragraph in enumerate(paragraphs)
                )
            else:
                # Global or unlocalizable failures still use verified material only.
                body = "## 已验证素材摘要\n\n" + "\n".join(dict.fromkeys(safe_statements))
                flagged = []
    return ReportCheck(
        body,
        tuple(dict.fromkeys(issues)),
        evidence,
        tuple(dict.fromkeys(problems)),
        draft,
        partial,
        tuple(dict.fromkeys(flagged)),
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
    preserve_valid: bool = False,
    uncited_sections: tuple[str, ...] = (),
    section_support: dict[str, str] | None = None,
    allow_uncited: bool = False,
    keep_revisable: bool = False,
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
        preserve_valid=preserve_valid,
        uncited_sections=uncited_sections,
        section_support=section_support,
        allow_uncited=allow_uncited,
        keep_revisable=keep_revisable,
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

    checked_body = replace_citations(check.body, replace_citation)
    references = "\n".join(
        f"[{index}] {eligible[url]}" for index, (url, _) in enumerate(included, 1)
    )
    return report.model_copy(
        update={
            "markdown": f"{checked_body}\n\n## 参考来源\n{references}\n",
            "citations": [url for url, _ in included],
        }
    ), check
