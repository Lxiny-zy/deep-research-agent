"""Content-bound final-prose review; every paragraph, heading, row and code block.

The model judges support, while code owns coverage, source scope and version
binding. Nothing is sampled or silently cut to fit a context window.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict
from typing import Any

from ..models import ResearchResult
from .delivery.markdown import _parser, framing_paragraphs
from .delivery.math_markdown import citation_text, only_math
from .gates import _body_without_references
from .support import SupportDecision, SupportReviewer, SupportUnit, digest, evidence_records

PROSE_REVIEW_KEY = "prose_review"
_CITE = re.compile(r"\[(\d+(?:\s*[,，]\s*\d+)*)\]")


def body_text(markdown: str, *, strip_references: bool = True) -> str:
    from .territory import normalize

    text = markdown.replace("\r\n", "\n")
    return normalize((_body_without_references(text) if strip_references else text).strip())


def prose_units(
    markdown: str,
    available: list[int],
    *,
    uncited_sections: tuple[str, ...] = (),
    implicit: bool = False,
    translation_citations: list[int] | None = None,
) -> tuple[list[SupportUnit], list[dict[str, Any]]]:
    body = body_text(markdown, strip_references=not implicit)
    lines = body.splitlines()
    units: list[SupportUnit] = []
    locations: list[dict[str, Any]] = []
    duplicates: Counter[str] = Counter()
    headings: list[tuple[int, str]] = []
    table_header = ""
    table_caption = ""
    table_text = ""
    previous = ""
    in_header = False
    tokens = _parser().parse(body)
    framing_lines = framing_paragraphs(body)
    translation_ranges: dict[int, int] = {}
    if translation_citations is not None:
        from .paper_abstract import translation_title

        for index, token in enumerate(tokens):
            if (
                token.type != "heading_open"
                or not token.map
                or not translation_title(tokens[index + 1].content)
            ):
                continue
            stop = next(
                (
                    other.map[0]
                    for other in tokens[index + 1 :]
                    if other.type == "heading_open"
                    and other.map
                    and int(other.tag[1:]) <= int(token.tag[1:])
                ),
                len(lines),
            )
            translation_ranges[token.map[0]] = stop
    translation_segments: list[tuple[int, int]] = []
    for start, end in sorted(translation_ranges.items()):
        if not translation_segments or start >= translation_segments[-1][1]:
            translation_segments.append((start, end))
    first_translation = translation_segments[0][0] if translation_segments else None
    for index, token in enumerate(tokens):
        if (
            token.map
            and any(start <= token.map[0] < end for start, end in translation_segments)
            and not (token.type == "heading_open" and token.map[0] == first_translation)
        ):
            continue
        if token.type == "table_open" and token.map:
            table_caption = previous
            table_text = "\n".join(lines[token.map[0] : token.map[1]])
        if token.type == "thead_open":
            in_header = True
        elif token.type == "thead_close":
            in_header = False
        elif token.type == "table_close":
            table_header = ""
            previous = table_text
        if (
            token.type
            not in {
                "heading_open",
                "paragraph_open",
                "tr_open",
                "fence",
                "code_block",
                "math_block",
            }
            or not token.map
        ):
            continue
        start, end = token.map
        is_translation = token.type == "heading_open" and start == first_translation
        if is_translation:
            end = translation_segments[-1][1]
        text = (
            "\n\n".join("\n".join(lines[a:b]) for a, b in translation_segments)
            if is_translation
            else "\n".join(lines[start:end])
        ).strip()
        if not text:
            continue
        if token.type == "heading_open":
            level = int(token.tag[1:])
            title = tokens[index + 1].content
            headings = [(depth, name) for depth, name in headings if depth < level]
            headings.append((level, title))
            previous = ""
        if token.type == "tr_open" and in_header:
            table_header = text
        section = " / ".join(name for _, name in headings)
        kind = "translation" if is_translation else "prose"
        # Code literals and code spans are not bibliography references.
        cite_text = (
            "" if token.type in {"fence", "code_block", "math_block"} else citation_text(text)
        )
        cited = sorted(
            {int(n) for group in _CITE.findall(cite_text) for n in re.split(r"\s*[,，]\s*", group)}
        )
        if is_translation:
            cited = list(translation_citations or [])
        implicit_here = (
            implicit
            or token.type in {"heading_open", "math_block"}
            or only_math(text)
            or start in framing_lines
            or any(word.casefold() in section.casefold() for word in uncited_sections)
        )
        if not cited and implicit_here and not is_translation:
            cited, kind = list(available), "summary"
        context = section
        if token.type == "tr_open" and not in_header:
            context += "\n表题：" + table_caption + "\n表头：" + table_header
        elif previous and token.type != "heading_open":
            context += "\n前文（只用于理解指代，不是新证据）：" + previous
        key = digest([text, context, kind, cited])
        ordinal = duplicates[key]
        duplicates[key] += 1
        uid = f"p-{key[:20]}-{ordinal}"
        units.append(SupportUnit(uid, text, context=context, kind=kind, citations=cited))
        locations.append(
            {
                "id": uid,
                "start_line": start + 1,
                "end_line": end,
                "section": section,
                "kind": kind,
                "citations": cited,
            }
        )
        if token.type != "heading_open":
            previous = text
    return units, locations


def can_revise(decisions: list[SupportDecision]) -> bool:
    return not any(
        d.reason.startswith(
            (
                "核验调用失败",
                "完整证据超过核验模型输入容量",
                "核验节点缺失或重复",
                "核验未提供本节点可用的证据映射",
                "缺少完整摘要原文",
            )
        )
        for d in decisions
    )


class ProseReviewer:
    def __init__(
        self,
        llm: Any,
        evidence: list[dict[str, Any]],
        capacity: int,
        *,
        source_version: str,
        query: str = "",
        uncited_sections: tuple[str, ...] = (),
        implicit: bool = False,
        corroboration: bool = False,
        translation_citations: list[int] | None = None,
    ) -> None:
        self.evidence = evidence
        self.source_version = source_version
        self.query = query
        self.uncited_sections = uncited_sections
        self.implicit = implicit
        self.corroboration = corroboration
        self.capacity = capacity
        self.translation_citations = translation_citations
        self.reviewer = SupportReviewer(llm, evidence, capacity, context=f"用户问题：{query}")
        self.records: dict[str, dict[str, Any]] = {}

    def prime(self, markdown: str, record: dict[str, Any]) -> bool:
        """Reuse only bound, valid decisions; uncertain results never become facts."""
        bound, _ = self.check(markdown, record)
        if not bound:
            return False
        units, _ = self.units(markdown)
        decisions = {
            item["unit_id"]: SupportDecision.model_validate(item) for item in record["decisions"]
        }
        for unit in units:
            decision = decisions[unit.id]
            selected = [e for e in self.evidence if e["citation"] in unit.citations]
            allowed = {e["id"] for e in selected}
            if decision.verdict == "uncertain":
                continue
            if decision.verdict == "supported" and (
                not decision.evidence_ids or not set(decision.evidence_ids).issubset(allowed)
            ):
                continue
            if decision.verdict == "non_factual" and unit.kind in {"claim", "translation"}:
                continue
            self.reviewer.cache[digest([asdict(unit), selected])] = decision
        return True

    @classmethod
    def research(
        cls,
        llm: Any,
        results: list[ResearchResult],
        mapping: dict[str, int],
        capacity: int,
        *,
        query: str = "",
        uncited_sections: tuple[str, ...] = (),
        corroboration: bool = False,
        abstracts: list[dict[str, Any]] | None = None,
    ) -> ProseReviewer:
        evidence = evidence_records(results, mapping, corroboration=corroboration)
        for index, abstract in enumerate(abstracts or [], 1):
            evidence.append(
                {
                    "id": "abstract-" + digest(abstract),
                    "citation": -index,
                    "statement": "完整摘要原文，仅用于忠实翻译检查，不供其他章节引用",
                    "quote": abstract["text"],
                    "source": abstract["source_url"],
                    "reference": abstract["source_title"],
                }
            )
        return cls(
            llm,
            evidence,
            capacity,
            source_version=digest([r.material_data() for r in results]),
            query=query,
            uncited_sections=uncited_sections,
            corroboration=corroboration,
            translation_citations=list(range(-1, -len(abstracts) - 1, -1))
            if abstracts is not None
            else None,
        )

    def signature(self, markdown: str) -> str:
        return digest(
            {
                "version": 4 if self.translation_citations is not None else 3,
                "body": body_text(markdown, strip_references=False),
                "evidence": self.evidence,
                "source_version": self.source_version,
                "query": self.query,
                "uncited_sections": self.uncited_sections,
                "implicit": self.implicit,
                **(
                    {"translation_citations": self.translation_citations}
                    if self.translation_citations is not None
                    else {}
                ),
            }
        )

    def units(self, markdown: str) -> tuple[list[SupportUnit], list[dict[str, Any]]]:
        return prose_units(
            markdown,
            sorted({e["citation"] for e in self.evidence if e["citation"] >= 0}),
            uncited_sections=self.uncited_sections,
            implicit=self.implicit,
            translation_citations=self.translation_citations,
        )

    async def review(self, markdown: str) -> dict[str, Any]:
        signature = self.signature(markdown)
        content_key = self.signature(body_text(markdown, strip_references=not self.implicit))
        if content_key in self.records:
            # Adding code-owned bibliography text does not require rejudging
            # the same prose. The public record still binds the complete file.
            return {**self.records[content_key], "input_hash": signature}
        units, locations = self.units(markdown)
        decisions = await self.reviewer.review(units)
        problems = [d for d in decisions if d.verdict not in {"supported", "non_factual"}]
        by_id = {unit.id: unit for unit in units}
        positions = {loc["id"]: loc["start_line"] for loc in locations}
        record: dict[str, Any] = {
            "version": 4 if self.translation_citations is not None else 3,
            "input_hash": signature,
            "status": "fail" if problems or not units else "pass",
            "scope": "model_assessed_final_prose_support",
            "reviewer": self.reviewer.provenance,
            "units": locations,
            "decisions": [d.model_dump(mode="json") for d in decisions],
            "issues": [
                f"第 {positions[d.unit_id]} 行：{d.reason}（{by_id[d.unit_id].text[:100]}）"
                for d in problems
            ],
            "can_revise": can_revise(decisions),
            "require_corroboration": self.corroboration,
            "uncited_sections": list(self.uncited_sections),
        }
        if not units:
            record["issues"] = ["正文没有可核对内容"]
        if self.translation_citations is not None and not any(
            u.kind == "translation" for u in units
        ):
            record["status"] = "fail"
            record["issues"].append("缺少摘要翻译章节")
        if self.translation_citations == []:
            record["status"] = "fail"
            record["can_revise"] = False
            if not any("缺少完整摘要原文" in issue for issue in record["issues"]):
                record["issues"].append("缺少完整摘要原文，不能核验摘要翻译")
        # This memo exists only for one revision loop. Unknown remains unknown;
        # do not reroll a judgement just because finalization added references.
        self.records[content_key] = record
        return record

    def check(self, markdown: str, record: Any) -> tuple[bool, list[str]]:
        if not isinstance(record, dict) or record.get("input_hash") != self.signature(markdown):
            return False, ["正文、证据或核验规则已变更，原终稿核验记录不再适用"]
        units, locations = self.units(markdown)
        try:
            decisions = [SupportDecision.model_validate(d) for d in record.get("decisions", [])]
        except ValueError:
            return False, ["终稿核验记录无法解析"]
        expected = {u.id: u for u in units}
        if (
            not units
            or len(decisions) != len(expected)
            or {d.unit_id for d in decisions} != set(expected)
        ):
            return False, ["终稿核验未覆盖全部正文单元"]
        if record.get("units") != locations:
            return False, ["终稿核验定位与正文不一致"]
        problems = []
        if self.translation_citations is not None and not any(
            u.kind == "translation" for u in units
        ):
            problems.append("缺少摘要翻译章节")
        if self.translation_citations == []:
            problems.append("缺少完整摘要原文，不能核验摘要翻译")
        positions = {location["id"]: location["start_line"] for location in locations}
        for d in decisions:
            allowed = {
                e["id"] for e in self.evidence if e["citation"] in expected[d.unit_id].citations
            }
            if d.verdict not in {"supported", "non_factual"}:
                problems.append(f"第 {positions[d.unit_id]} 行：{d.reason}")
            elif d.verdict == "non_factual" and expected[d.unit_id].kind == "translation":
                problems.append(f"第 {positions[d.unit_id]} 行：译文未完成忠实性与完整性核对")
            elif d.verdict == "supported" and (
                not d.evidence_ids or not set(d.evidence_ids).issubset(allowed)
            ):
                problems.append(f"第 {positions[d.unit_id]} 行：终稿证据映射超出该单元引用范围")
        return True, problems


def reviewer_for_report(
    llm: Any,
    query: str,
    results: list[ResearchResult],
    citations: list[str],
    scratch: dict,
    capacity: int,
    *,
    corroboration: bool = False,
) -> ProseReviewer | None:
    from .contract import contract_from_scratch
    from .paper_abstract import checked_abstracts
    from .scholarly import uncited_sections_for

    workbench = scratch.get("workbench", {})
    if workbench.get("template") == "mindmap":
        return None
    ledger = scratch.get("analysis")
    if workbench.get("template") == "dataAnalysis" and isinstance(ledger, dict) and ledger:
        from .analysis import ledger_facts

        version = digest(ledger)
        payload = {
            "computed_facts": ledger_facts(ledger),
            "user_input_description": query,
            "data_origin": "程序生成的演示数据" if ledger.get("synthetic") else "用户提供的数据",
            "origin_semantics": (
                "是否由程序生成演示数据，不判断用户数据来自真实测量还是合成；"
                "数据性质须保留用户声明范围。"
            ),
            "analysis_scope": "本次已执行的计算均列在台账中；未列出的统计分析未执行。",
        }
        text = "本次任务冻结的计算记录（不是外部研究结论）：\n" + json.dumps(
            payload, ensure_ascii=False
        )
        evidence = [
            {
                "id": version,
                "citation": 0,
                "statement": "本次统计台账",
                "quote": text,
                "source": "computed-run-statistics",
            }
        ]
        return ProseReviewer(
            llm, evidence, capacity, source_version=version, query=query, implicit=True
        )
    contract = contract_from_scratch(scratch)
    paper_read = workbench.get("template") == "paperRead" or bool(
        contract and contract.template == "paperRead"
    )
    if (not results or not citations) and not paper_read:
        return None
    corroboration = corroboration or bool(
        (stored_review(scratch) or {}).get("require_corroboration")
    )
    uncited = uncited_sections_for(scratch)
    if contract_from_scratch(scratch) is None:
        raw_sections = (stored_review(scratch) or {}).get("uncited_sections", [])
        if isinstance(raw_sections, list) and all(isinstance(item, str) for item in raw_sections):
            uncited = tuple(raw_sections)
    return ProseReviewer.research(
        llm,
        results,
        {url: i for i, url in enumerate(citations, 1)},
        capacity,
        query=query,
        uncited_sections=uncited,
        corroboration=corroboration,
        abstracts=checked_abstracts(scratch) if paper_read else None,
    )


def stored_review(scratch: dict) -> Any:
    return scratch.get(PROSE_REVIEW_KEY) or scratch.get("workbench", {}).get("extras", {}).get(
        PROSE_REVIEW_KEY
    )
