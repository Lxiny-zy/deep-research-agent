"""把已落库的运行数据装配成 ``ReportDocument``。

## 为什么 join 要放在这里

改造前这套 join 只存在于前端 TypeScript（``frontend/src/lib/evidence.ts`` +
``EvidencePanel.tsx``）：引用号 → URL → 该来源下的 findings → 徽章。于是

* 下载 .md 只拿到裸 markdown，证据装置全丢；
* 想加任何一个后端渲染出口（PDF、邮件、API），就得把同一套 join 再实现一遍。

放到 Python 侧之后，它成为**唯一**的规范化装配路径：交互视图、Markdown 投影、
打印布局、以及将来的服务端 PDF 都消费同一个 ``ReportDocument``。

## 与既有契约的兼容

``Report``（``{query, markdown, citations}``）**一个字段都不改**：前端按 citations
下标做 [n] 跳转，质量指标按 URL 与检索快照比对覆盖率，两条链路继续按原样工作。
本模块只**读**它，产出一个并列的增量产物。历史 run 也能装配——缺 ``source_reference``
的旧记录会回退到裸 URL，缺审计事件的旧 run 把拦截数标成"不可用"而不是编一个 0。
"""

from __future__ import annotations

import re

from ..models import Finding, Quantity, Report, ResearchResult
from ..observability import Event
from .document import (
    Block,
    ChartBlock,
    EvidenceRecord,
    Overview,
    PaperSection,
    ProseBlock,
    ReferenceEntry,
    ReportDocument,
    SectionKind,
    TableBlock,
)
from .hsi_tables import hsi_tables_from_results
from .pivot import pivot_tables

# Synthesizer 会在正文末尾追加 "## 参考来源" 段落（``synthesizer._finalize``）。
# 结构化文档里参考来源是独立字段，正文若把那一段带进来就会渲染两遍。
_REFERENCES_HEADING = re.compile(r"\n#{2,3}\s*参考来源\s*\n.*\Z", re.DOTALL)
_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FENCE = re.compile(r"^(`{3,}|~{3,})")

_SECTION_ALIASES: dict[str, SectionKind] = {
    "摘要": "abstract",
    "引言": "introduction",
    "绪论": "introduction",
    "介绍": "introduction",
    "相关工作": "related_work",
    "文献综述": "related_work",
    "方法": "methods",
    "研究方法": "methods",
    "实验": "experiments",
    "实验设置": "experiments",
    "结果": "results",
    "实验结果": "results",
    "讨论": "results",
    "局限": "limitations",
    "局限性": "limitations",
    "结论": "conclusion",
    "数据与代码可用性": "data_availability",
    "数据可用性": "data_availability",
    "伦理声明": "ethics",
    "利益冲突": "ethics",
}


def assemble_document(
    report: Report | None,
    results: list[ResearchResult],
    *,
    events: list[Event] | None = None,
    query: str = "",
    extra_blocks: list[Block] | None = None,
    require_corroboration: bool = False,
    include_tables: bool = True,
    include_hsi_tables: bool = False,
) -> ReportDocument:
    """装配一份结构化报告文档。

    ``include_tables`` 开启时会从通过门禁的 findings 自动透视出定量对照表
    （见 ``pivot``）。这对旧数据是无操作：历史 findings 没有 ``entity`` /
    ``Quantity``，透视器返回空列表，产物逐字节不变。

    ``extra_blocks`` 供调用方注入自己构造的块（例如挑好形式的图表），
    它排在自动透视的表之后。
    """
    findings = [finding for result in results for finding in result.findings]
    citations = list(report.citations) if report is not None else []
    references = _references(citations, findings)
    index_by_url = {entry.url: entry.index for entry in references}

    blocks: list[Block] = []
    body = _body(report)
    if body:
        blocks.append(ProseBlock(markdown=body))
    if include_tables:
        blocks.extend(
            pivot_tables(results, index_by_url, require_corroboration=require_corroboration)
        )
    if include_hsi_tables:
        blocks.extend(
            hsi_tables_from_results(
                results,
                index_by_url,
                require_corroboration=require_corroboration,
            )
        )
    blocks.extend(extra_blocks or [])

    sections = _paper_sections(body, blocks)
    abstract = _take_abstract(sections)

    return ReportDocument(
        query=query or (report.query if report is not None else ""),
        title=_leading_title(body),
        abstract=abstract,
        blocks=blocks,
        sections=sections,
        references=references,
        evidence=_evidence(findings, index_by_url),
        overview=_overview(findings, events),
    )


