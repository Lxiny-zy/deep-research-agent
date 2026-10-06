"""Keep open-domain absence claims distinct from checks of named full texts."""

from __future__ import annotations

import re

from .delivery.math_markdown import citation_text

_DOMAIN_ABSENCE = re.compile(
    r"(?:本?领域|现有研究|已有研究|现有文献|已有文献|所有研究|所有文献|整个学界)"
    r"[^。!?！？；;]{0,45}(?:尚无|没有|从未|均未|未曾|尚未|不存在)"
    r"|(?:尚无|没有任何|不存在任何|从未有)(?:相关)?(?:研究|工作|方法|文献|论文)"
    r"|\b(?:no|not a single)\s+(?:(?:prior|previous|existing|published|other)\s+)?"
    r"(?:studies|study|research|works?|methods?|papers?)\s+"
    r"(?:have|has|exist|address|investigat|examin|consider|report)"
    r"|\b(?:the literature|this field|all studies)\b[^.!?;]{0,60}"
    r"\b(?:never|none|no research|not been)\b",
    re.I,
)
_ATTRIBUTED = re.compile(
    r"(?:作者|该文|论文|文献|原文)[^，,。;；]{0,16}(?:认为|指出|声称|主张|表述)"
    r"|\b(?:according to|the authors? (?:claim|state|argue|report))\b",
    re.I,
)
_NOT_ASSERTED = re.compile(
    r"是否|能否|待检索|需(?:要)?核查|不能[^，,]{0,8}(?:证明|推断)|不意味着|不代表"
    r"|\b(?:whether|cannot establish|does not imply)\b|[?？]",
    re.I,
)
_DOCUMENT_SCOPE = re.compile(
    r"本次取得的全文(?:文本)?中|所选论文的全文|本次检索范围内|在所选语料中|"
    r"in (?:the |these )?(?:provided|selected) full.texts?|"
    r"within (?:this search|the (?:retrieved|selected|provided) corpus)",
    re.I,
)


def field_absence_issue(text: str) -> str | None:
    protected = citation_text(text)
    clauses = re.split(
        r"[。！!；;\n]|(?<=[.?？])(?=\s|[\u4e00-\u9fff])\s*"
        r"|(?:但是|然而|但|\bbut\b|\bhowever\b)",
        protected,
        flags=re.I,
    )
    for clause in clauses:
        if not _DOMAIN_ABSENCE.search(clause) or _NOT_ASSERTED.search(clause):
            continue
        if _ATTRIBUTED.search(clause) or _DOCUMENT_SCOPE.search(clause):
            continue
        return (
            "领域全称否定不能由若干论文或短摘录证明；请限定并披露检索日期、语料和范围，"
            "或仅对已核查的目标全文说明本次未见，不能声称整个领域不存在相关研究"
        )
    return None
