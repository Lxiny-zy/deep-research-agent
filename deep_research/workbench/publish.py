"""交付发布：为一次已完成的运行生成全部承诺格式、跑验收门、写交付登记。

流程固定为「生成候选 → 验收 → 发布」：

1. 以定稿 Markdown 为唯一真源生成模板承诺的每种格式（候选全部在内存中完成）；
2. 对候选跑验收门，得到每道门的结论与总体结论；
3. 把成品写入 ``output/<slug>/deliverables/``，把门结论写入
   ``output/<slug>/deliverables/qa.json``，并更新交付登记 ``deliverables.json``。

验收失败的格式**仍然发布**但在登记里标为 ``status=fail`` 并附问题清单——
用户需要知道「PDF 自检失败」，而不是看到一个少了 PDF 的交付列表却不知道为什么。
唯一例外是 fail 级的引用越界：它意味着正文引用了不存在的来源，那份正文不应以
任何格式对外，此时只发布 Markdown 并标 fail，供排查。

生成是确定性的：同一次运行重复发布得到字节级相同的产物（PDF 除外的元数据时间戳
已固定），所以发布可以安全地在读取时按需触发，也可以在运行结束时预先生成。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..persistence.repository import RunDetail
from .contract import contract_from_scratch
from .gates import (
    HARD_GATES,
    GateResult,
    citation_gate,
    consistency_gate,
    length_gate,
    markdown_gate,
    overall,
    review_gate,
    revision_gate,
    scholarly_gate,
    slides_gate,
    structure_gate,
    territory_gate,
)
from .templates import DEFAULT_TEMPLATE, TaskTemplate, get_template, template_for_workflow
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

    def registry(self) -> dict[str, Any]:
        primary = next(
            (
                f
                for f in self.files
                if f.role in {"report", "slides"} and f.format in {"pdf", "pptx"}
            ),
            next(
                (f for f in self.files if f.format == "html"), self.files[0] if self.files else None
            ),
        )
        return {
            "version": 1,
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
            return template
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
    created_at = detail.created_at
    created = created_at.isoformat() if created_at is not None else ""
    meta = f"{template.title} · 引用 {len(citations)} 个已核验来源" + (
        f" · {created[:10]}" if created else ""
    )
    stem = _file_stem(title)
    images: dict[str, bytes] = {}
    files: list[DeliveryFile] = []

    # 概念图：写作者整理的结构描述 → 确定性示意图，插在正文第一个二级标题之后
    raw_figure = extras.get("concept_figure") if isinstance(extras, dict) else None
    if isinstance(raw_figure, dict):
        from .figures import ConceptFigure, render_concept_png

        try:
            concept = ConceptFigure.model_validate(raw_figure)
            png = render_concept_png(concept)
        except Exception:
            concept = None
        if concept is not None:
            name = "fig_concept.png"
            images[name] = png
            files.append(
                DeliveryFile(f"figures/{name}", "png", f"概念图：{concept.title}", "figure", png)
            )
            caption = concept.caption or concept.title
            block = f"\n\n![{concept.title}（{caption}）]({name})\n\n"
            parts = re.split(r"(?m)(^##\s[^\n]*\n(?:(?!^##\s)[^\n]*\n)*)", markdown, maxsplit=1)
            markdown = (
                parts[0] + parts[1] + block + parts[2] if len(parts) == 3 else markdown + block
            )

    # 数据分析：确定性地重算图表（与运行时同一函数、同一数据），把图挂进正文
    if template.key == "dataAnalysis":
        from .analysis import DatasetError, analyse

        try:
            result = analyse(
                contract.dataset_csv if contract else "", contract.focus if contract else ""
            )
        except DatasetError:
            result = None
        if result is not None:
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
                markdown = re.sub(
                    r"(\n##\s*图表[^\n]*\n)",
                    lambda m: m.group(1) + "\n" + figure_md + "\n\n",
                    markdown,
                    count=1,
                )
                if "![" not in markdown:
                    markdown += "\n\n## 图表\n\n" + figure_md + "\n"
            files.append(
                DeliveryFile(
                    f"{stem}-statistics.xlsx", "xlsx", "统计结果表", "data", _stats_xlsx(result)
                )
            )

    from .quality import coerce_policy

    policy = coerce_policy(contract.quality if contract is not None else None)
    min_citations = (
        contract.min_citations
        if contract is not None and contract.min_citations
        else policy.min_citations_for(template.key, template.min_citations)
    )
    gates: list[GateResult] = [markdown_gate(markdown), length_gate(markdown, template)]
    gates.append(structure_gate(markdown, template, extras))
    if min_citations or citations:
        gates.append(citation_gate(markdown, citations, template, min_citations))
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
            )
        )
    revision = revision_gate(extras)
    if revision is not None:
        gates.append(revision)
    if template.key == "peerReview":
        gates.append(review_gate(extras))
    citation_failed = any(g.name == "citation" and g.status == "fail" for g in gates)

    files.insert(
        0,
        DeliveryFile(
            f"{stem}.md", "md", f"{title}（Markdown 源）", "source", markdown.encode("utf-8")
        ),
    )
    wants = set(template.deliverables)
    render_failures: list[str] = []

    def render(label: str, build: Any) -> None:
        """单个格式渲染失败只让该格式缺席并记入验收门，不拖垮整个交付包。"""
        try:
            build()
        except Exception as exc:  # noqa: BLE001 - 任何渲染异常都要转成可读的验收结论
            logger.exception("delivery format %s failed", label)
            render_failures.append(f"{label} 生成失败：{type(exc).__name__}: {exc}"[:300])

    if not citation_failed:

        def build_html() -> None:
            from .delivery.html import render_html

            html = render_html(
                markdown, title=title, kicker=template.title, meta=meta, images=images
            )
            files.append(
                DeliveryFile(
                    f"{stem}.html", "html", f"{title}（阅读版）", "reading", html.encode("utf-8")
                )
            )

        def build_docx() -> None:
            from .delivery.docx import render_docx

            data = render_docx(markdown, title=title, meta=meta, images=images)
            files.append(DeliveryFile(f"{stem}.docx", "docx", f"{title}（Word）", "report", data))

        def build_pdf() -> None:
            from .delivery.pdf import PdfRenderError, render_pdf

            try:
                pdf = render_pdf(markdown, title=title, meta=meta, images=images)
            except PdfRenderError as exc:
                gates.append(GateResult("pdf", "fail", [f"PDF 生成失败：{exc}"]))
                return
            files.append(DeliveryFile(f"{stem}.pdf", "pdf", f"{title}（PDF）", "report", pdf))

        def build_pptx() -> None:
            from .delivery.pptx import render_pptx

            deck = extras.get("deck") or _deck_from_markdown(markdown, title)
            pptx = render_pptx(deck, citations=citations)
            files.append(
                DeliveryFile(f"{stem}.pptx", "pptx", f"{title}（演示文稿）", "slides", pptx)
            )
            gates.append(slides_gate(pptx, deck))

        def build_mindmap() -> None:
            from .delivery.mindmap import render_mindmap_html, render_mindmap_png

            mindmap = extras["mindmap"]
            html = render_mindmap_html(mindmap, title=title).encode("utf-8")
            png = render_mindmap_png(mindmap)
            files.append(
                DeliveryFile(
                    f"{stem}-mindmap.html", "html", f"{title}（交互导图）", "reading", html
                )
            )
            files.append(
                DeliveryFile(f"{stem}-mindmap.png", "png", f"{title}（导图图片）", "figure", png)
            )

        if "html" in wants:
            render("HTML", build_html)
        if "docx" in wants:
            render("Word", build_docx)
        if "pdf" in wants:
            render("PDF", build_pdf)
        if "pptx" in wants:
            render("PPT", build_pptx)
        if "mindmap" in wants and extras.get("mindmap"):
            render("思维导图", build_mindmap)
        gates.append(consistency_gate(markdown, {f.name: f.data for f in files}))
    if render_failures:
        gates.append(GateResult("render", "fail", render_failures))
    gates.append(territory_gate({f.name: f.data for f in files if f.format not in {"png", "xlsx"}}))
    for gate in gates:
        if gate.status == "pass":
            continue
        for file in files:
            if gate.name == "consistency" and file.format in {"docx", "html", "pdf"}:
                file.status, file.issues = gate.status, file.issues + gate.issues
            elif gate.name == "pdf" and file.format == "pdf":
                file.status, file.issues = gate.status, gate.issues
    status = overall(gates)
    if policy.fail_on_quality and status == "warn":
        # 用户要求「质量不合格即判失败」：硬性质量门的 warn 升级为 fail
        if any(g.status == "warn" and g.name in HARD_GATES for g in gates):
            status = "fail"
    return DeliveryBundle(
        template=template.key,
        title=title,
        files=files,
        gates=gates,
        status=status,
        # 固定为运行创建时刻：同一运行重复发布，登记内容逐字节一致
        generated_at=(created_at or datetime.now(UTC)).astimezone(UTC).isoformat(),
    )


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
            current["bullets"] += [plain(item.inlines) for item in block.items if item.depth == 0][
                :5
            ]
        elif current is not None and block.kind == "paragraph" and len(current["bullets"]) < 5:
            text = plain(block.inlines)
            current["bullets"].append(text[:90])
    return {"title": title, "subtitle": "", "slides": slides[:20]}


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
    ]
    tests.append(test_headers)
    for row in result.tests:
        tests.append([row.get(key) for key in test_headers])
    corr = workbook.create_sheet("相关性")
    corr.append(["a", "b", "r", "p_value"])
    for row in result.correlations:
        corr.append([row.get(key) for key in ("a", "b", "r", "p_value")])
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
