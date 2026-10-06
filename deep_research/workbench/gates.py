"""机械验收门：交付前的确定性检查，结论写进交付登记。

每道门返回 ``GateResult``：``status`` 为 pass / warn / fail，``issues`` 是可读的问题清单。
门只读取已生成的产物与运行数据，不调用模型——「我看过了」不算验收。
节点证据门校验已有模型核对记录的版本和完整性，不将其视为确定性的事实真值。

* ``citation``   —— 正文引用编号必须全部落在已核验来源内；至少满足模板的最少引用数；
* ``markdown``   —— 交付 Markdown 不含裸 HTML 标签、页内锚点链接与未闭合代码块；
* ``structure``  —— 模板承诺的章节都在（按标题或别名匹配），导图结构完整且不重复；
* ``length``     —— 正文不短于模板下限；
* ``consistency``—— 同源多格式交叉计数：DOCX 内嵌图 = HTML 内联图 = Markdown 图数，
                    PDF 可抽出正文且末段不缺失；
* ``slides``     —— PPTX 页数、备注覆盖率与版面溢出估算；
* ``review``     —— 评审必须给出 1–10 的整数评分。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from .delivery.markdown import parse_blocks, plain
from .delivery.math_markdown import citation_text
from .templates import TaskTemplate

Status = Literal["pass", "warn", "fail"]


@dataclass
class GateResult:
    name: str
    status: Status
    issues: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    blocking_issues: list[str] | None = None
    advisories: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "issues": self.issues,
            "metrics": self.metrics,
            **(
                {"blocking_issues": self.blocking_issues, "advisories": self.advisories}
                if self.blocking_issues is not None
                else {}
            ),
        }


_CITE = re.compile(r"\[(\d+(?:\s*[,，]\s*\d+)*)\]")
_RAW_TAG = re.compile(r"</?(?:div|span|script|style|iframe|img|br|font|table|center)\b[^>]*>", re.I)
_ANCHOR_LINK = re.compile(r"\]\(#[^)]*\)")


def _body_without_references(markdown: str) -> str:
    from ..bibliography import source_body

    return source_body(markdown)


def citation_gate(
    markdown: str,
    citations: list[str],
    template: TaskTemplate,
    min_citations: int | None = None,
    *,
    document_keys: dict[str, str] | None = None,
) -> GateResult:
    from .quality import effective_citation_minimum
    from .scholarly import source_counts

    body = citation_text(_body_without_references(markdown))
    used = {
        int(number) for match in _CITE.findall(body) for number in re.split(r"\s*[,，]\s*", match)
    }
    issues: list[str] = []
    out_of_range = sorted(i for i in used if i < 1 or i > len(citations))
    if out_of_range:
        issues.append(f"引用编号越界：{out_of_range}（共 {len(citations)} 个已核验来源）")
    unused = [i for i in range(1, len(citations) + 1) if i not in used]
    anchor_count = len(used - set(out_of_range))
    distinct = source_counts(
        [citations[i - 1] for i in sorted(used - set(out_of_range))], document_keys=document_keys
    )[0]
    available = source_counts(citations, document_keys=document_keys)[0]
    requested = template.min_citations if min_citations is None else min_citations
    minimum = effective_citation_minimum(requested, available)
    if minimum and distinct < minimum:
        issues.append(f"已核验引用 {distinct} 个，少于要求的 {minimum} 个")
    status: Status = "fail" if out_of_range else ("warn" if issues else "pass")
    if requested > available:
        issues.append(
            f"（检索提示）可用已核验来源只有 {available} 个，低于目标 {requested} 个；"
            f"本次引用下限按可用来源调整为 {minimum} 个，可继续补充独立来源。"
        )
    return GateResult(
        "citation",
        status,
        issues,
        {
            "used": distinct,
            "available": available,
            "unused": max(0, available - distinct),
            "anchors_used": anchor_count,
            "unused_anchors": len(unused),
            "required": minimum,
            "requested": requested,
            "retrieval_shortfall": max(0, requested - available),
        },
    )


def markdown_gate(markdown: str) -> GateResult:
    from .delivery.math_residual import raw_tex_fragments

    issues: list[str] = []
    raw = _RAW_TAG.findall(markdown)
    if raw:
        issues.append(f"含裸 HTML 标签 {len(raw)} 处（如 {raw[0][:40]}）")
    if _ANCHOR_LINK.search(markdown):
        issues.append("含页内锚点链接（跨格式交付后失效）")
    if markdown.count("```") % 2:
        issues.append("存在未闭合的代码块")
    bare_math = raw_tex_fragments(citation_text(markdown, mask_escapes=False))
    if bare_math:
        issues.append("公式缺少数学标记：" + "、".join(fragment[:60] for fragment in bare_math[:3]))
    return GateResult(
        "markdown", "fail" if raw or bare_math else ("warn" if issues else "pass"), issues,
    )


def _normalize(title: str) -> str:
    return re.sub(r"[\s\d.、:：()（）\-—]+", "", title).casefold()


def structure_gate(
    markdown: str, template: TaskTemplate, extras: dict[str, Any] | None = None
) -> GateResult:
    extras = extras or {}
    if template.key == "mindmap":
        from .mindmap_contract import Mindmap, structural_issues

        stats = extras.get("stats", {})
        raw = extras.get("mindmap")
        issues = (
            structural_issues(Mindmap.model_validate(raw), 10**9) if raw else ["没有可用的导图结构"]
        )
        return GateResult("structure", "warn" if issues else "pass", issues, dict(stats))
    if template.key == "slides":
        deck = extras.get("deck") or {}
        count = len(deck.get("slides", []))
        issues = (
            []
            if count >= len(template.sections) - 1
            else [f"内容页 {count} 页，少于大纲 {len(template.sections)} 页"]
        )
        return GateResult("structure", "warn" if issues else "pass", issues, {"slides": count})
    headings = [
        _normalize(plain(block.inlines))
        for block in parse_blocks(markdown)
        if block.kind == "heading"
    ]
    missing: list[str] = []
    for section in template.sections:
        if not section.required:
            continue
        candidates = [_normalize(section.title), *(_normalize(alias) for alias in section.aliases)]
        if not any(any(c and c in heading for c in candidates) for heading in headings):
            missing.append(section.title)
    issues = [f"缺少章节：{'、'.join(missing)}"] if missing else []
    return GateResult(
        "structure",
        "warn" if missing else "pass",
        issues,
        {"required": len([s for s in template.sections if s.required]), "missing": len(missing)},
    )


def body_length(markdown: str) -> int:
    body = _body_without_references(markdown)
    cjk = len(re.findall(r"[一-鿿]", body))
    words = len(re.findall(r"[A-Za-z][A-Za-z'-]+", body))
    return cjk + words


def length_gate(markdown: str, template: TaskTemplate) -> GateResult:
    length = body_length(markdown)
    if template.min_length and length < template.min_length:
        return GateResult(
            "length",
            "warn",
            [f"正文约 {length} 字，少于模板下限 {template.min_length} 字"],
            {"length": length, "minimum": template.min_length},
        )
    return GateResult("length", "pass", [], {"length": length})


def review_gate(extras: dict[str, Any]) -> GateResult:
    score = extras.get("score")
    if isinstance(score, int) and not isinstance(score, bool) and 1 <= score <= 10:
        review = extras.get("prose_review") or {}
        structured = review.get("peer_review") if isinstance(review, dict) else None
        items = structured.get("items") if isinstance(structured, dict) else None
        valid_items = isinstance(items, list) and bool(items) and all(
            isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
            and isinstance(item.get("text"), str) and item["text"].strip()
            and isinstance(item.get("type"), str) and item["type"] in {
                "strength", "weakness", "comment", "question", "suggestion", "recommendation",
            }
            and (item.get("severity") is None or isinstance(item.get("severity"), str)
                 and item["severity"] in {"critical", "general", "expression"})
            and (item["type"] != "weakness" or item.get("severity") is not None)
            and isinstance(item.get("evidence_ids"), list)
            and all(isinstance(e, str) and e for e in item["evidence_ids"])
            and (item.get("type") not in {"strength", "weakness"}
                 or bool(item["evidence_ids"]) and item.get("basis_valid") is True)
            for item in items
        )
        critical = structured.get("critical_count") if isinstance(structured, dict) else None
        valid_count = type(critical) is int and critical >= 0
        from .peer_review_items import CRITICAL_SCORE_CEILING

        ceiling = CRITICAL_SCORE_CEILING if valid_count and critical else 10
        if (isinstance(structured, dict) and structured.get("status") == "pass"
                and type(structured.get("score")) is int
                and structured.get("score") == score and valid_items and valid_count
                and structured.get("score_ceiling") == ceiling and score <= ceiling
                and not structured.get("issues")):
            return GateResult("review", "pass", [], {"score": score,
                "items": len(structured["items"]), "critical": structured.get("critical_count", 0)})
        return GateResult("review", "fail", ["评审条目、严重度、一致性或评分依据尚未通过核对"])
    return GateResult("review", "fail", ["评审没有给出 1–10 的整数评分（格式：评分：N/10）"])


def mindmap_composition_gate(model: Any) -> GateResult:
    from .mindmap_contract import composition

    metrics, advice = composition(model)
    return GateResult("mindmap_composition", "pass", advice, metrics)


def consistency_gate(markdown: str, files: dict[str, bytes]) -> GateResult:
    """同源多格式交叉计数。只比对实际生成了的格式。"""
    from .delivery.math_residual import residual_math_issues

    issues: list[str] = []
    metrics: dict[str, Any] = {}
    failed: set[str] = set()
    md_images = sum(1 for block in parse_blocks(markdown) if block.kind == "image")
    metrics["markdown_images"] = md_images
    docx = next((data for name, data in files.items() if name.endswith(".docx")), None)
    html = next(
        (data for name, data in files.items() if name.endswith(".html") and "mindmap" not in name),
        None,
    )
    pdf = next((data for name, data in files.items() if name.endswith(".pdf")), None)
    if docx is not None:
        try:
            from .delivery.docx import docx_stats

            stats = docx_stats(docx)
            metrics["docx_images"] = stats["images"]
            if stats["images"] != md_images:
                issues.append(f"DOCX 内嵌图 {stats['images']} 张 ≠ Markdown 图 {md_images} 张")
                failed.add("docx")
        except Exception as exc:
            issues.append(f"DOCX 自检失败：{type(exc).__name__}")
            failed.add("docx")
    if html is not None:
        inline = html.count(b'src="data:image')
        metrics["html_images"] = inline
        if inline != md_images:
            issues.append(f"HTML 内联图 {inline} 张 ≠ Markdown 图 {md_images} 张")
            failed.add("html")
        if b"<script" in html.lower():
            issues.append("自包含 HTML 含脚本")
            failed.add("html")
    if pdf is not None:
        try:
            from .delivery.pdf import verify_pdf

            verify_pdf(pdf, markdown)
            metrics["pdf"] = "ok"
        except Exception as exc:
            issues.append(f"PDF 自检失败：{exc}")
            failed.add("pdf")
    for fmt, data in (("html", html), ("docx", docx), ("pdf", pdf)):
        if data is None:
            continue
        try:
            residue = residual_math_issues(fmt, data)
        except Exception as exc:
            residue = [f"{fmt.upper()} 公式检查失败：{type(exc).__name__}"]
        if residue:
            issues.extend(residue)
            failed.add(fmt)
    metrics["failed_formats"] = sorted(failed)
    return GateResult("consistency", "fail" if issues else "pass", issues, metrics)


def slides_gate(pptx: bytes, deck: dict[str, Any], markdown: str | None = None) -> GateResult:
    from .delivery.pptx import fit_report, pptx_stats
    from .delivery.pptx_visuals import visual_consistency
    from .slide_content import slide_quality

    stats = pptx_stats(pptx)
    problems = fit_report(deck)
    issues, timing = slide_quality(deck)
    if stats["with_notes"] < stats["slides"]:
        issues.append(f"{stats['slides'] - stats['with_notes']} 页缺少演讲备注")
    inconsistent = visual_consistency(pptx, deck, markdown)
    issues.extend(inconsistent)
    return GateResult(
        "slides", "fail" if inconsistent else "warn" if issues else "pass", issues,
        {**stats, **timing, "overflow": len(problems), "visual_render_review": "pending"},
    )


def territory_gate(files: dict[str, bytes]) -> GateResult:
    """地名规范：所有可见交付物（各格式的可见文本）逐一检查。"""
    from .territory import check_files

    report = {}
    unreadable = []
    for name, data in files.items():
        try:
            report.update(check_files({name: data}))
        except Exception as exc:
            unreadable.append((name, type(exc).__name__))
    issues = [
        f"{name}:{hit.line} {hit.code}：{hit.excerpt}"
        for name, hits in report.items()
        for hit in hits[:5]
    ]
    issues.extend(f"{name}: 无法检查文件内容（{kind}）" for name, kind in unreadable)
    failed_files = [*report, *(name for name, _ in unreadable)]
    return GateResult(
        "territory",
        "fail" if failed_files else "pass",
        issues[:20],
        {
            "files_checked": len(files),
            "files_failed": len(failed_files),
            "failed_files": failed_files,
        },
    )


def scholarly_gate(
    markdown: str,
    *,
    template: TaskTemplate,
    query: str,
    citations: list[str],
    min_citations: int,
    policy: Any,
    source_texts: list[str] | None = None,
    references: dict[str, str] | None = None,
    document_keys: dict[str, str] | None = None,
) -> GateResult:
    """学术写作质量：文体、摘要引用、引用堆砌、重复来源、引用下限、时效、局限说明。"""
    from .quality import effective_citation_minimum
    from .scholarly import evaluate, source_counts

    body = citation_text(_body_without_references(markdown))
    used = {
        int(number) for match in _CITE.findall(body) for number in re.split(r"\s*[,，]\s*", match)
    }
    cited = [citations[i - 1] for i in sorted(used) if 1 <= i <= len(citations)]
    report = evaluate(
        markdown,
        template_key=template.key,
        query=query,
        citations=cited,
        used_citations=len(cited),
        min_citations=effective_citation_minimum(
            min_citations, source_counts(citations, document_keys=document_keys)[0]
        ),
        policy=policy,
        source_texts=source_texts,
        references=references,
        document_keys=document_keys,
    )
    issues = [f.render() for f in report.errors] + [
        f"（建议）{f.render()}" for f in report.warnings
    ]
    status: Status = "warn" if report.errors else "pass"
    return GateResult(
        "scholarly",
        status,
        issues[:20],
        {
            **report.metrics,
            "findings": [{"code": f.code, "message": f.render()} for f in report.findings],
        },
    )


def revision_gate(extras: dict[str, Any]) -> GateResult | None:
    """写作返工结果：用尽返工次数后仍存在的问题。"""
    log = extras.get("revision") if isinstance(extras, dict) else None
    if not isinstance(log, dict):
        return None
    remaining = [str(item) for item in log.get("remaining") or []]
    attempts = int(log.get("attempts") or 1)
    advisories = [str(item) for item in log.get("advisories") or []]
    metrics = {
        "attempts": attempts,
        "revisions": max(0, attempts - 1),
        "remaining": len(remaining),
        "remaining_issues": remaining,
        "advisory_issues": advisories,
    }
    if remaining:
        return GateResult("revision", "warn", remaining[:12], metrics)
    return GateResult("revision", "pass", advisories[:12], metrics)


HARD_GATES = frozenset(
    {
        "citation",
        "structure",
        "scholarly",
        "review",
        "revision",
        "analysis",
        "structured_content",
        "node_evidence",
        "prose_evidence",
        "provided_corpus",
        "task_content",
        "review_coverage",
        "source_processing",
        "user_requirements",
        "table_evidence",
        "table_scope",
        "evidence_quote_length",
    }
)


def overall(results: list[GateResult]) -> Status:
    if any(result.status == "fail" for result in results):
        return "fail"
    if any(result.status == "warn" for result in results):
        return "warn"
    return "pass"


__all__ = [
    "GateResult",
    "body_length",
    "citation_gate",
    "consistency_gate",
    "length_gate",
    "markdown_gate",
    "overall",
    "review_gate",
    "revision_gate",
    "scholarly_gate",
    "slides_gate",
    "structure_gate",
]
