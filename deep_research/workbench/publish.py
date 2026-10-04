"""交付发布：为一次已完成的运行生成全部承诺格式、跑验收门、写交付登记。

流程固定为「生成候选 → 验收 → 发布」：

1. 报告格式由定稿 Markdown 生成，幻灯片结构须与定稿一致，统计沿用任务计算记录；
2. 对候选跑验收门，得到每道门的结论与总体结论；
3. 把成品写入 ``output/<slug>/deliverables/``，把门结论写入
   ``output/<slug>/deliverables/qa.json``，并更新交付登记 ``deliverables.json``。

验收失败的格式**仍然发布**但在登记里标为 ``status=fail`` 并附问题清单——
用户需要知道「PDF 自检失败」，而不是看到一个少了 PDF 的交付列表却不知道为什么。
fail 级引用越界和导图节点证据失败不生成正式格式：只保留标记 fail 的 Markdown
用于排查，不把错误引用或未获支持的图当成正式交付。

HTTP 入口通过 delivery_store 持久化不可变交付版本，后续下载核对登记并读取原文件，
跨进程重启不重新渲染。publish() 保留为工作流显式发布的兼容入口。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, cast

from ..persistence.repository import RunDetail
from .contract import contract_from_scratch
from .gates import (
    GateResult,
    Status,
    citation_gate,
    length_gate,
    markdown_gate,
    review_gate,
    revision_gate,
    scholarly_gate,
    structure_gate,
)
from .templates import (
    DEFAULT_TEMPLATE,
    DeliverableFormat,
    SectionSpec,
    TaskTemplate,
    get_template,
    template_for_workflow,
)
from .writers import WORKBENCH_SCRATCH_KEY

logger = logging.getLogger(__name__)

DELIVERY_STAGE = "deliverables"
_MIME = {
    "md": "text/markdown",
    "html": "text/html",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "png": "image/png",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "json": "application/json",
}
ROLE_BY_FORMAT = {
    "pdf": "report",
    "docx": "report",
    "html": "reading",
    "md": "source",
    "pptx": "slides",
    "png": "figure",
    "xlsx": "data",
}


@dataclass
class DeliveryFile:
    name: str
    format: str
    title: str
    role: str
    data: bytes
    status: str = "pass"
    issues: list[str] = field(default_factory=list)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    def record(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "format": self.format,
            "title": self.title,
            "role": self.role,
            "size": len(self.data),
            "sha256": self.sha256,
            "mime_type": _MIME.get(self.format, "application/octet-stream"),
            "status": self.status,
            "issues": self.issues,
        }


@dataclass
class DeliveryBundle:
    template: str
    title: str
    files: list[DeliveryFile]
    gates: list[GateResult]
    status: str
    generated_at: str
    content_version: str = ""
    input_version: str = ""
    parent_version: str = ""
    attempt: int = 1
    failures: list[dict[str, Any]] = field(default_factory=list)
    render_context: dict[str, Any] = field(default_factory=dict, repr=False)

    def registry(self) -> dict[str, Any]:
        usable = [file for file in self.files if file.status != "fail"]
        primary = next(
            (f for f in usable if f.role in {"report", "slides"} and f.format in {"pdf", "pptx"}),
            next((f for f in usable if f.format == "html"), usable[0] if usable else None),
        )
        return {
            "version": 1,
            "content_version": self.content_version,
            "input_version": self.input_version or self.content_version,
            "parent_version": self.parent_version,
            "attempt": self.attempt,
            "failures": self.failures,
            "template": self.template,
            "title": self.title,
            "status": self.status,
            "generated_at": self.generated_at,
            "primary": primary.name if primary else None,
            "items": [f.record() for f in self.files],
            "gates": [g.to_dict() for g in self.gates],
        }


def resolve_template(detail: RunDetail) -> TaskTemplate:
    scratch = _scratch(detail)
    contract = contract_from_scratch(scratch)
    if contract is not None:
        template = get_template(contract.template)
        if template is not None:
            # The task's promised sections and formats were frozen at creation.
            # Keep current aliases for equivalent headings without introducing
            # requirements added to the template while this task was running.
            sections = []
            for index, title in enumerate(contract.required_sections):
                matched = next(
                    (
                        section
                        for section in template.sections
                        if title.strip().casefold()
                        in {
                            heading.strip().casefold()
                            for heading in (section.title, *section.aliases)
                        }
                    ),
                    None,
                )
                sections.append(
                    replace(
                        matched,
                        title=title,
                        aliases=tuple(dict.fromkeys((matched.title, *matched.aliases))),
                        required=True,
                    )
                    if matched is not None
                    else SectionSpec(key=f"contract-{index}", title=title, guidance="")
                )
            return replace(
                template,
                sections=tuple(sections) if contract.required_sections else template.sections,
                deliverables=cast("tuple[DeliverableFormat, ...]", tuple(contract.deliverables))
                if contract.deliverables
                else template.deliverables,
            )
    workbench = scratch.get(WORKBENCH_SCRATCH_KEY)
    if isinstance(workbench, dict):
        template = get_template(str(workbench.get("template", "")))
        if template is not None:
            return template
    workflow = detail.orchestration.workflow_name if detail.orchestration else None
    template = template_for_workflow(workflow)
    return template or get_template(DEFAULT_TEMPLATE)  # type: ignore[return-value]


def _scratch(detail: RunDetail) -> dict[str, Any]:
    execution = detail.orchestration
    checkpoint = execution.checkpoint if execution is not None else None
    scratch = checkpoint.get("scratch") if isinstance(checkpoint, dict) else None
    return scratch if isinstance(scratch, dict) else {}


def _file_stem(title: str) -> str:
    ascii_part = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()[:40]
    digest = hashlib.sha256(title.encode("utf-8")).hexdigest()[:8]
    return f"{ascii_part or 'report'}-{digest}"


def _insert_concept(markdown: str, block: str) -> str:
    method = re.search(
        r"(?im)^##\s+(?:方法[^\n]*|研究方法[^\n]*|Methods?[^\n]*|Approach[^\n]*)\n", markdown
    )
    if method:
        return markdown[: method.end()] + block + markdown[method.end() :]
    parts = re.split(r"(?m)(^##\s[^\n]*\n(?:(?!^##\s)[^\n]*\n)*)", markdown, maxsplit=1)
    return parts[0] + parts[1] + block + parts[2] if len(parts) == 3 else markdown + block


def _insert_analysis_figures(markdown: str, figure_md: str) -> str:
    markdown = re.sub(
        r"(\n##\s*图表[^\n]*\n)",
        lambda m: m.group(1) + "\n" + figure_md + "\n\n",
        markdown,
        count=1,
    )
    return markdown if "![" in markdown else markdown + "\n\n## 图表\n\n" + figure_md + "\n"


_DELIVERY_RUNTIME_KEYS = frozenset(
    {
        "_completion",
        "_runtime_metrics",
        "_deadline_at",
        "_task_deadline_at",
        "_attempt_elapsed_origin",
        "_recovery",
        "_orchestration_run",
        "_committed_research_progress",
    }
)


def delivery_fingerprint(detail: RunDetail) -> str:
    """Every persisted input consumed by build_bundle, not just report Markdown."""
    from .support import SUPPORT_POLICY_VERSION

    payload = {
        "format_version": 50,
        "support_policy": SUPPORT_POLICY_VERSION,
        "query": detail.query,
        "created_at": detail.created_at.isoformat() if detail.created_at else None,
        "report": detail.report.model_dump(mode="json") if detail.report else None,
        "results": [result.model_dump(mode="json") for result in detail.results],
        "workflow": detail.orchestration.workflow_name if detail.orchestration else None,
        # Exact operational keys only: evidence, quality policy, frozen settings,
        # source material and unknown future content fields remain cache inputs.
        "scratch": {
            key: value
            for key, value in _scratch(detail).items()
            if key not in _DELIVERY_RUNTIME_KEYS
        },
        "sources": [source.model_dump(mode="json") for source in detail.sources],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def build_bundle(detail: RunDetail) -> DeliveryBundle:
    """纯函数：从一次运行的持久化数据生成交付包（不写盘）。"""
    # 各格式的第三方依赖（python-docx 等）在各自的 build_* 里延迟导入：
    # 缺一个可选依赖只让该格式缺席，不拖垮整个交付包
    template = resolve_template(detail)
    scratch = _scratch(detail)
    workbench = (
        scratch.get(WORKBENCH_SCRATCH_KEY)
        if isinstance(scratch.get(WORKBENCH_SCRATCH_KEY), dict)
        else {}
    )
    extras = workbench.get("extras", {}) if isinstance(workbench, dict) else {}
    report = detail.report
    markdown = report.markdown if report is not None else "（本次运行没有生成正文）"
    # 地名规范在生成任何格式之前统一改写一次：所有格式派生自同一份定稿，
    # 改写只做一次就能保证 DOCX / PDF / HTML 一致；地名门随后逐格式把关。
    from .territory import normalize as normalize_territory

    markdown = normalize_territory(markdown)
    extras = _normalize_extras(extras, normalize_territory)
    citations = list(report.citations) if report is not None else []
    contract = contract_from_scratch(scratch)
    title = (contract.title if contract else "") or f"{template.title}：{detail.query[:40]}"
    if template.key in {"paperRead", "autoResearch", "litReview"}:
        from .titles import paper_report_title

        title = paper_report_title(markdown, title, detail.query, label=template.title)
    created_at = detail.created_at
    created = created_at.isoformat() if created_at is not None else ""
    from ..bibliography import build_bibliography, present_markdown, work_keys
    from .reader import paper_sources
    from .scholarly import source_counts

    catalog = build_bibliography(
        markdown,
        citations,
        [f for result in detail.results for f in result.findings],
        [*detail.sources, *paper_sources(detail)],
    )
    if report is not None:
        from ..report.service import requires_corroboration
        from .citation_binding import bind_review
        from .prose_review import reviewer_for_report, stored_review

        citation_reviewer = reviewer_for_report(
            None,
            detail.query,
            detail.results,
            citations,
            scratch,
            0,
            corroboration=requires_corroboration(detail),
        )
        bind_review(catalog, citation_reviewer, report.markdown, stored_review(scratch))
    display_markdown = present_markdown(markdown, catalog) if citations else markdown
    document_keys = work_keys(catalog)
    if template.key == "peerReview":
        from .titles import paper_report_title

        title = paper_report_title(
            markdown,
            title,
            detail.query,
            label=template.title,
            reference_title=catalog.documents[0].title if len(catalog.documents) == 1 else "",
        )
    distinct_sources = source_counts(citations, document_keys=document_keys)[0]
    meta = f"{template.title} · {distinct_sources} 个已核验来源"
    if len(citations) > distinct_sources:
        meta += f" · {len(citations)} 处引用定位"
    meta += f" · {created[:10]}" if created else ""
    if template.key == "dataAnalysis" and isinstance(scratch.get("analysis"), dict):
        from .titles import analysis_meta, analysis_title

        title = analysis_title(scratch["analysis"])
        meta = analysis_meta(scratch["analysis"]) + (f" · {created[:10]}" if created else "")
    if template.key == "mindmap" and isinstance(extras.get("mindmap"), dict):
        from .titles import mindmap_title

        title = mindmap_title(extras["mindmap"])
    stem = _file_stem(title)
    images: dict[str, bytes] = {}
    files: list[DeliveryFile] = []
    statistics: dict[str, Any] | None = None
    input_gates: list[GateResult] = []
    if template.key == "slides" and extras.get("deck"):
        from .gates import _body_without_references
        from .writers import SlideDeck, deck_to_markdown

        projected = deck_to_markdown(SlideDeck.model_validate(extras["deck"])).strip()
        if projected != _body_without_references(markdown).strip():
            extras = {**extras}
            extras.pop("deck", None)
            extras["structured_output_issue"] = (
                "幻灯片结构与审核正文不一致，已改用审核后的正文重新生成"
            )
    if extras.get("structured_output_issue"):
        input_gates.append(
            GateResult("structured_content", "warn", [str(extras["structured_output_issue"])])
        )

    # 概念图：写作者整理的结构描述 → 确定性示意图，插在正文第一个二级标题之后
    raw_figure = extras.get("concept_figure") if isinstance(extras, dict) else None
    if isinstance(raw_figure, dict):
        from ..report.service import requires_corroboration
        from .figure_review import FIGURE_REVIEW_KEY, check_figure
        from .figures import ConceptFigure, present_figure, render_concept_png
        from .support import evidence_records

        concept: ConceptFigure | None = None
        try:
            concept = ConceptFigure.model_validate(raw_figure)
            figure_evidence = evidence_records(
                detail.results,
                {url: i for i, url in enumerate(citations, 1)},
                corroboration=requires_corroboration(detail),
            )
            issues = check_figure(concept, figure_evidence, extras.get(FIGURE_REVIEW_KEY))
            if issues:
                input_gates.append(
                    GateResult(
                        "figure_evidence", "warn", ["图示未纳入交付：" + issue for issue in issues]
                    )
                )
                concept = None
            else:
                shown_concept = present_figure(concept, catalog)
                png = render_concept_png(shown_concept)
                input_gates.append(GateResult("figure_evidence", "pass"))
        except Exception as exc:
            input_gates.append(
                GateResult("figure_evidence", "warn", [f"图示生成未完成：{type(exc).__name__}"])
            )
            concept = None
        if concept is not None:
            name = "fig_concept.png"
            images[name] = png
            files.append(
                DeliveryFile(f"figures/{name}", "png", f"概念图：{concept.title}", "figure", png)
            )
            caption = concept.caption or concept.title
            block = f"\n\n![{concept.title}（{caption}）]({name})\n\n"
            markdown = _insert_concept(markdown, block)
            shown_caption = shown_concept.caption or shown_concept.title
            shown_block = f"\n\n![{shown_concept.title}（{shown_caption}）]({name})\n\n"
            display_markdown = _insert_concept(display_markdown, shown_block)

    computed_fallback = False
    # 数据分析：确定性地重算图表（与运行时同一函数、同一数据），把图挂进正文
    if template.key == "dataAnalysis":
        from .analysis import (
            ANALYSIS_SCRATCH_KEY,
            DatasetError,
            allows_synthetic,
            analyse,
            fallback_report,
        )

        try:
            result = analyse(
                contract.dataset_csv if contract else "",
                contract.focus if contract else "",
                allow_synthetic=allows_synthetic(contract),
                source=contract.dataset_source if contract else None,
                frozen=scratch.get(ANALYSIS_SCRATCH_KEY),
            )
        except DatasetError as exc:
            result = None
            input_gates.append(GateResult("analysis", "fail", [str(exc)]))
        if result is not None:
            computed_fallback = (
                report is not None
                and normalize_territory(report.markdown).strip()
                == normalize_territory(fallback_report(result)).strip()
            )
            input_gates.append(
                GateResult(
                    "analysis",
                    "warn" if result.issues else "pass",
                    result.issues,
                    {
                        "tests": len(result.tests),
                        "unavailable": sum(t.get("significant") is None for t in result.tests),
                    },
                )
            )
            for figure in result.figures:
                images[figure.name] = figure.png
                files.append(
                    DeliveryFile(
                        f"figures/{figure.name}", "png", figure.title, "figure", figure.png
                    )
                )
            if result.figures and "![" not in markdown:
                figure_md = "\n\n".join(
                    f"![{f.title}（{f.caption}）]({f.name})" for f in result.figures
                )
                markdown = _insert_analysis_figures(markdown, figure_md)
                display_markdown = _insert_analysis_figures(display_markdown, figure_md)
            statistics = {
                key: getattr(result, key)
                for key in (
                    "describe",
                    "tests",
                    "correlations",
                    "issues",
                    "scope",
                    "composition",
                    "columns",
                )
            }

    from .quality import coerce_policy

    policy = coerce_policy(contract.quality if contract is not None else None)
    from .contract import provided_material, provided_review

    min_citations = (
        contract.min_citations
        if contract is not None and (contract.min_citations or provided_review(contract))
        else policy.min_citations_for(template.key, template.min_citations)
    )
    gates: list[GateResult] = [
        *input_gates,
        markdown_gate(markdown),
        length_gate(markdown, template),
    ]
    from .quote_recovery import quote_length_issues

    quote_issues = quote_length_issues(detail.results, policy.max_evidence_quote_chars)
    gates.append(
        GateResult("evidence_quote_length", "fail" if quote_issues else "pass", quote_issues)
    )
    from .extraction import processing_failures

    incomplete = processing_failures(detail.results)
    if incomplete:
        gates.append(GateResult("source_processing", "fail", incomplete))
    gates.append(structure_gate(markdown, template, extras))
    validation = scratch.get("_report_validation")
    prose = extras.get("prose_review")
    if not computed_fallback and (
        (isinstance(validation, dict) and validation.get("fallback") is True)
        or (isinstance(prose, dict) and prose.get("body_replaced") is True)
    ):
        gates.append(
            GateResult(
                "task_content",
                "fail",
                [f"{template.title}正文未通过检查，目前仅保留证据摘录，尚未完成所要求的交付"],
            )
        )
    if provided_material(contract):
        from .corpus import corpus_issues
        from .revision import _used_indices

        selected = _used_indices(markdown)
        issues = corpus_issues(
            scratch, detail.results, [u for i, u in enumerate(citations, 1) if i in selected]
        )
        gates.append(GateResult("provided_corpus", "fail" if issues else "pass", issues))
        if provided_review(contract):
            from .review_coverage import coverage_issues

            coverage = coverage_issues(scratch, detail.results)
            gates.append(GateResult("review_coverage", "fail" if coverage else "pass", coverage))
    if template.key == "mindmap" and extras.get("mindmap"):
        from .mindmap_contract import checked_review

        raw = extras["mindmap"]
        review = extras.get("node_review")
        _bound, issues = checked_review(raw, citations, detail.results, review, markdown)
        status: Status = "fail" if issues else "pass"
        if review is None and issues and issues[0].startswith("历史导图"):
            status = "warn"
        gates.append(GateResult("node_evidence", status, issues))
    if template.key != "mindmap" and report is not None:
        from ..report.service import requires_corroboration
        from .prose_review import reviewer_for_report, stored_review

        prose = reviewer_for_report(
            None,
            detail.query,
            detail.results,
            citations,
            scratch,
            0,
            corroboration=requires_corroboration(detail),
        )
        record = stored_review(scratch)
        if prose is not None:
            if record is None:
                gates.append(
                    GateResult("prose_evidence", "warn", ["该历史正文没有终稿支持关系核验记录"])
                )
            else:
                bound, issues = prose.check(report.markdown, record)
                gates.append(
                    GateResult(
                        "prose_evidence",
                        "fail" if issues or not bound else "pass",
                        issues,
                        {
                            "units": len(record.get("units", [])),
                            "method": "not_reviewed"
                            if record.get("model_review_skipped")
                            else "model_assessment",
                        },
                    )
                )
    if min_citations or citations:
        gates.append(
            citation_gate(markdown, citations, template, min_citations, document_keys=document_keys)
        )
    if template.key not in {"slides", "mindmap"}:
        gates.append(
            scholarly_gate(
                markdown,
                template=template,
                query=contract.original_request if contract else detail.query,
                citations=citations,
                min_citations=min_citations,
                policy=policy,
                source_texts=_source_texts(detail, citations),
                references=_references(detail),
                document_keys=document_keys,
            )
        )
    revision = revision_gate(extras)
    if revision is not None:
        gates.append(revision)
    if template.key == "peerReview":
        gates.append(review_gate(extras))
    citation_failed = any(
        g.name
        in {
            "citation",
            "node_evidence",
            "prose_evidence",
            "provided_corpus",
            "task_content",
            "review_coverage",
            "source_processing",
            "evidence_quote_length",
        }
        and g.status == "fail"
        for g in gates
    )

    from ..report.service import requires_corroboration
    from .delivery_render import render_bundle
    from .support import evidence_records

    bibliography = None
    canonical_markdown = markdown
    if citations:
        bibliography = catalog
        markdown = display_markdown

    context = {
        "markdown": markdown,
        "canonical_markdown": canonical_markdown,
        "bibliography": bibliography.model_dump(mode="json") if bibliography else None,
        "title": title,
        "meta": meta,
        "stem": stem,
        "template": template.key,
        "kicker": template.title,
        "wants": list(template.deliverables),
        "extras": extras,
        "citations": citations,
        "references": _references(detail),
        "evidence": evidence_records(
            detail.results,
            {url: i for i, url in enumerate(citations, 1)},
            corroboration=requires_corroboration(detail),
        ),
        "statistics": statistics,
        "base_gates": [gate.to_dict() for gate in gates],
        "blocked": citation_failed,
        "fail_on_quality": policy.fail_on_quality,
        "generated_at": (created_at or datetime.now(UTC)).astimezone(UTC).isoformat(),
    }
    return render_bundle(context, files)


def _source_texts(detail: RunDetail, citations: list[str]) -> list[str]:
    """被引来源的可比对文本（学术引用 + 标题 + 证据），用于时效覆盖检查。"""
    cited = set(citations)
    texts: dict[str, list[str]] = {}
    for result in detail.results:
        for finding in result.findings:
            if finding.source_url not in cited:
                continue
            verification = finding.verification
            texts.setdefault(finding.source_url, []).extend(
                [verification.source_reference, verification.source_title, finding.source_url]
            )
    return [" ".join(filter(None, parts)) for parts in texts.values()]


def _references(detail: RunDetail) -> dict[str, str]:
    references: dict[str, str] = {}
    for result in detail.results:
        for finding in result.findings:
            reference = finding.verification.source_reference
            if reference:
                references.setdefault(finding.source_url, reference)
    return references


def _normalize_extras(value: Any, fix: Any) -> Any:
    """幻灯片页面与导图节点不经 Markdown，同样逐个字符串规范化。"""
    if isinstance(value, str):
        return fix(value)
    if isinstance(value, list):
        return [_normalize_extras(item, fix) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_extras(item, fix) for key, item in value.items()}
    return value


def _deck_from_markdown(markdown: str, title: str) -> dict[str, Any]:
    """没有结构化幻灯片时的兜底：按二级标题切页，列表项作要点。"""
    from .delivery.markdown import parse_blocks

    slides: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for block in parse_blocks(markdown):
        if block.kind == "heading" and block.level >= 2:
            text = plain(block.inlines)
            if re.match(r"^(参考来源|参考文献|References)$", text):
                break
            current = {"title": text, "bullets": [], "notes": "", "citations": []}
            slides.append(current)
        elif current is not None and block.kind == "list":
            current["bullets"] += [plain(item.inlines) for item in block.items]
        elif current is not None and block.kind == "paragraph":
            text = plain(block.inlines)
            current["bullets"].append(text)
    return {"title": title, "subtitle": "", "slides": slides}


def plain(inlines):  # type: ignore[no-untyped-def]
    from .delivery.markdown import plain as _plain

    return _plain(inlines)


def _stats_xlsx(result: Any) -> bytes:
    import io

    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "描述统计"
    headers = ["variable", "n", "mean", "std", "median", "min", "max"]
    sheet.append(headers)
    for row in result.describe:
        sheet.append([row.get(key) for key in headers])
    tests = workbook.create_sheet("显著性检验")
    test_headers = [
        "variable",
        "group",
        "method",
        "statistic",
        "p_value",
        "significant",
        "robust_method",
        "robust_p",
        "paired",
        "left",
        "right",
        "n_pairs",
        "excluded_pairs",
        "mean_difference",
        "difference_std",
        "df",
        "ci_low",
        "ci_high",
        "reason",
        "n_total",
        "df_between",
        "df_within",
        "eta_squared",
    ]
    tests.append(test_headers)
    for row in result.tests:
        tests.append([row.get(key) for key in test_headers])
    if any(row.get("group_summaries") for row in result.tests):
        groups = workbook.create_sheet("分组统计")
        groups.append(["variable", "group", "label", "n", "mean", "std", "median"])
        for test in result.tests:
            for summary in test.get("group_summaries", []):
                groups.append(
                    [
                        test["variable"],
                        test["group"],
                        *[summary.get(key) for key in ("label", "n", "mean", "std", "median")],
                    ]
                )
    corr = workbook.create_sheet("相关性")
    corr.append(["a", "b", "r", "p_value", "n"])
    for row in result.correlations:
        corr.append([row.get(key) for key in ("a", "b", "r", "p_value", "n")])
    if getattr(result, "scope", None) is not None:
        scope = workbook.create_sheet("分析范围")
        scope.append(["列名", "用途"])
        for role, label in (
            ("measures", "测量变量"),
            ("groups", "比较分组"),
            ("background", "样本背景（不自动检验）"),
        ):
            for column in result.scope[role]:
                scope.append([column, label])
        selected = set(
            result.scope["measures"] + result.scope["groups"] + result.scope["background"]
        )
        for column in result.columns:
            if column not in selected:
                scope.append([column, "保留在原始数据，本次未分析"])
        composition = workbook.create_sheet("样本构成")
        composition.append(["按每列本身计数，不代表每项测量的有效分组样本量"])
        composition.append(["列名", "有效数", "缺失数", "不同取值数", "取值", "构成数"])
        for item in result.composition:
            totals = [item[k] for k in ("column", "n", "missing", "distinct")]
            for level in item["levels"] or [{"label": None, "n": None}]:
                composition.append([*totals, level["label"], level["n"]])
    if result.issues:
        notices = workbook.create_sheet("未完成分析")
        notices.append(["说明"])
        for issue in result.issues:
            notices.append([issue])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def publish(store: Any, slug: str, bundle: DeliveryBundle) -> dict[str, Any]:
    """把交付包写进 ``output/<slug>/deliverables/`` 并返回登记。"""
    for file in bundle.files:
        store.write(
            slug,
            DELIVERY_STAGE,
            file.name,
            file.data,
            area="output",
            mime_type=_MIME.get(file.format, "application/octet-stream"),
            metadata={"role": file.role, "title": file.title, "status": file.status},
        )
    registry = bundle.registry()
    store.write(
        slug,
        DELIVERY_STAGE,
        "deliverables.json",
        json.dumps(registry, ensure_ascii=False, indent=2),
        area="output",
        mime_type="application/json",
    )
    return registry


__all__ = [
    "DELIVERY_STAGE",
    "DeliveryBundle",
    "DeliveryFile",
    "build_bundle",
    "publish",
    "resolve_template",
]
