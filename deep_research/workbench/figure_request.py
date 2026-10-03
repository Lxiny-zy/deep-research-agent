"""Recognize explicit task instructions about optional companion diagrams."""

from __future__ import annotations

import re

_FIGURE = r"(?:概念图(?:示)?|示意图|流程图|图示|配图|图表|图片)"
_DIRECTIVE = re.compile(
    r"(?:^|[\n。！？；，,.!?;])\s*"
    r"(?:请\s*|本次\s*|这次\s*|现在\s*|报告中\s*|please\s+)?"
    r"(?:(?P<no_zh>不需要|无需|不要|不必|不)"
    r"(?:\s|额外|配套|任何|再|添加|生成|绘制|提供|加入|的)*"
    + _FIGURE
    + r"|(?P<yes_zh>需要|请添加|请生成|添加|生成|绘制|提供|补充|加入)"
    r"(?:\s|额外|配套|一张|一个|的)*" + _FIGURE + r"|(?P<no_en>no|do\s+not|don't)\s+"
    r"(?:(?:add|generate|include|draw|provide|a|an|the|any|extra|additional|companion|conceptual)\s+)*"
    r"(?:diagrams?|figures?|illustrations?)\b"
    r"|(?P<yes_en>add|generate|include|draw|provide)\s+"
    r"(?:(?:a|an|the|extra|additional|companion|conceptual)\s+)*"
    r"(?:diagrams?|figures?|illustrations?)\b)",
    re.I,
)


def concept_figure_enabled(query: str) -> bool:
    """Last explicit directive wins; unrecognized wording keeps the default."""
    enabled = True
    for match in _DIRECTIVE.finditer(query):
        enabled = not bool(match["no_zh"] or match["no_en"])
    return enabled
