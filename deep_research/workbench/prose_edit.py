"""Source-bound edits of rejected paragraphs; all untouched text stays byte-for-byte."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel

from ..prompting import (
    EVIDENCE_MODALITY_RULES,
    MEASUREMENT_SCOPE_RULES,
    PrefixPrompt,
    leaf_system_prompt,
    structured_system_prompt,
)
from .delivery.markdown import _parser, parse_blocks
from .delivery.math_markdown import citation_text
from .prose_review import ProseReviewer
from .support import SupportUnit


class ProseEdit(BaseModel):
    unit_id: str
    replacement: str


class ProseEdits(BaseModel):
    edits: list[ProseEdit]


class UnchangedProseError(ValueError):
    """The proposed repair repeats rejected content; another review cannot help."""


_SYSTEM = (
    "你只修订指定的报告段落，不重写全文。逐条解决核验指出的问题，保留原段已有且有依据的内容。"
    "删除或收窄无依据的解释，不加入新事实，不把相关关系写成因果。"
    "当前选用证据未包含某内容，不等于论文未报告或不存在该内容。"
    "修复无依据的缺失/全面否定断言时，应删除该断言，或明确改为后续核查/验证建议；"
    "不能仅把‘论文未报告’改成‘所给证据未涉及’，也不能把未确认内容写成论文缺陷。"
    "可以修正引用，但只能使用给定证据中的本次引用编号，不能复制引句里的原论文编号。"
    "建议、问题和待确认项若提及已报告的数值或实验设置，这部分事实仍须保留或补上对应引用；"
    "也可删去重复的已知条件，写成不夹带事实断言的简短核查建议，不能仅改句式来免除引用。"
    "每个 unit_id 恰好返回一个 replacement，保留原段的 Markdown 结构与行内格式；"
    "列表项保留原有编号/标记，表格行保留列数与顺序，不添加其他条目、行、标题或章节。"
    "保持语言与文体；仅在问题明确为文体时调整措辞或标点，事实问题不能仅改标点敷衍。"
    "相邻段落只用于理解指代，不是证据，不修改它们。"
    + EVIDENCE_MODALITY_RULES
    + MEASUREMENT_SCOPE_RULES
)


def _shape(unit: SupportUnit, text: str) -> tuple[str, ...] | None:
    blocks = parse_blocks(text)
    if unit.kind == "translation":
        if (
            not blocks
            or blocks[0].kind != "heading"
            or not any(
                block.kind not in {"heading", "rule"} and block.plain().strip() for block in blocks
            )
        ):
            return None
        lines = text.splitlines()
        headings = [
            "\n".join(lines[token.map[0] : token.map[1]])
            for token in _parser().parse(text)
            if token.type == "heading_open" and token.map
        ]
        return ("translation", *headings)
    if len(blocks) != 1:
        return None
    if "\n表头：" in unit.context:
        if len(text.splitlines()) != 1 or blocks[0].kind != "paragraph":
            return None
        # GFM treats only unescaped pipes as cell separators (also in code spans).
        separators, slashes = 0, 0
        for char in text:
            if char == "|" and slashes % 2 == 0:
                separators += 1
            slashes = slashes + 1 if char == "\\" else 0
        if not separators:
            return None
        return ("row", str(separators), str(text.startswith("|")), str(text.endswith("|")))
    if blocks[0].kind == "list" and len(blocks[0].items) == 1 and len(text.splitlines()) == 1:
        if any(
            token.type
            not in {
                "bullet_list_open",
                "bullet_list_close",
                "ordered_list_open",
                "ordered_list_close",
                "list_item_open",
                "list_item_close",
                "paragraph_open",
                "paragraph_close",
                "inline",
            }
            for token in _parser().parse(text)
        ):
            return None
        marker = re.match(r"^(?:[-+*]|\d+[.)])\s+", text)
        return ("list", marker[0]) if marker else None
    if blocks[0].kind == "paragraph" and not text.startswith("|"):
        return ("paragraph",)
    return None


def _locate_problems(
    units: list[SupportUnit], problems: list[tuple[str, str]]
) -> dict[str, list[str]] | None:
    """Map deterministic excerpts only when they identify exactly one unit."""

    def compact(text: str) -> str:
        return re.sub(r"\s+", "", text)

    variants = {
        unit.id: (
            compact(unit.text),
            compact("\n".join(b.plain() for b in parse_blocks(unit.text))),
        )
        for unit in units
    }
    located: dict[str, list[str]] = {}
    for excerpt, message in problems:
        if excerpt in variants:
            located.setdefault(excerpt, []).append(message)
            continue
        needle = compact(excerpt.removesuffix("…"))
        matches = [
            uid for uid, texts in variants.items() if needle and any(needle in t for t in texts)
        ]
        if len(matches) != 1:
            return None
        located.setdefault(matches[0], []).append(message)
    return located


async def repair_paragraphs(
    llm: Any,
    reviewer: ProseReviewer,
    markdown: str,
    record: dict[str, Any],
    *,
    local_problems: list[tuple[str, str]] | None = None,
    only_units: set[str] | None = None,
) -> str | None:
    bound, _ = reviewer.check(markdown, record)
    if not bound or not record.get("can_revise", True):
        return None
    units, locations = reviewer.units(markdown)
    decisions = {
        d["unit_id"]: d
        for d in record.get("decisions", [])
        if d["verdict"] not in {"supported", "non_factual"}
    }
    prose_issues = record.get("prose_issues", record.get("issues", []))
    if len(prose_issues) != len(decisions):
        return None
    peer = record.get("peer_review") or {}
    if peer.get("requires_full_revision"):
        return None
    problems = _locate_problems(
        units, [*(local_problems or []), *(tuple(p) for p in peer.get("local_problems", []))],
    )
    if problems is None:
        return None
    for uid, decision in decisions.items():
        problems.setdefault(uid, []).append(decision["reason"])
    targets = [
        unit
        for unit in units
        if unit.id in problems and (only_units is None or unit.id in only_units)
    ]
    if not targets:
        return None
    by_id = {loc["id"]: loc for loc in locations}
    protected = {
        token.map[0]
        for token in _parser().parse(markdown)
        if token.map and token.type in {"fence", "code_block", "math_block"}
    }
    lines = markdown.splitlines(keepends=True)
    spans = {}
    payload = []
    known = {item["citation"] for item in reviewer.evidence if item["citation"] >= 0}
    abstract_known = {item["citation"] for item in reviewer.evidence if item["citation"] < 0}
    needed: dict[bool, set[int]] = {}
    shapes = {}
    for unit in targets:
        shape = _shape(unit, unit.text)
        if shape is None:
            return None
        shapes[unit.id] = shape
        translation = unit.kind == "translation"
        allowed = abstract_known if translation else known
        if (translation and (not unit.citations or not set(unit.citations).issubset(allowed))) or (
            not translation and only_units is None and not set(unit.citations).issubset(allowed)
        ):
            return None
        # Missing printed citations can be repaired against existing admitted
        # evidence; the replacement still undergoes full semantic/numeric checks.
        group_needed = needed.setdefault(translation, set())
        group_needed.update(set(unit.citations).intersection(allowed) or allowed)
        # A checker may point to a specific existing source missing from the
        # paragraph. Include it without resending unrelated parts of the paper.
        group_needed.update(
            int(n)
            for group in re.findall(r"\[(\d+(?:\s*[,，]\s*\d+)*)\]", "\n".join(problems[unit.id]))
            for n in re.split(r"\s*[,，]\s*", group)
            if not translation and int(n) in known
        )
        location = by_id[unit.id]
        start, end = location["start_line"] - 1, location["end_line"]
        if start in protected:
            return None
        original = "".join(lines[start:end])
        if original.strip().replace("\r\n", "\n") != unit.text:
            return None
        spans[unit.id] = (start, end, original)
        payload.append(
            {
                "unit_id": unit.id,
                "text": unit.text,
                "context": unit.context,
                "problem": "\n".join(problems[unit.id]),
                "structure": shape[0],
            }
        )
    # Translation-only evidence never enters the request that edits body claims.
    requests = []
    capacity = getattr(llm, "input_capacity_chars", reviewer.capacity)
    allowed_edits = {}
    for translation, group_needed in needed.items():
        group = [part for part in payload if (part["structure"] == "translation") == translation]
        evidence = [item for item in reviewer.evidence if item["citation"] in group_needed]
        fixed = "【已核验证据】\n" + json.dumps(evidence, ensure_ascii=False)
        dynamic = "\n\n【只修订以下段落】\n" + json.dumps(
            {"query": reviewer.query, "paragraphs": group}, ensure_ascii=False
        )
        rules = _SYSTEM
        if translation:
            rules += (
                "translation 是完整摘要翻译章节，replacement 包含原有标题与全部修订译文。"
                "标题逐字保留，译文段落可调整；只能逐句依据给定完整原摘要，不得概述或省略限定条件。"
                "负号 citation 是内部摘要标识，不是文献编号，不得输出这些编号或添加正文引用。"
                "修订后仍会对照完整原文重新检查译文。"
            )
        system = leaf_system_prompt(rules)
        if len(structured_system_prompt(system, ProseEdits)) + len(fixed) + len(dynamic) > capacity:
            return None
        ids = {part["unit_id"] for part in group}
        requests.append((system, PrefixPrompt(fixed, dynamic), ids))
        allowed_edits.update({uid: group_needed for uid in ids})
    proposals = []
    for system, prompt, ids in requests:
        response = await llm.parse(system, prompt, ProseEdits, temperature=0.2)
        if len(response.edits) != len(ids) or {edit.unit_id for edit in response.edits} != ids:
            raise ValueError("局部修订缺少段落或包含未知段落，未替换原文")
        proposals.extend(response.edits)
    edits = []
    by_unit = {unit.id: unit for unit in targets}
    for edit in proposals:
        replacement = edit.replacement.strip()
        start, end, original = spans[edit.unit_id]
        if not replacement or _shape(by_unit[edit.unit_id], replacement) != shapes[edit.unit_id]:
            raise ValueError("局部修订改变了段落结构，未替换原文")
        if re.sub(r"\W", "", replacement) == re.sub(r"\W", "", original):
            raise UnchangedProseError("局部修订未修改被拒绝的内容，未重新抽签核验")
        cited = {
            int(n)
            for group in re.findall(r"\[(\d+(?:\s*[,，]\s*\d+)*)\]", citation_text(replacement))
            for n in re.split(r"\s*[,，]\s*", group)
        }
        if not cited.issubset(allowed_edits[edit.unit_id]):
            raise ValueError("局部修订使用了未提供的来源编号，未替换原文")
        if by_unit[edit.unit_id].kind == "translation" and re.search(
            r"\[\s*-\d+(?:\s*[,，]\s*-\d+)*\s*\]", citation_text(replacement)
        ):
            raise ValueError("摘要译文不能输出内部来源编号，未替换原文")
        indent = (
            original[: len(original) - len(original.lstrip(" \t"))]
            if shapes[edit.unit_id][0] == "list"
            else ""
        )
        trailing = original[len(original.rstrip()) :]
        edits.append((start, end, indent + replacement + trailing))
    for start, end, replacement in sorted(edits, reverse=True):
        lines[start:end] = [replacement]
    return "".join(lines)
