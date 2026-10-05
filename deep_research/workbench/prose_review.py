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

from ..document_corpus import FullTextCorpus, corpus_from_inputs
from ..models import ResearchResult, Source
from .delivery.markdown import _parser, framing_paragraphs, parse_blocks
from .delivery.math_markdown import citation_text, equation_prose_spans, only_math
from .gates import _body_without_references
from .support import (
    SUPPORT_POLICY_VERSION,
    SupportDecision,
    SupportReviewer,
    SupportUnit,
    asserted_comparison,
    digest,
    evidence_records,
)
from .support_alignment import numeric_fact

PROSE_REVIEW_KEY = "prose_review"
_CITE = re.compile(r"\[(\d+(?:\s*[,，]\s*\d+)*)\]")
_TABLE_REF = re.compile(
    r"(?<![A-Za-z])(?:Table|Tab\.?|表)\s*(\d+[a-z]?|[A-Z]\d*|[一二三四五六七八九十百]+)"
    r"(?![A-Za-z0-9])",
    re.I,
)


def _report_tables(markdown: str) -> dict[str, list[dict[str, str]]]:
    """Own table labels require an adjacent caption and an actual Markdown table."""
    tokens = _parser().parse(markdown)
    lines = markdown.splitlines()
    captions = [
        token
        for token in tokens
        if token.type in {"paragraph_open", "heading_open"} and token.level == 0 and token.map
    ]
    tables: dict[str, list[dict[str, str]]] = {}
    for token in tokens:
        if token.type != "table_open" or token.level != 0 or not token.map:
            continue
        caption = next(
            (
                candidate
                for candidate in reversed(captions)
                if candidate.map and candidate.map[1] <= token.map[0]
            ),
            None,
        )
        if caption is None or caption.map is None:
            continue
        if any(line.strip() for line in lines[caption.map[1] : token.map[0]]):
            continue
        title = "\n".join(lines[caption.map[0] : caption.map[1]])
        plain_title = " ".join(block.plain() for block in parse_blocks(title))
        match = _TABLE_REF.match(plain_title.strip())
        if match is None:
            continue
        tables.setdefault(match[1].casefold(), []).append(
            {"caption": title, "table": "\n".join(lines[token.map[0] : token.map[1]])}
        )
    return tables