def _leading_title(body: str) -> str:
    """Synthesizer 正文开头的一级标题即报告标题。

    分章投影会丢弃没有正文的标题行，这个 H1 若不提升为 ``title``，
    Markdown / LaTeX 导出就只剩 query 作标题，报告自己的标题凭空消失。
    """

    for raw in body.replace("\r\n", "\n").splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _ATX_HEADING.match(line)
        if match and len(match.group(1)) == 1:
            return match.group(2).strip().strip("#").strip()
        return ""
    return ""


def _heading_lines(body: str) -> dict[int, int]:
    """行号 → 标题级别；代码围栏内的 ``# 注释`` 不是标题，不能切断章节。"""

    headings: dict[int, int] = {}
    fence = ""
    for number, raw in enumerate(body.replace("\r\n", "\n").splitlines()):
        line = raw.strip()
        marker = _FENCE.match(line)
        if marker:
            if not fence:
                fence = marker.group(1)[0] * 3
            elif line.startswith(fence):
                fence = ""
            continue
        if fence:
            continue
        match = _ATX_HEADING.match(line)
        if match:
            headings[number] = len(match.group(1))
    return headings


def _take_abstract(sections: list[PaperSection]) -> str:
    """Promote one explicit abstract heading to publication front matter."""

    for index, section in enumerate(sections):
        if section.kind != "abstract":
            continue
        prose = next(
            (block.markdown for block in section.blocks if isinstance(block, ProseBlock)),
            "",
        )
        sections.pop(index)
        return prose.strip()
    return ""


def _paper_sections(body: str, blocks: list[Block]) -> list[PaperSection]:
    """Split report prose into controlled paper chapters.

    Headings remain ordinary Markdown in the compatibility ``blocks`` view.
    The section projection strips only the heading line and keeps all other
    text byte-for-byte, so every renderer gets the same source content.
    """

    prose_sections: list[PaperSection] = []
    current_title = "引言"
    current_kind: SectionKind = "introduction"
    current_level = 1
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_lines
        markdown = "\n".join(current_lines).strip()
        if markdown:
            prose_sections.append(
                PaperSection(
                    id=f"section-{len(prose_sections) + 1}",
                    title=current_title,
                    kind=current_kind,
                    level=current_level,
                    blocks=[ProseBlock(markdown=markdown)],
                )
            )
        current_lines = []

    headings = _heading_lines(body)
    # 开头的 H1 已提升为报告标题，不参与分章；其余标题按最浅一级归一到 1 级，
    # 否则 Synthesizer 惯用的 "## 章" 会变成 LaTeX 的 \subsection（编号 0.1）。
    title_line = min(headings) if headings and _leading_title(body) else None
    if title_line is not None:
        headings.pop(title_line)
    shift = min(headings.values(), default=1) - 1

    for number, raw in enumerate(body.replace("\r\n", "\n").splitlines()):
        if number == title_line:
            continue
        match = _ATX_HEADING.match(raw.strip()) if number in headings else None
        if match:
            flush()
            title = match.group(2).strip().strip("#").strip()
            current_title = title or "引言"
            current_kind = _SECTION_ALIASES.get(title, "other")
            current_level = max(1, min(headings[number] - shift, 3))
            continue
        current_lines.append(raw)
    flush()

    table_blocks = [block for block in blocks if isinstance(block, TableBlock)]
    chart_blocks = [block for block in blocks if isinstance(block, ChartBlock)]
    result_section = next(
        (section for section in prose_sections if section.kind == "results"),
        None,
    )
    if table_blocks or chart_blocks:
        if result_section is None:
            result_section = PaperSection(
                id=f"section-{len(prose_sections) + 1}",
                title="结果",
                kind="results",
                level=1,
            )
            prose_sections.append(result_section)
        result_section.blocks.extend(table_blocks)
        result_section.blocks.extend(chart_blocks)
    return prose_sections


def _body(report: Report | None) -> str:
    """正文：去掉 Synthesizer 自动追加的参考来源段落。

    只在**结尾**匹配（``\\Z``），因此正文中间出现"参考来源"字样的普通段落不会被误删。
    """
    if report is None:
        return ""
    return _REFERENCES_HEADING.sub("", report.markdown).strip()


def _references(citations: list[str], findings: list[Finding]) -> list[ReferenceEntry]:
    """参考来源：顺序与 ``Report.citations`` 严格一致。

    引用文本取自 Finding 上由 ``EvidenceVerifier`` 在验证时刻渲染并落库的
    ``source_reference``；取不到就留空，由渲染器回退成裸 URL。这里不重新推导引用，
    所以历史 run 回放与 worker 跨进程执行拿到的参考来源完全一致。
    """
    reference_by_url: dict[str, str] = {}
    for finding in findings:
        text = finding.verification.source_reference
        if text and finding.source_url not in reference_by_url:
            reference_by_url[finding.source_url] = text
    return [
        ReferenceEntry(index=index, url=url, reference=reference_by_url.get(url, ""))
        for index, url in enumerate(citations, 1)
    ]


