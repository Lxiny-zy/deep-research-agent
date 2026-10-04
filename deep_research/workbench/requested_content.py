"""Freeze explicit user requirements independently of a template's default outline."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

REQUIREMENTS_VERSION = 1
_ACTIONS = (
    r"解释|说明|提炼|比较|对比|讨论|回答|分析|总结|写清|列出|包含|包括|覆盖|明确|关注|核对|处理|"
    r"explain|describe|compare|discuss|answer|include|summari[sz]e|analy[sz]e|list"
)
_PREFIX = (
    r"(?:请(?:你)?|务必|需要|要求|必须|重点|准确|详细|简要|同时|并|进一步|用中文|用英文|"
    r"please|also|and|briefly|specifically|in Chinese|in English|I want you to|"
    r"I would like you to|could you|can you|the report should|the table should|\s)*"
)
_COMMAND = re.compile(
    r"(?:^|[，,]\s*)"
    + _PREFIX
    + rf"(?P<action>{_ACTIONS})\s*(?P<items>.+?)"
    + rf"(?=[，,]\s*{_PREFIX}(?:{_ACTIONS})|$)",
    re.I,
)
_TABLE = re.compile(r"对比表|比较表|表格|表\s*\d+|\btable\b", re.I)
_NEGATIVE = re.compile(
    r"^(?:不需要|无需|不要(?!遗漏|漏掉)|不得(?!遗漏)|不能|不必|避免|do not\b|don't\b|without\b)",
    re.I,
)
_KIND_LABEL = {
    "overall": "整体目标",
    "content": "需说明",
    "question": "需回答",
    "comparison": "需比较",
    "section": "指定章节",
    "branch": "指定分支",
    "table_column": "表格字段",
}


class RequestedItem(BaseModel):
    id: str
    kind: Literal[
        "overall", "content", "question", "comparison", "section", "branch", "table_column"
    ]
    label: str = Field(min_length=1)
    source_quote: str
    origin: Literal["request", "confirmed_choice"] = "request"
    choice_key: str = ""
    group: str = ""
    table_hint: str = ""
    table_only: bool = False
    exact_name: bool = False


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def _items(text: str) -> list[str]:
    text = re.sub(
        r"^(?:以下|这些|至少|包括|包含|如下|the following)\s*[:：]?\s*", "", text, flags=re.I
    )
    values = re.split(r"[、，,|]|以及|并且|及|与|和|\band\b", text)
    cleaned = []
    for value in values:
        value = value.strip(" \t\r\n：:。.;；‘’“”\"'「」【】（）()")
        value = re.sub(r"(?:等内容|等方面|等)$", "", value).strip()
        if value:
            cleaned.append(value)
    return cleaned


def extract_requested_items(
    request: str, choices: dict[str, Any] | None = None
) -> list[RequestedItem]:
    result: list[RequestedItem] = []
    seen: set[tuple[str, str, str]] = set()

    def add(kind: str, label: str, quote: str, **options: Any) -> None:
        label = label.strip()
        if kind == "content" and re.fullmatch(
            r"(?:规范|中文|可阅读的)?(?:PDF|Word|Markdown|HTML|报告|图示|图表|示意图)", label, re.I
        ):
            return  # Existing format/figure contracts own these outputs.
        key = (kind, label.casefold(), options.get("group", ""))
        if not label or key in seen:
            return
        seen.add(key)
        result.append(
            RequestedItem(
                id="r-" + _hash([kind, label, quote, options])[:16],
                kind=kind,
                label=label,
                source_quote=quote,
                **options,
            )
        )

    # URLs and fenced document examples are not authored coverage instructions.
    masked = re.sub(r"```[\s\S]*?```", lambda match: " " * len(match[0]), request)
    masked = re.sub(r"https?://[^\s<>，。；！？、,]+", lambda match: " " * len(match[0]), masked)
    table_groups: list[tuple[str, str]] = []
    for match in re.finditer(r"[^。！？!?；;\n]+[？?]?", masked):
        raw = request[match.start() : match.end()].strip()
        sentence = re.sub(r"^\s*(?:[-*]|\d+[.)、]|[一二三四五六七八九十]+[、.])\s*", "", raw)
        sentence = re.sub(r"[ \t]+", " ", sentence)
        if not sentence or _NEGATIVE.match(sentence):
            continue
        commands = list(_COMMAND.finditer(sentence))
        handled_actions: set[int] = set()
        table = _TABLE.search(sentence)
        table_hint = ""
        if number := re.search(r"(?:表|Table)\s*(\d+)", sentence, re.I):
            table_hint = "表" + number[1]
        if table:
            fields = list(
                re.finditer(
                    r"(?:写清|列出|\blist\b|(?:字段|列名)(?:为|包括|包含|[:：])|columns?(?:\s+(?:are|include))?)\s*[:：]?\s*",
                    sentence,
                    re.I,
                )
            )
            if not fields:
                fields = list(re.finditer(r"(?:包含|包括|include)\s*[:：]?\s*", sentence, re.I))
            if fields:
                group = "t-" + _hash(raw)[:12]
                table_groups.append((group, table_hint))
                for field in fields:
                    end = min(
                        (
                            command.start()
                            for command in commands
                            if command.start("action") >= field.end()
                        ),
                        default=len(sentence),
                    )
                    for label in _items(sentence[field.end() : end]):
                        add(
                            "table_column",
                            label,
                            raw,
                            group=group,
                            table_hint=table_hint,
                            table_only=True,
                        )
                    handled_actions.update(
                        command.start("action")
                        for command in commands
                        if field.start() <= command.start("action") < field.end()
                    )
        if re.search(r"章节|小节|分支|\b(?:sections?|branches)\b", sentence, re.I):
            section_kind = (
                "branch" if re.search(r"分支|\bbranches\b", sentence, re.I) else "section"
            )
            titles = re.findall(r"[「‘“\"]([^」’”\"]+)[」’”\"]", sentence)
            if not titles:
                listing = re.search(
                    r"(?:章节|小节|分支|sections?|branches)(?:包括|包含|为|分别为|\s+include|\s+are)\s*[:：]?\s*(.+)|(?:章节|小节|分支|sections?|branches)\s*[:：]\s*(.+)",
                    sentence,
                    re.I,
                )
                listed = listing[1] or listing[2] if listing else ""
                listed = re.split(
                    rf"[，,]\s*{_PREFIX}(?:{_ACTIONS})", listed, maxsplit=1, flags=re.I
                )[0]
                titles = _items(listed)
            for title in titles:
                add(section_kind, title, raw, exact_name=True)
            if titles:
                handled_actions.update(
                    command.start("action")
                    for command in commands
                    if re.match(r"章节|小节|分支|sections?\b|branches\b", command["items"], re.I)
                )
        if not commands and sentence.startswith("结合"):
            nested = re.search(r"(?P<action>核对|说明|分析)\s*(?P<items>.+)", sentence)
            if nested:
                commands.append(nested)
        for command in commands:
            if command.start("action") in handled_actions:
                continue
            content = command["items"]
            # Stop at a later explicit command, but retain ordinary list commas.
            content = re.split(
                rf"[，,]\s*{_PREFIX}(?:{_ACTIONS})", content, maxsplit=1, flags=re.I
            )[0]
            kind = (
                "comparison"
                if command["action"].casefold() in {"比较", "对比", "compare"}
                else "content"
            )
            for label in _items(content):
                item_kind = (
                    "question"
                    if re.search(
                        r"如何|为何|为什么|是否|能否|什么|区别|[?？]|\b(?:what|why|how)\b",
                        label,
                        re.I,
                    )
                    else kind
                )
                add(item_kind, label, raw)
        if not commands and re.match(
            r"(?:Q\d+\s*[:：]|如何|为何|为什么|什么|是否|能否|what\b|why\b|how\b)", sentence, re.I
        ):
            add("question", sentence, raw)
        elif re.match(r"(?:重点|关注点|必须覆盖|不要遗漏)\s*[:：]", sentence):
            for label in _items(sentence.split("：", 1)[-1].split(":", 1)[-1]):
                add("content", label, raw)

    # A named supplied-paper list is a concrete set of comparison targets.
    if table_groups:
        for match in re.finditer(
            r"上传的\s*(.+?)\s*(?:[一二三四五六七八九十\d]+篇)?(?:论文|文献)", request
        ):
            names = _items(match[1])
            for group, hint in table_groups:
                for name in names:
                    add(
                        "comparison",
                        name,
                        match[0],
                        group=group,
                        table_hint=hint,
                        table_only=True,
                        exact_name=True,
                    )
    kinds = {
        "sections": "section",
        "required_sections": "section",
        "branches": "branch",
        "columns": "table_column",
        "table_columns": "table_column",
        "comparison_targets": "comparison",
        "questions": "question",
    }
    for key, kind in kinds.items():
        value = (choices or {}).get(key)
        values = (
            value if isinstance(value, list) else _items(value) if isinstance(value, str) else []
        )
        for label in values:
            if isinstance(label, str) and label.strip():
                add(
                    kind,
                    label,
                    label,
                    origin="confirmed_choice",
                    choice_key=key,
                    table_only=kind == "table_column",
                    exact_name=kind == "section",
                )
    if result and request.strip():
        add("overall", "整体内容目标", request)
    return result


def requirements_hash(
    request: str,
    choices: dict[str, Any],
    items: list[RequestedItem],
    instructions: str | None = None,
) -> str:
    return _hash(
        {
            "version": REQUIREMENTS_VERSION,
            "request": request,
            "choices": choices,
            "items": [item.model_dump(mode="json") for item in items],
            "instructions": request if instructions is None else instructions,
        }
    )


def instruction_text(request: str, *, document: bool = False) -> str:
    marker = re.search(
        r"(?:以下(?:是|为)?(?:论文|文献)(?:原文|正文)|(?:论文|文献)(?:原文|正文)(?:如下)?|"
        r"paper text|source text)\s*[:：]",
        request,
        re.I,
    )
    if marker:
        return request[: marker.start()].strip()
    return "" if document else request


def render_requested_items(items: list[RequestedItem]) -> list[str]:
    return [
        f"- [{_KIND_LABEL[item.kind]}] "
        + (item.table_hint + "：" if item.table_hint else "") + item.label
        for item in items
        if item.kind != "overall"
    ]
