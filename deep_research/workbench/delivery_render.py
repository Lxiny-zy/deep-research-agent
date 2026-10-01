"""Render selected formats from a frozen delivery context, without model work."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

from .gates import HARD_GATES, GateResult, consistency_gate, overall, slides_gate, territory_gate
from .publish import DeliveryBundle, DeliveryFile, _deck_from_markdown, _stats_xlsx

logger = logging.getLogger(__name__)


def render_bundle(
    context: dict[str, Any],
    preserved: list[DeliveryFile],
    *,
    retry_format: str | None = None,
    previous_failures: list[dict[str, Any]] | None = None,
) -> DeliveryBundle:
    title, markdown, stem = context["title"], context["markdown"], context["stem"]
    extras, citations = context["extras"], context["citations"]
    files = [
        replace(file, status="pass", issues=[])
        for file in preserved
        if file.format != retry_format or file.status != "fail"
    ]
    images = {
        file.name.removeprefix("figures/"): file.data
        for file in files
        if file.name.startswith("figures/")
    }
    gates = [GateResult(**gate) for gate in context["base_gates"]]
    failures = [
        dict(failure) for failure in previous_failures or [] if failure["format"] != retry_format
    ]
    wants = set(context["wants"])

    def failed(fmt: str, label: str, issues: list[str], *, retryable: bool = True) -> None:
        entry = next((f for f in failures if f["format"] == fmt), None)
        if entry is None:
            failures.append(
                {"format": fmt, "title": label, "issues": issues, "retryable": retryable}
            )
        else:
            entry["issues"] = list(dict.fromkeys([*entry["issues"], *issues]))
            entry["retryable"] = entry["retryable"] and retryable

    def render(fmt: str, suffix: str, label: str, role: str, build: Callable[[], bytes]) -> None:
        name = stem + suffix
        if any(file.name == name for file in files):
            return
        if context["blocked"] and role != "data":
            failed(fmt, label, ["正文或证据核验未通过，需要先修订研究内容"], retryable=False)
            return
        if retry_format is not None and fmt != retry_format:
            return
        try:
            data = build()
            files.append(DeliveryFile(name, fmt, label, role, data))
        except Exception as exc:
            logger.exception("delivery format %s failed", fmt)
            failed(fmt, label, [f"生成失败：{type(exc).__name__}: {exc}"[:300]])

    if not any(file.format == "md" for file in files):
        source = (
            f"# {title}\n\n{markdown}"
            if context["template"] == "dataAnalysis" and not markdown.lstrip().startswith("# ")
            else markdown
        )
        files.insert(
            0,
            DeliveryFile(f"{stem}.md", "md", f"{title}（Markdown 源）", "source", source.encode()),
        )

    from .titles import without_repeated_title

    body = without_repeated_title(markdown, title)

    def html() -> bytes:
        from .delivery.html import render_html

        return render_html(
            body, title=title, kicker=context["kicker"], meta=context["meta"], images=images
        ).encode()

    def docx() -> bytes:
        from .delivery.docx import render_docx

        return render_docx(body, title=title, meta=context["meta"], images=images)

    def pdf() -> bytes:
        from .delivery.pdf import render_pdf

        return render_pdf(body, title=title, meta=context["meta"], images=images)

    def deck() -> dict:
        from .delivery.pptx import paginate_deck

        return paginate_deck(extras.get("deck") or _deck_from_markdown(markdown, title))

    def pptx() -> bytes:
        from .delivery.pptx import render_pptx

        return render_pptx(deck(), citations=citations)

    def mindmap() -> dict:
        used = {
            key
            for d in (extras.get("node_review") or {}).get("decisions", [])
            for key in d.get("evidence_ids", [])
        }
        return {
            **extras["mindmap"],
            "sources": [
                {
                    "index": i,
                    "url": url,
                    "title": context["references"].get(url, url),
                    "quotes": list(
                        dict.fromkeys(
                            e["quote"]
                            for e in context["evidence"]
                            if e["citation"] == i and e["id"] in used
                        )
                    ),
                }
                for i, url in enumerate(citations, 1)
            ],
        }

    def map_html() -> bytes:
        from .delivery.mindmap import render_mindmap_html

        return render_mindmap_html(mindmap(), title=title).encode()

    def map_png() -> bytes:
        from .delivery.mindmap import render_mindmap_png

        return render_mindmap_png(mindmap())

    for fmt, suffix, label, role, build in (
        ("html", ".html", "阅读版", "reading", html),
        ("docx", ".docx", "Word", "report", docx),
        ("pdf", ".pdf", "PDF", "report", pdf),
        ("pptx", ".pptx", "演示文稿", "slides", pptx),
    ):
        if fmt in wants:
            render(fmt, suffix, f"{title}（{label}）", role, build)
    if "mindmap" in wants and extras.get("mindmap"):
        render("html", "-mindmap.html", f"{title}（交互导图）", "reading", map_html)
        render("png", "-mindmap.png", f"{title}（导图图片）", "figure", map_png)
    if context.get("statistics") is not None:
        render(
            "xlsx",
            "-statistics.xlsx",
            "统计结果表",
            "data",
            lambda: _stats_xlsx(SimpleNamespace(**context["statistics"])),
        )

    if not context["blocked"]:
        check = consistency_gate(markdown, {file.name: file.data for file in files})
        gates.append(check)
        for fmt in check.metrics.get("failed_formats", []):
            failed(fmt, fmt.upper(), check.issues)
        for file in files:
            if file.format == "pptx":
                gates.append(slides_gate(file.data, deck()))
    if failures:
        gates.append(
            GateResult(
                "render",
                "fail",
                [
                    f"{failure['title']}：{issue}"
                    for failure in failures
                    for issue in failure["issues"]
                ],
            )
        )
    gates.append(
        territory_gate(
            {file.name: file.data for file in files if file.format not in {"png", "xlsx"}}
        )
    )
    for gate in gates:
        if gate.status == "pass":
            continue
        for file in files:
            applies = (
                (
                    gate.name == "consistency"
                    and file.format in gate.metrics.get("failed_formats", [])
                )
                or (gate.name == "slides" and file.format == "pptx")
                or (
                    gate.name in HARD_GATES | {"length", "markdown"}
                    and file.role in {"source", "report", "reading", "slides"}
                )
                or (gate.name == "territory" and file.name in gate.metrics.get("failed_files", []))
                or (gate.name == "analysis" and file.format == "xlsx")
            )
            if applies:
                rank = {"pass": 0, "warn": 1, "fail": 2}
                if rank[gate.status] > rank[file.status]:
                    file.status = gate.status
                file.issues = list(dict.fromkeys([*file.issues, *gate.issues]))
    status = overall(gates)
    if (
        context["fail_on_quality"]
        and status == "warn"
        and any(gate.status == "warn" and gate.name in HARD_GATES for gate in gates)
    ):
        status = "fail"
    return DeliveryBundle(
        context["template"],
        title,
        files,
        gates,
        status,
        context["generated_at"],
        failures=failures,
        render_context=context,
    )
