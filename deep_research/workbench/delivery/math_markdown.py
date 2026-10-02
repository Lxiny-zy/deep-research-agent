"""Math token rules run before Markdown escape/emphasis can alter TeX."""

from __future__ import annotations

import re
from collections.abc import Callable

from markdown_it import MarkdownIt
from markdown_it.rules_block import StateBlock
from markdown_it.rules_inline import StateInline

_INTERVAL_NUMBER = r"[-+−]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
_INTERVAL = re.compile(rf"\[\s*{_INTERVAL_NUMBER}\s*[,，]\s*{_INTERVAL_NUMBER}\s*\]")
_INTERVAL_PREFIX = re.compile(
    r"(?:\b(?:range|interval)\s*(?:of|is|[:=])?"
    r"|\b(?:rescaled|scaled|normalized|normalised|mapped|clipped)\s+(?:linearly\s+)?(?:to|into|within)"
    r"|(?:范围|区间)(?:设为|为|是|[:：=])?"
    r"|(?:归一化|缩放|映射|限制)(?:到|至|为|在))\s*$",
    re.I,
)


def interval_spans(text: str) -> list[tuple[int, int]]:
    """Explicitly introduced numeric intervals are data, not reference clusters."""
    spans = []
    for match in _INTERVAL.finditer(text):
        prefix = text[max(0, match.start() - 120) : match.start()].rstrip()
        prefix = re.sub(r"[*_]+$", "", prefix).rstrip()
        if _INTERVAL_PREFIX.search(prefix):
            spans.append(match.span())
    return spans


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
    for start, end in interval_spans("".join(chars)):
        mask(start, end)
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
    """Keep equations and their continued sentence within the same check unit."""
    lines = text.splitlines(keepends=True)
    ranges = {start: end for start, (end, _) in math_blocks(text).items()}
    ranges.update(equation_prose_spans(text))
    for start, end in ranges.items():
        for index in range(start + 1, end - 1):
            if not lines[index].strip():
                lines[index] = ""
    return re.split(r"\n\s*\n", "".join(lines))


def equation_prose_spans(text: str) -> dict[int, int]:
    """Join an unfinished sentence, display math and its cited continuation.

    This changes the unit boundary, never exempts its facts or numbers. Only
    adjacent top-level blocks qualify; a heading, list, table, code block or
    completed sentence cannot borrow the next paragraph's citation.
    """
    from .markdown import _parser

    blocks = [token for token in _parser().parse(text) if token.level == 0 and token.map]
    lines = text.splitlines()
    spans: dict[int, int] = {}
    covered_until = -1
    for index, lead in enumerate(blocks):
        if lead.type != "paragraph_open" or not lead.map or lead.map[0] < covered_until:
            continue
        after = index + 1
        while after < len(blocks) and blocks[after].type == "math_block":
            after += 1
        if after == index + 1 or after >= len(blocks):
            continue
        tail = blocks[after]
        if tail.type != "paragraph_open" or not tail.map:
            continue
        before = replace_citations(
            "\n".join(lines[lead.map[0] : lead.map[1]]), lambda _: ""
        ).rstrip()
        continuation = "\n".join(lines[tail.map[0] : tail.map[1]]).lstrip()
        if re.search(r"[。.!?！？；;][\"'”’）)*_~]*$", before) or not re.match(
            r"(?:其中|式中|这里|进行(?:优化|训练|估计|计算)|where\b|with\b)", continuation, re.I
        ):
            continue
        if not re.search(r"\[\d+(?:\s*[,，]\s*\d+)*\]", citation_text(continuation)):
            continue
        spans[lead.map[0]] = tail.map[1]
        covered_until = tail.map[1]
    return spans


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
