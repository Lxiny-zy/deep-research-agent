"""待核标注：可修订问题所在段落原文保留，在段末追加〔待核：原因〕。

标注只加在交付展示稿上（Markdown / HTML / DOCX / PDF 共用的同一份），不改动
``report.markdown``：核验记录按正文哈希绑定，改动正文会让记录失效。
段落原文找不到时不硬塞标注，该条目仍出现在待核清单里。
"""

from __future__ import annotations

import re
from typing import Any

from .issue_tiers import revisable_code

REASON_LIMIT = 40
_LINE_PREFIX = re.compile(r"^(?:第\s*\d+\s*行|节点\s*[^：\s]+)：")
_TRAILING_QUOTE = re.compile(r"[（(][^（）()]{12,}[）)]$")

GATE_LABELS = {
    "prose_evidence": "段落支持关系",
    "node_evidence": "导图节点",
    "revision": "修订剩余问题",
    "structure": "章节结构",
    "structured_content": "结构化内容",
    "scholarly": "学术规范",
    "review": "评审格式",
    "review_coverage": "评审覆盖",
    "research_sufficiency": "检索充分性",
    "research_progress": "检索进度",
    "figure_evidence": "图示",
    "slides": "幻灯片版式",
    "uncited_paragraphs": "引用角标",
}


def short_reason(message: str, limit: int = REASON_LIMIT) -> str:
    """去掉行号/节点前缀与括注摘录，压成不超过 ``limit`` 字的原因。"""
    text = _LINE_PREFIX.sub("", re.sub(r"\s+", " ", message).strip())
    text = _TRAILING_QUOTE.sub("", text).strip(" ：:;；。")
    # 原因里不能再出现标注定界符，否则读者无法分辨标注的起止。
    text = text.replace("〔", "（").replace("〕", "）")
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text or "支持关系待核"


def marker(message: str) -> str:
    return f"〔待核：{short_reason(message)}〕"


def mark_paragraphs(markdown: str, targets: list[tuple[str, str]]) -> str:
    """``targets`` 为（段落原文, 原因）；同一原文重复出现时按出现顺序逐个对应。"""
    insertions: list[tuple[int, str]] = []
    seen: dict[str, int] = {}
    for needle, reason in targets:
        needle = needle.strip()
        if not needle:
            continue
        ordinal = seen.get(needle, 0)
        seen[needle] = ordinal + 1
        at = -1
        for _ in range(ordinal + 1):
            at = markdown.find(needle, at + 1)
            if at < 0:
                break
        if at < 0:
            continue
        end = at + len(needle)
        label = marker(reason)
        last = needle.splitlines()[-1].strip()
        if last.startswith("|") and last.endswith("|") and len(last) > 1:
            # 表格行：标注放进最后一格，不破坏表格结构。
            cell_end = end - 1
            while cell_end > at and markdown[cell_end - 1] == " ":
                cell_end -= 1
            insertions.append((cell_end, f" {label}"))
        elif needle.startswith(("```", "~~~", "$$")) or last in {"```", "~~~", "$$"}:
            # 代码块与公式块内部不能插字，标注另起一段紧随其后。
            insertions.append((end, f"\n\n{label}"))
        else:
            insertions.append((end, label))
    for position, text in sorted(insertions, key=lambda item: item[0], reverse=True):
        markdown = markdown[:position] + text + markdown[position:]
    return markdown


def prose_targets(
    reviewer: Any, markdown: str, findings: list[dict[str, Any]]
) -> list[tuple[str, str]]:
    """逐段核验中可修订、且定位到正文单元的问题 →（单元原文, 原因），每个单元只标一次。"""
    units = {unit.id: unit for unit in reviewer.units(markdown)[0]}
    targets: list[tuple[str, str]] = []
    marked: set[str] = set()
    for item in findings:
        unit = item.get("unit")
        if (
            not isinstance(unit, str)
            or unit in marked
            or unit not in units
            or not revisable_code("prose_evidence", item.get("code"))
        ):
            continue
        marked.add(unit)
        targets.append((units[unit].text, str(item.get("message", ""))))
    return targets


def node_targets(findings: list[dict[str, Any]]) -> dict[str, str]:
    """导图节点核对中可修订的问题 → {节点路径: 原因}；关系单元标在其子节点上。"""
    flagged: dict[str, str] = {}
    for item in findings:
        unit = item.get("unit")
        if not isinstance(unit, str) or not revisable_code("node_evidence", item.get("code")):
            continue
        path = unit.removeprefix("relation:")
        flagged.setdefault(path, str(item.get("message", "")))
    return flagged


def mark_mindmap(value: dict[str, Any], flagged: dict[str, str]) -> dict[str, Any]:
    """在导图节点标题后追加待核标注；``value`` 须是可修改的副本。"""
    if not flagged:
        return value
    if "root" in flagged and isinstance(value.get("root"), str):
        value["root"] += marker(flagged["root"])

    def walk(nodes: list[dict[str, Any]], prefix: str) -> None:
        for index, node in enumerate(nodes):
            path = f"{prefix}.{index}" if prefix else str(index)
            if path in flagged and isinstance(node.get("label"), str):
                node["label"] += marker(flagged[path])
            walk(node.get("children", []), path)

    walk(value.get("branches", []), "")
    return value


def slide_targets(deck: dict[str, Any], targets: list[tuple[str, str]]) -> dict[str, str]:
    """把待核段落对应到幻灯片：要点或标题出现在该段原文中的页 → {页序号: 原因}。"""
    flagged: dict[str, str] = {}
    for index, slide in enumerate(deck.get("slides", [])):
        texts = [
            text.strip()
            for text in [slide.get("title"), *slide.get("bullets", [])]
            if isinstance(text, str) and len(text.strip()) >= 4
        ]
        for needle, reason in targets:
            if any(text in needle for text in texts):
                flagged.setdefault(str(index), reason)
                break
    return flagged


def mark_slides(deck: dict[str, Any], flagged: dict[str, str]) -> dict[str, Any]:
    """待核标注写进该页备注，不改动要点（要点须与审核正文一致）。"""
    for key, reason in flagged.items():
        slides = deck.get("slides", [])
        if not key.isdigit() or int(key) >= len(slides):
            continue
        spec = slides[int(key)]
        notes = spec.get("notes") if isinstance(spec.get("notes"), str) else ""
        spec["notes"] = (notes.rstrip() + "\n" if notes.strip() else "") + marker(reason)
    return deck


def flagged_appendix(items: list[dict[str, str]]) -> str:
    """附在交付正文末尾的待核清单，与完成记录中的 ``flagged`` 同源。"""
    if not items:
        return ""
    lines = [
        "## 待核事项",
        "",
        f"以下 {len(items)} 项在修订轮次内未能消除，相关段落已在正文中以〔待核〕标出；"
        "这些段落的结论请对照原始来源确认。",
        "",
    ]
    for item in items:
        label = GATE_LABELS.get(item.get("gate", ""), "其他")
        lines.append(f"- {label}：{short_reason(item.get('issue', ''), 120)}")
    return "\n".join(lines) + "\n"


__all__ = [
    "flagged_appendix",
    "mark_mindmap",
    "mark_paragraphs",
    "mark_slides",
    "marker",
    "node_targets",
    "prose_targets",
    "short_reason",
    "slide_targets",
]