def _evidence(findings: list[Finding], index_by_url: dict[str, int]) -> list[EvidenceRecord]:
    """证据附录记录，按引用号升序。

    只收录**进了报告引用列表**的来源：没有被引用的 finding 在正文里没有对应角标，
    放进附录会给读者一堆无处可去的记录。它们仍然计入概览统计——概览说的是"这次
    研究验证了多少条"，与"报告引用了哪些"是两个不同的量。
    """
    records: list[EvidenceRecord] = []
    for finding in findings:
        index = index_by_url.get(finding.source_url)
        if index is None:
            continue
        verification = finding.verification
        records.append(
            EvidenceRecord(
                citation=index,
                claim_id=verification.claim_id,
                statement=finding.statement,
                quote=finding.evidence_quote,
                context=verification.evidence_context,
                source_url=finding.source_url,
                reference=verification.source_reference,
                source_section=(
                    verification.source_identity.section
                    if verification.source_identity is not None
                    else ""
                ),
                content_hash=verification.source_content_hash,
                verbatim_verified=verification.status == "verified",
                verification_reason=verification.reason,
                semantic_status=verification.semantic_status,
                semantic_confidence=verification.semantic_confidence,
                semantic_reason=verification.semantic_reason,
                consistency_status=verification.consistency_status,
                contradicts_claim_ids=list(verification.contradicts_claim_ids),
                contradiction_reason=verification.contradiction_reason,
                corroboration_status=verification.corroboration_status,
                independent_source_count=verification.independent_source_count,
                corroboration_reason=verification.corroboration_reason,
                quantity_label=_quantity_label(finding.quantity),
                conditions_label=(finding.conditions.describe() if finding.conditions else ""),
                quantity_status=verification.quantity_status,
                quantity_reason=verification.quantity_reason,
            )
        )
    records.sort(key=lambda record: record.citation)
    return records


def _quantity_label(quantity: Quantity | None) -> str:
    """把结构化数值渲染成 ``PSNR > 38.36 dB ± 0.05`` 这样的一行。

    比较符必须保留：把"超过 38.36 dB"写成"= 38.36 dB"会让报告给出比证据更强的结论。
    """
    if quantity is None or quantity.value is None:
        return ""
    number = quantity.rendered or f"{quantity.value:g}"
    comparator = f"{quantity.comparator} " if quantity.comparator not in ("", "=") else ""
    parts = [quantity.metric.strip(), "=" if not comparator else ""]
    head = " ".join(part for part in parts if part).strip()
    tail = f"{comparator}{number}"
    if quantity.unit:
        tail += f" {quantity.unit}"
    if quantity.uncertainty is not None:
        tail += f" ± {quantity.uncertainty:g}"
    return f"{head} {tail}".strip()


def _overview(findings: list[Finding], events: list[Event] | None) -> Overview:
    """概览统计。口径与前端 ``summarizeEvidence`` 一致，覆盖全部 findings。"""
    verbatim = sum(1 for f in findings if f.verification.status == "verified")
    supported = sum(
        1
        for f in findings
        if f.verification.status == "verified" and f.verification.semantic_status == "supported"
    )
    corroborated = sum(1 for f in findings if f.verification.corroboration_status == "corroborated")
    conflicted = sum(1 for f in findings if f.verification.consistency_status == "conflicted")
    return Overview(
        records=len(findings),
        verbatim_matched=verbatim,
        semantically_supported=supported,
        corroborated=corroborated,
        conflicted=conflicted,
        blocked_sources=_blocked_sources(events),
    )


def _blocked_sources(events: list[Event] | None) -> int | None:
    """来源拦截数来自事件流的 ``source_policy`` 审计事件。

    一条这类事件都没有时返回 ``None``＝"不可用"，而不是 0。历史 run 与关闭了审计的
    部署本就拿不到这个数，写 0 是一个我们并不掌握的具体断言。
    """
    if not events:
        return None
    found = False
    total = 0
    for event in events:
        data = event.data or {}
        if data.get("category") != "source_policy":
            continue
        found = True
        blocked = data.get("blocked")
        if isinstance(blocked, int) and not isinstance(blocked, bool):
            total += blocked
    return total if found else None