def _table_context(text: str, tables: dict[str, list[dict[str, str]]]) -> str:
    mentioned = {m[1].casefold() for m in _TABLE_REF.finditer(citation_text(text))}
    selected = {label: entries for label, entries in tables.items() if label in mentioned}
    if not selected:
        return ""
    return (
        "\n本报告内的表格编号与内容（只用于辨认报告自身的表号、排布和内部对应关系，"
        "不是来源论文中的表号或新增事实证据；明确提到原文/作者的表格仍须按来源证据核对）：\n"
        + json.dumps(selected, ensure_ascii=False)
    )


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
    equation_spans = equation_prose_spans(body)
    report_tables = _report_tables(body)
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
        if token.map and any(start < token.map[0] < end for start, end in equation_spans.items()):
            continue
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
        if token.type == "paragraph_open":
            end = equation_spans.get(start, end)
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
        if token.type not in {"fence", "code_block", "math_block"} and not is_translation:
            context += _table_context(text, report_tables)
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
                "公式专门核验未完成",
                "全文核查未完成",
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
        statistics: dict[str, Any] | None = None,
        fulltext_corpus: FullTextCorpus | None = None,
        contract: Any = None,
        material_scratch: dict[str, Any] | None = None,
    ) -> None:
        self.evidence = evidence
        self.source_version = source_version
        self.query = query
        self.uncited_sections = uncited_sections
        self.implicit = implicit
        self.corroboration = corroboration
        self.capacity = capacity
        self.translation_citations = translation_citations
        self.statistics = statistics
        from .coverage_review import CoverageReviewer, effective_contract

        self.contract = effective_contract(contract, material_scratch)
        self.material_scratch = material_scratch or {}
        self.coverage = (
            CoverageReviewer(llm, self.contract, capacity) if self.contract is not None else None
        )
        from .analysis_review import STATISTICS_RULES

        self.reviewer = SupportReviewer(
            llm,
            evidence,
            capacity,
            context=f"用户问题：{query}",
            system_rules=STATISTICS_RULES if statistics is not None else "",
            fulltext_corpus=fulltext_corpus,
            check_fulltext=statistics is None,
            check_formulas=statistics is None,
        )
        self.records: dict[str, dict[str, Any]] = {}
        self.peer = None
        if self.contract is not None and self.contract.template == "peerReview":
            from .peer_review_items import PeerReviewChecker

            self.peer = PeerReviewChecker(
                llm, evidence, capacity, query=query,
                fulltext_support=self.reviewer.fulltext_supports,
                source_version=digest([source_version, self.reviewer.fulltext_corpus.fingerprint]),
            )

    def requirement_bases(self, markdown: str, record: dict[str, Any]) -> list[dict[str, Any]]:
        from .coverage_review import material_bases

        return material_bases(self.material_scratch, record, self.units(markdown)[0])

    async def _with_requirements(self, markdown: str, record: dict[str, Any]) -> dict[str, Any]:
        if self.coverage is not None:
            bases = self.requirement_bases(markdown, record)
            coverage = await self.coverage.review(markdown, bases)
            record = {**record, "requirements_review": coverage}
        if self.peer is not None:
            units, locations = self.units(markdown)
            decisions = [SupportDecision.model_validate(d) for d in record.get("decisions", [])]
            previous = record.get("peer_review")
            bound, _ = self.peer.bound_check(markdown, units, locations, decisions, previous)
            peer = (
                previous if bound and isinstance(previous, dict)
                else await self.peer.review(markdown, units, locations, decisions)
            )
            original_issues = record.get("prose_issues", record["issues"])
            original_status = record.get("prose_status", record["status"])
            original_revise = record.get("prose_can_revise", record["can_revise"])
            record = {**record, "peer_review": peer,
                      "prose_issues": original_issues, "prose_status": original_status,
                      "prose_can_revise": original_revise,
                      "issues": list(dict.fromkeys([*original_issues, *peer["issues"]])),
                      "status": "fail" if peer["status"] == "fail" else original_status,
                      "can_revise": peer["can_revise"] and (
                          original_status == "pass" or original_revise)}
        return record

    def check_requirements(self, markdown: str, record: Any) -> list[str]:
        from .coverage_review import coverage_issues

        raw = record if isinstance(record, dict) else {}
        return coverage_issues(
            self.contract,
            markdown,
            raw.get("requirements_review"),
            self.requirement_bases(markdown, raw),
        )

    def prime(self, markdown: str, record: dict[str, Any] | None) -> bool:
        """Reuse only bound, valid decisions; uncertain results never become facts."""
        if not isinstance(record, dict) or record.get("model_review_skipped"):
            return False
        bound, _ = self.check(markdown, record)
        if not bound:
            return False
        units, _ = self.units(markdown)
        if self.peer is not None and isinstance(record.get("peer_review"), dict):
            self.peer.prime(
                markdown, *self.units(markdown),
                [SupportDecision.model_validate(d) for d in record.get("decisions", [])],
                record["peer_review"],
            )
        decisions = {
            item["unit_id"]: SupportDecision.model_validate(item) for item in record["decisions"]
        }
        for unit in units:
            if unit.id in record.get("deferred_units", []):
                continue
            decision = decisions[unit.id]
            selected = [e for e in self.evidence if e["citation"] in unit.citations]
            allowed = {e["id"] for e in selected}
            if decision.verdict == "uncertain":
                continue
            if decision.verdict in {"supported", "non_factual"} and self.reviewer.record_issue(
                unit, decision
            ):
                continue
            fulltext_only = not decision.evidence_ids and self.reviewer.fulltext_supports(
                unit, decision
            )
            if decision.verdict == "supported" and (
                (not decision.evidence_ids and not fulltext_only)
                or not set(decision.evidence_ids).issubset(allowed)
                or (not fulltext_only and self.reviewer.alignment_issue(unit, decision))
            ):
                continue
            if decision.verdict == "non_factual" and unit.kind in {"claim", "translation"}:
                continue
            if decision.verdict == "non_factual" and asserted_comparison(unit.text):
                continue
            if decision.verdict == "non_factual" and numeric_fact(unit.text):
                continue
            self.reviewer.cache[digest([asdict(unit), selected])] = decision
            if decision.fulltext_review:
                self.reviewer.fulltext_records[unit.id] = decision.fulltext_review
            if decision.formula_review:
                self.reviewer.formula_records[unit.id] = decision.formula_review
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
        scratch: dict[str, Any] | None = None,
        sources: list[Source] | None = None,
    ) -> ProseReviewer:
        from .contract import contract_from_scratch

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
            fulltext_corpus=corpus_from_inputs(results, mapping, scratch, sources),
            contract=contract_from_scratch(scratch or {}),
            material_scratch=scratch,
        )

    def signature(self, markdown: str) -> str:
        from .analysis_review import STATISTICS_POLICY_VERSION
        from .fulltext_review import requires_fulltext

        uses_fulltext = self.reviewer.check_fulltext and any(
            requires_fulltext(unit) for unit in self.units(markdown)[0]
        )

        return digest(
            {
                "version": 4 if self.translation_citations is not None else 3,
                "support_policy": SUPPORT_POLICY_VERSION,
                **({"peer_review_policy": self.peer.policy_version}
                   if self.peer is not None else {}),
                "body": body_text(markdown, strip_references=False),
                "evidence": self.evidence,
                "source_version": self.source_version,
                "formula_scopes": [
                    scope
                    for unit in self.units(markdown)[0]
                    if (scope := self.reviewer.formula_scope_hash(unit)) is not None
                ],
                "fulltext_corpus": self.reviewer.fulltext_corpus.fingerprint
                if uses_fulltext
                else None,
                "query": self.query,
                "uncited_sections": self.uncited_sections,
                "implicit": self.implicit,
                **(
                    {"statistics_policy": STATISTICS_POLICY_VERSION}
                    if self.statistics is not None
                    else {}
                ),
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

    def cached_review(self, markdown: str) -> dict[str, Any] | None:
        key = self.signature(body_text(markdown, strip_references=not self.implicit))
        cached = self.records.get(key)
        return {**cached, "input_hash": self.signature(markdown)} if cached is not None else None

    def diagnostic_record(self, markdown: str) -> dict[str, Any]:
        """Bind an explicit failure to diagnostic material without another model call."""
        return {
            "version": 4 if self.translation_citations is not None else 3,
            "input_hash": self.signature(markdown),
            "status": "fail",
            "scope": "unreviewed_diagnostic_material",
            "model_review_skipped": True,
            "mechanically_finalized": True,
            "body_replaced": True,
            "issues": ["任务正文未通过，证据摘录仅用于诊断，未作为正式成品重新核验"],
            "can_revise": False,
            "require_corroboration": self.corroboration,
            "uncited_sections": list(self.uncited_sections),
        }

    async def review(
        self, markdown: str, *, deferred: dict[str, str] | None = None
    ) -> dict[str, Any]:
        deferred = deferred or {}
        signature = self.signature(markdown)
        content_key = self.signature(body_text(markdown, strip_references=not self.implicit))
        if not deferred and (cached := self.cached_review(markdown)):
            # Adding code-owned bibliography text does not require rejudging
            # the same prose. The public record still binds the complete file.
            return await self._with_requirements(markdown, cached)
        units, locations = self.units(markdown)
        if not set(deferred).issubset(unit.id for unit in units):
            raise ValueError("机械问题定位包含未知正文单元")
        checked = await self.reviewer.review([unit for unit in units if unit.id not in deferred])
        by_unit = {decision.unit_id: decision for decision in checked}
        decisions = [
            SupportDecision(unit_id=unit.id, verdict="unsupported", reason=deferred[unit.id])
            if unit.id in deferred
            else by_unit[unit.id]
            for unit in units
        ]
        problems = [d for d in decisions if d.verdict not in {"supported", "non_factual"}]
        by_id = {unit.id: unit for unit in units}
        positions = {loc["id"]: loc["start_line"] for loc in locations}
        record: dict[str, Any] = {
            "version": 4 if self.translation_citations is not None else 3,
            "input_hash": signature,
            "status": "fail" if problems or not units else "pass",
            "scope": "model_assessed_partial_prose_support"
            if deferred
            else "model_assessed_final_prose_support",
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
            "protocol_repairs": list(self.reviewer.protocol_repairs),
            **({"deferred_units": list(deferred)} if deferred else {}),
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
        if self.statistics is not None:
            from .analysis_review import statistic_scope_issues
            from .statistic_bindings import bind_statistics

            record["statistics_bindings"] = bind_statistics(markdown, self.statistics)
            scope_issues = statistic_scope_issues(markdown, self.statistics)
            if scope_issues:
                record["status"] = "fail"
                record["issues"].extend(scope_issues)
        # This memo exists only for one revision loop. Unknown remains unknown;
        # do not reroll a judgement just because finalization added references.
        record = await self._with_requirements(markdown, record)
        if not deferred:
            self.records[content_key] = record
        return record

    def check(self, markdown: str, record: Any) -> tuple[bool, list[str]]:
        if not isinstance(record, dict) or record.get("input_hash") != self.signature(markdown):
            return False, ["正文、证据或核验规则已变更，原终稿核验记录不再适用"]
        if record.get("model_review_skipped"):
            return True, ["诊断摘录未进行成品核验，不能作为正式报告交付"]
        units, locations = self.units(markdown)
        try:
            decisions = [SupportDecision.model_validate(d) for d in record.get("decisions", [])]
        except ValueError:
            return False, ["终稿核验记录无法解析"]
        expected = {u.id: u for u in units}
        deferred = record.get("deferred_units", [])
        if not isinstance(deferred, list) or any(
            not isinstance(uid, str) or uid not in expected for uid in deferred
        ):
            return False, ["机械问题定位与正文不一致"]
        if (
            not units
            or len(decisions) != len(expected)
            or {d.unit_id for d in decisions} != set(expected)
        ):
            return False, ["终稿核验未覆盖全部正文单元"]
        if record.get("units") != locations:
            return False, ["终稿核验定位与正文不一致"]
        from .analysis_review import statistic_scope_issues

        problems = (
            statistic_scope_issues(markdown, self.statistics) if self.statistics is not None else []
        )
        if self.statistics is not None:
            from .statistic_bindings import bind_statistics

            if record.get("statistics_bindings") != bind_statistics(markdown, self.statistics):
                problems.append("统计数值绑定记录缺失或与当前正文/台账不一致")
        if self.translation_citations is not None and not any(
            u.kind == "translation" for u in units
        ):
            problems.append("缺少摘要翻译章节")
        if self.translation_citations == []:
            problems.append("缺少完整摘要原文，不能核验摘要翻译")
        positions = {location["id"]: location["start_line"] for location in locations}
        for d in decisions:
            unit = expected[d.unit_id]
            if d.unit_id in deferred and d.verdict in {"supported", "non_factual"}:
                problems.append(f"第 {positions[d.unit_id]} 行：此单元仍待机械修订后的断言核验")
                continue
            fulltext_only = not d.evidence_ids and self.reviewer.fulltext_supports(unit, d)
            allowed = {
                e["id"] for e in self.evidence if e["citation"] in expected[d.unit_id].citations
            }
            if d.verdict not in {"supported", "non_factual"}:
                problems.append(f"第 {positions[d.unit_id]} 行：{d.reason}")
            elif d.verdict == "non_factual" and expected[d.unit_id].kind == "translation":
                problems.append(f"第 {positions[d.unit_id]} 行：译文未完成忠实性与完整性核对")
            elif d.verdict == "non_factual" and asserted_comparison(expected[d.unit_id].text):
                problems.append(f"第 {positions[d.unit_id]} 行：事实性比较被错误归类为纯编排说明")
            elif d.verdict == "non_factual" and numeric_fact(expected[d.unit_id].text):
                problems.append(f"第 {positions[d.unit_id]} 行：数值事实被错误归类为纯编排说明")
            elif d.verdict == "supported" and (
                (not d.evidence_ids and not fulltext_only)
                or not set(d.evidence_ids).issubset(allowed)
            ):
                problems.append(f"第 {positions[d.unit_id]} 行：终稿证据映射超出该单元引用范围")
            elif d.verdict == "supported" and not fulltext_only:
                issue = self.reviewer.alignment_issue(unit, d)
                if issue:
                    problems.append(f"第 {positions[d.unit_id]} 行：{issue}")
            if d.verdict in {"supported", "non_factual"}:
                if issue := self.reviewer.record_issue(unit, d):
                    problems.append(f"第 {positions[d.unit_id]} 行：{issue}")
        if self.peer is not None:
            bound, peer_issues = self.peer.bound_check(
                markdown, units, locations, decisions, record.get("peer_review"),
            )
            if not bound:
                return False, peer_issues
            problems.extend(peer_issues)
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
    sources: list[Source] | None = None,
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
        from .analysis_review import sample_size_scopes

        version = digest(ledger)
        payload = {
            "computed_facts": ledger_facts(ledger),
            "sample_size_scopes": sample_size_scopes(ledger),
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
            llm,
            evidence,
            capacity,
            source_version=version,
            query=query,
            implicit=True,
            statistics=ledger,
            contract=contract_from_scratch(scratch),
            material_scratch=scratch,
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
        scratch=scratch,
        sources=sources,
    )


def stored_review(scratch: dict) -> Any:
    return scratch.get(PROSE_REVIEW_KEY) or scratch.get("workbench", {}).get("extras", {}).get(
        PROSE_REVIEW_KEY
    )
