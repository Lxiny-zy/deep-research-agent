"""Math token rules run before Markdown escape/emphasis can alter TeX."""

from __future__ import annotations

import re
from collections.abc import Callable

from markdown_it import MarkdownIt
from markdown_it.rules_block import StateBlock
from markdown_it.rules_inline import StateInline


def _closing(source: str, marker: str, start: int) -> int:
    index = source.find(marker, start)
    while index >= 0:
        before = index - 1
        while before >= 0 and source[before] == "\\":
            before -= 1
        if (index - before - 1) % 2 == 0:
            return index
        index = source.find(marker, index + 2)
    return -1


def _bracket_inline(state: StateInline, silent: bool) -> bool:
    opening = state.src[state.pos : state.pos + 2]
    if opening not in {r"\(", r"\["}:
        return False
    end = _closing(state.src, r"\)" if opening == r"\(" else r"\]", state.pos + 2)
    if end < 0:
        return False
    if not silent:
        token = state.push("math_inline" if opening == r"\(" else "math_inline_double", "math", 0)
        token.content = state.src[state.pos + 2 : end]
        token.markup = opening
    state.pos = end + 2
    return True


def _bracket_block(state: StateBlock, start: int, end: int, silent: bool) -> bool:
    begin = state.bMarks[start] + state.tShift[start]
    if state.sCount[start] - state.blkIndent >= 4 or not state.src.startswith(r"\[", begin):
        return False
    close = _closing(state.src, r"\]", begin + 2)
    if close < 0:
        return False
    last = start
    while last < end and state.eMarks[last] < close + 2:
        last += 1
    if last >= end or state.src[close + 2 : state.eMarks[last]].strip():
        return False
    if silent:
        return True
    token = state.push("math_block", "math", 0)
    token.block = True
    token.content = state.src[begin + 2 : close].strip()
    token.map = [start, last + 1]
    token.markup = r"\["
    state.line = last + 1
    return True


def math_plugin(md: MarkdownIt) -> None:
    from mdit_py_plugins.dollarmath import dollarmath_plugin

    md.use(
        dollarmath_plugin,
        allow_labels=False,
        allow_space=True,
        allow_digits=False,
        double_inline=True,
    )
    rules = dict(zip(md.inline.ruler.get_active_rules(), md.inline.ruler.getRules(""), strict=True))
    dollar_rule = rules["math_inline"]

    def dollar_math(state: StateInline, silent: bool) -> bool:
        start, count = state.pos, len(state.tokens)
        pending = state.pending
        matched = dollar_rule(state, silent)
        if matched and "`" in state.src[start : state.pos]:
            state.pos, state.pending = start, pending
            del state.tokens[count:]
            return False
        return matched

    md.inline.ruler.at("math_inline", dollar_math)
    md.inline.ruler.before("escape", "bracket_math", _bracket_inline)
    md.block.ruler.before(
        "fence",
        "bracket_math",
        _bracket_block,
        {"alt": ["paragraph", "reference", "blockquote", "list"]},
    )


def citation_text(text: str) -> str:
    """Mask non-prose without changing offsets, for both checking and rewriting.

    Parse block boundaries first, then inline rules over the masked source.
    Keeping source coordinates avoids rewriting indices, code and link targets
    when bibliography entries are renumbered after evidence filtering.
    """
    from .markdown import _parser

    md = _parser()
    chars = list(text)

    def mask(start: int, end: int) -> None:
        for index in range(start, end):
            if chars[index] not in "\r\n":
                chars[index] = " "

    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    environment: dict = {}
    for token in md.parse(text, environment):
        if token.map and token.type in {"math_block", "fence", "code_block"}:
            mask(offsets[token.map[0]], offsets[token.map[1]])

    def protect(rule: Callable[[StateInline, bool], bool]) -> Callable[[StateInline, bool], bool]:
        def wrapped(state: StateInline, silent: bool) -> bool:
            start = state.pos
            matched = rule(state, silent)
            if matched and not silent:
                mask(start, state.pos)
            return matched

        return wrapped

    names = md.inline.ruler.get_active_rules()
    rules = md.inline.ruler.getRules("")
    for name, rule in zip(names, rules, strict=True):
        if name in {
            "math_inline",
            "bracket_math",
            "backticks",
            "escape",
            "link",
            "image",
            "autolink",
        }:
            md.inline.ruler.at(name, protect(rule))
    # parseInline runs core normalization (CRLF -> LF), which would invalidate
    # source coordinates. The inline parser itself keeps the original offsets.
    md.inline.parse("".join(chars), md, environment, [])
    return "".join(chars)


def replace_citations(text: str, replace: Callable[[re.Match[str]], str]) -> str:
    """Replace only bibliography markers while preserving original Markdown."""
    pattern = re.compile(r"\[(\d+(?:\s*[,，]\s*\d+)*)\]")
    parts: list[str] = []
    start = 0
    for match in pattern.finditer(citation_text(text)):
        parts.extend((text[start : match.start()], replace(match)))
        start = match.end()
    return "".join(parts) + text[start:]


def validation_paragraphs(text: str) -> list[str]:
    """Keep blank lines inside display equations within the same check unit."""
    lines = text.splitlines(keepends=True)
    for start, (end, _) in math_blocks(text).items():
        for index in range(start + 1, end - 1):
            if not lines[index].strip():
                lines[index] = ""
    return re.split(r"\n\s*\n", "".join(lines))


def only_math(text: str) -> bool:
    from .markdown import _parser

    found = False
    for token in _parser().parse(text):
        if token.type == "math_block":
            found = True
        elif token.type == "fence":
            if token.info.strip() not in {"math", "latex", "tex"} or any(
                command in token.content for command in (r"\documentclass", r"\begin{document}")
            ):
                return False
            found = True
        elif token.type == "inline":
            for child in token.children or []:
                if child.type in {"math_inline", "math_inline_double"}:
                    found = True
                elif child.type == "text" and child.content.strip():
                    return False
    return found


def math_blocks(markdown: str) -> dict[int, tuple[int, str]]:
    """Line ranges let legacy exporters preserve their other Markdown behavior."""
    from .markdown import _parser

    result = {}
    for token in _parser().parse(markdown):
        is_math = token.type == "math_block" or (
            token.type == "fence"
            and token.info.strip() in {"math", "latex", "tex"}
            and not any(
                command in token.content for command in (r"\documentclass", r"\begin{document}")
            )
        )
        if token.map and is_math:
            result[token.map[0]] = (token.map[1], token.content.strip())
    return result
