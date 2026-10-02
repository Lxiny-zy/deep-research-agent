"""Markdown → 块树：所有交付格式共用的唯一解析结果。

使用 markdown-it（CommonMark + GFM 表格），不做任何 HTML 直通：原始 HTML 块被当作
普通文本处理。交付物的内容来自模型，模型输出里的 ``<script>`` 不能活着进入自包含
HTML——关掉 html 选项，在解析这一步就把这条路堵死。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from markdown_it import MarkdownIt
from markdown_it.token import Token

BlockKind = Literal[
    "heading", "paragraph", "list", "table", "code", "quote", "rule", "image", "math"
]


@dataclass
class Inline:
    """行内片段：文本 + 样式标记。"""

    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    href: str = ""
    math: bool = False
    display: bool = False


@dataclass
class ListItem:
    inlines: list[Inline]
    depth: int = 0
    ordered: bool = False
    number: int = 0


@dataclass
class Block:
    kind: BlockKind
    level: int = 0
    inlines: list[Inline] = field(default_factory=list)
    items: list[ListItem] = field(default_factory=list)
    rows: list[list[list[Inline]]] = field(default_factory=list)  # 首行为表头
    text: str = ""  # code / image alt
    src: str = ""  # image
    lang: str = ""

    def plain(self) -> str:
        if self.kind in {"code", "math"}:
            return self.text
        if self.kind == "list":
            return "\n".join(plain(item.inlines) for item in self.items)
        if self.kind == "table":
            return "\n".join(" | ".join(plain(cell) for cell in row) for row in self.rows)
        if self.kind == "image":
            return self.text
        return plain(self.inlines)


def plain(inlines: list[Inline]) -> str:
    return "".join(item.text for item in inlines)


def _parser() -> MarkdownIt:
    from .math_markdown import math_plugin

    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
    md.enable("table")
    md.enable("strikethrough")
    md.use(math_plugin)
    return md


def framing_paragraphs(markdown: str) -> dict[int, str]:
    """Short bold headings and table captions need no printed citation marker.

    Their factual content still undergoes numeric and semantic checks.
    """
    tokens = _parser().parse(markdown)
    lines = markdown.splitlines()
    frames: dict[int, str] = {}
    for index, token in enumerate(tokens):
        if token.type != "paragraph_open" or not token.map:
            continue
        children = [
            child
            for child in tokens[index + 1].children or []
            if child.type != "text" or child.content.strip()
        ]
        text = "".join(child.content for child in children if child.type == "text")
        heading = (
            len(children) >= 3
            and children[0].type == "strong_open"
            and children[-1].type == "strong_close"
            and all(child.type == "text" for child in children[1:-1])
            and 0 < len(text.strip()) <= 40
        )
        caption = (
            index + 3 < len(tokens)
            and tokens[index + 3].type == "table_open"
            and re.match(r"^\s*(?:表\s*[\d一二三四五六七八九十A-Z]|Table\s+[\dA-Z])", text, re.I)
        )
        if heading or caption:
            frames[token.map[0]] = "\n".join(lines[token.map[0] : token.map[1]]).strip()
    return frames


def _inlines(token: Token | None) -> list[Inline]:
    if token is None or not token.children:
        return [Inline(token.content)] if token is not None and token.content else []
    out: list[Inline] = []
    bold = italic = False
    href = ""
    for child in token.children:
        if child.type == "strong_open":
            bold = True
        elif child.type == "strong_close":
            bold = False
        elif child.type == "em_open":
            italic = True
        elif child.type == "em_close":
            italic = False
        elif child.type == "link_open":
            href = str(child.attrs.get("href", ""))
        elif child.type == "link_close":
            href = ""
        elif child.type == "code_inline":
            out.append(Inline(child.content, code=True))
        elif child.type in {"softbreak", "hardbreak"}:
            out.append(Inline("\n" if child.type == "hardbreak" else " "))
        elif child.type == "image":
            out.append(Inline(child.content or str(child.attrs.get("alt", ""))))
        elif child.type in {"math_inline", "math_inline_double"}:
            out.append(Inline(child.content, math=True, display=child.type == "math_inline_double"))
        elif child.type == "text":
            out.append(Inline(child.content, bold=bold, italic=italic, href=href))
    return out


def parse_blocks(markdown: str) -> list[Block]:
    tokens = _parser().parse(markdown)
    blocks: list[Block] = []
    list_stack: list[dict[str, int | bool]] = []
    quote_depth = 0
    index = 0
    while index < len(tokens):
        token = tokens[index]
        kind = token.type
        if kind == "heading_open":
            level = int(token.tag[1])
            blocks.append(Block("heading", level=level, inlines=_inlines(tokens[index + 1])))
            index += 3
            continue
        if kind == "math_block":
            if list_stack and blocks and blocks[-1].kind == "list":
                blocks[-1].items[-1].inlines.append(Inline(token.content, math=True, display=True))
            else:
                blocks.append(Block("math", text=token.content))
            index += 1
            continue
        if kind in {"bullet_list_open", "ordered_list_open"}:
            start = int(token.attrs.get("start", 1)) if token.attrs else 1
            list_stack.append({"ordered": kind == "ordered_list_open", "counter": start - 1})
            if len(list_stack) == 1:
                blocks.append(Block("list"))
            index += 1
            continue
        if kind in {"bullet_list_close", "ordered_list_close"}:
            list_stack.pop()
            index += 1
            continue
        if kind == "list_item_open":
            frame = list_stack[-1]
            frame["counter"] = int(frame["counter"]) + 1
            item = ListItem(
                inlines=[],
                depth=len(list_stack) - 1,
                ordered=bool(frame["ordered"]),
                number=int(frame["counter"]),
            )
            blocks[-1].items.append(item)
            index += 1
            continue
        if kind == "paragraph_open":
            inline = tokens[index + 1]
            if list_stack:
                # 列表项中的后续段落并入该项
                if blocks and blocks[-1].kind == "list" and blocks[-1].items:
                    current_inlines = blocks[-1].items[-1].inlines
                    current_inlines.extend(
                        ([Inline(" ")] if current_inlines else []) + _inlines(inline)
                    )
                index += 3
                continue
            children = inline.children or []
            images = [child for child in children if child.type == "image"]
            if len(images) == 1 and all(
                child.type == "image" or (child.type == "text" and not child.content.strip())
                for child in children
            ):
                image = images[0]
                blocks.append(
                    Block("image", text=image.content, src=str(image.attrs.get("src", "")))
                )
            elif quote_depth:
                blocks.append(Block("quote", inlines=_inlines(inline)))
            else:
                blocks.append(Block("paragraph", inlines=_inlines(inline)))
            index += 3
            continue
        if kind == "blockquote_open":
            quote_depth += 1
            index += 1
            continue
        if kind == "blockquote_close":
            quote_depth -= 1
            index += 1
            continue
        if kind in {"fence", "code_block"}:
            info = token.info.strip() if token.info else ""
            text = token.content.rstrip("\n")
            if info in {"math", "latex", "tex"} and not any(
                command in text for command in (r"\documentclass", r"\begin{document}")
            ):
                if list_stack and blocks and blocks[-1].kind == "list":
                    blocks[-1].items[-1].inlines.append(Inline(text, math=True, display=True))
                else:
                    blocks.append(Block("math", text=text))
            else:
                blocks.append(Block("code", text=text, lang=info))
            index += 1
            continue
        if kind == "hr":
            blocks.append(Block("rule"))
            index += 1
            continue
        if kind == "table_open":
            rows: list[list[list[Inline]]] = []
            j = index + 1
            current: list[list[Inline]] | None = None
            while j < len(tokens) and tokens[j].type != "table_close":
                t = tokens[j]
                if t.type == "tr_open":
                    current = []
                elif t.type == "tr_close" and current is not None:
                    rows.append(current)
                    current = None
                elif t.type == "inline" and current is not None:
                    current.append(_inlines(t))
                j += 1
            blocks.append(Block("table", rows=rows))
            index = j + 1
            continue
        index += 1
    return blocks


def outline(blocks: list[Block]) -> list[tuple[int, str]]:
    return [(block.level, plain(block.inlines)) for block in blocks if block.kind == "heading"]


__all__ = ["Block", "Inline", "ListItem", "outline", "parse_blocks", "plain"]
