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

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "issues": self.issues,
            "metrics": self.metrics,
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
    minimum = template.min_citations if min_citations is None else min_citations
    if minimum and distinct < minimum:
        issues.append(f"已核验引用 {distinct} 个，少于要求的 {minimum} 个")
    status: Status = "fail" if out_of_range else ("warn" if issues else "pass")
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
        },
    )


def markdown_gate(markdown: str) -> GateResult:
    issues: list[str] = []
    raw = _RAW_TAG.findall(markdown)
    if raw:
        issues.append(f"含裸 HTML 标签 {len(raw)} 处（如 {raw[0][:40]}）")
    if _ANCHOR_LINK.search(markdown):
        issues.append("含页内锚点链接（跨格式交付后失效）")
    if markdown.count("```") % 2:
        issues.append("存在未闭合的代码块")
    return GateResult("markdown", "fail" if raw else ("warn" if issues else "pass"), issues)


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
            {"length": length},
        )
    return GateResult("length", "pass", [], {"length": length})


def review_gate(extras: dict[str, Any]) -> GateResult:
    score = extras.get("score")
    if isinstance(score, int) and 1 <= score <= 10:
        return GateResult("review", "pass", [], {"score": score})
    return GateResult("review", "fail", ["评审没有给出 1–10 的整数评分（格式：评分：N/10）"])


def consistency_gate(markdown: str, files: dict[str, bytes]) -> GateResult:
    """同源多格式交叉计数。只比对实际生成了的格式。"""
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
    metrics["failed_formats"] = sorted(failed)
    return GateResult("consistency", "fail" if issues else "pass", issues, metrics)


def slides_gate(pptx: bytes, deck: dict[str, Any]) -> GateResult:
    from .delivery.pptx import fit_report, pptx_stats

    stats = pptx_stats(pptx)
    problems = fit_report(deck)
    issues = []
    if stats["with_notes"] < stats["slides"]:
        issues.append(f"{stats['slides'] - stats['with_notes']} 页缺少演讲备注")
    for problem in problems:
        if problem.get("empty"):
            issues.append(f"第 {problem['slide']} 页没有要点")
        else:
            issues.append(
                f"第 {problem['slide']} 页约 {problem['lines']} 行 / "
                f"{problem['bullets']} 条要点，可能溢出"
            )
    return GateResult(
        "slides", "warn" if issues else "pass", issues, {**stats, "overflow": len(problems)}
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
    from .scholarly import evaluate

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
        min_citations=min_citations,
        policy=policy,
        source_texts=source_texts,
        references=references,
        document_keys=document_keys,
    )
    issues = [f.render() for f in report.errors] + [
        f"（建议）{f.render()}" for f in report.warnings
    ]
    status: Status = "warn" if report.errors else "pass"
    return GateResult("scholarly", status, issues[:20], dict(report.metrics))


def revision_gate(extras: dict[str, Any]) -> GateResult | None:
    """写作返工结果：用尽返工次数后仍存在的问题。"""
    log = extras.get("revision") if isinstance(extras, dict) else None
    if not isinstance(log, dict):
        return None
    remaining = [str(item) for item in log.get("remaining") or []]
    attempts = int(log.get("attempts") or 1)
    metrics = {"attempts": attempts, "revisions": max(0, attempts - 1), "remaining": len(remaining)}
    if remaining:
        return GateResult("revision", "warn", remaining[:12], metrics)
    return GateResult("revision", "pass", [], metrics)


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
