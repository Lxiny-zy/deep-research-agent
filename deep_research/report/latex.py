"""Controlled academic LaTeX projection for :class:`ReportDocument`.

The report model is the only source of content.  This module owns the fixed
template, escaping, and compiler invocation; neither an LLM nor a request can
choose a package, shell flag, or executable argument.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from ..models import Source
from .document import ChartBlock, PaperSection, ProseBlock, ReportDocument, TableBlock
from .templates import LatexTemplateName, get_latex_template

ExportProfile = Literal["academic", "technical", "executive", "appendix"]


class LatexExportError(RuntimeError):
    """Base error for LaTeX exports."""


class LatexExportUnavailable(LatexExportError):
    """Raised when XeLaTeX/latexmk is not installed."""


class LatexRenderError(LatexExportError):
    """Raised when the controlled compiler cannot produce a PDF."""


_SPECIAL = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def _escape(value: str) -> str:
    return "".join(_SPECIAL.get(char, char) for char in value)


def _url(value: str) -> str:
    # ``url`` deliberately receives the raw URL so slashes and query strings
    # remain readable.  Braces are the only characters that could terminate
    # the argument unexpectedly in ordinary source URLs.
    return (
        value.replace("\\", r"\textbackslash{}")
        .replace("\r", "")
        .replace("\n", "")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("#", r"\#")
    )


def _display_title(document: ReportDocument) -> str:
    return document.title.strip() or document.query.strip() or "Deep Research Report"


def _inline(value: str) -> str:
    """Escape Markdown-ish inline text while preserving basic emphasis/code."""

    escaped = _escape(value)
    # The replacements operate on escaped text and only emit fixed LaTeX
    # commands.  This intentionally supports a small, predictable subset.
    escaped = re.sub(r"`([^`]+)`", lambda m: rf"\texttt{{{m.group(1)}}}", escaped)
    escaped = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", lambda m: rf"\textbf{{{m.group(1)}}}", escaped)
    escaped = re.sub(
        r"(?<!\w)\*(?=\S)([^*]+?)(?<=\S)\*(?!\w)",
        lambda m: rf"\emph{{{m.group(1)}}}",
        escaped,
    )
    # Citation markers stay human-readable and deterministic.  The reference
    # list below uses the same numeric order, so no model-generated cite key is
    # ever accepted by the template.
    return escaped


def _prose(markdown: str) -> str:
    """Project the portable Markdown subset to safe LaTeX paragraphs."""

    lines = markdown.replace("\r\n", "\n").splitlines()
    out: list[str] = []
    list_kind: str | None = None
    code: list[str] | None = None

    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            out.append(rf"\end{{{list_kind}}}")
            list_kind = None

    def close_code() -> None:
        nonlocal code
        if code is not None:
            out.append(r"\begin{verbatim}")
            out.extend(code)
            out.append(r"\end{verbatim}")
            code = None

    for raw in lines:
        line = raw.strip()
        if line.startswith("```") or line.startswith("~~~"):
            close_list()
            if code is None:
                code = []
            else:
                close_code()
            continue
        if code is not None:
            code.append(raw)
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            close_list()
            level = min(len(heading.group(1)), 3)
            command = ("section", "subsection", "subsubsection")[level - 1]
            out.append(rf"\{command}{{{_inline(heading.group(2))}}}")
            continue
        bullet = re.match(r"^[-*+]\s+(.+)$", line)
        numbered = re.match(r"^\d+[.)]\s+(.+)$", line)
        if bullet or numbered:
            wanted = "itemize" if bullet else "enumerate"
            if list_kind != wanted:
                close_list()
                out.append(rf"\begin{{{wanted}}}")
                list_kind = wanted
            item_match = bullet or numbered
            assert item_match is not None
            out.append(rf"\item {_inline(item_match.group(1))}")
            continue
        if not line:
            close_list()
            if out and out[-1] != "":
                out.append("")
            continue
        quote = re.match(r"^>\s?(.*)$", line)
        if quote:
            close_list()
            out.extend([r"\begin{quote}", _inline(quote.group(1)), r"\end{quote}"])
            continue
        if re.match(r"^([-*_])(?:\s*\1){2,}$", line):
            close_list()
            out.append(r"\medskip\hrule\medskip")
            continue
        close_list()
        out.append(_inline(line))
    close_code()
    close_list()
    return "\n\n".join(part for part in out if part != "")


def _cell(value: str) -> str:
    return _inline(value.strip() or "未报告")


def _table(table: TableBlock) -> str:
    if not table.columns:
        return ""
    columns = "l" + "".join("r" if column.numeric else "l" for column in table.columns)
    lines = [rf"\begin{{longtable}}{{@{{}}{columns}@{{}}}}"]
    if table.title:
        lines.append(rf"\caption{{{_inline(table.title)}}}\\")
    lines.append(r"\toprule")
    headers = ["对象"] + [
        column.label + (f" ({column.unit})" if column.unit else "") for column in table.columns
    ]
    lines.append(" & ".join(_cell(header) for header in headers) + r" \\")
    lines.append(r"\midrule")
    lines.append(r"\endfirsthead")
    lines.append(r"\toprule")
    lines.append(" & ".join(_cell(header) for header in headers) + r" \\")
    lines.append(r"\midrule\endhead")
    for row in table.rows:
        label = row.label + (f" [{row.citation}]" if row.citation else "")
        values = [label]
        for column in table.columns:
            cell = row.cell(column.key)
            citation_suffix = (
                f" [{', '.join(str(c) for c in cell.citations)}]" if cell.citations else ""
            )
            values.append(_cell(cell.value) + citation_suffix)
        lines.append(" & ".join(values) + r" \\")
    lines.append(r"\bottomrule")
    lines.append(r"\end{longtable}")
    if table.caption:
        lines.append(rf"\noindent\emph{{{_inline(table.caption)}}}")
    if table.notes:
        lines.append(r"\begin{small}")
        for index, note in enumerate(table.notes, 1):
            lines.append(rf"\noindent\textsuperscript{{{index}}} {_inline(note)}\\")
        lines.append(r"\end{small}")
    return "\n".join(lines)


def _chart_asset_name(chart: ChartBlock, index: int) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]", "_", chart.id).strip("._-") or f"chart-{index}"
    return f"figures/{index:02d}-{slug}.pdf"


def _chart_label(chart: ChartBlock, index: int) -> str:
    slug = re.sub(r"[^A-Za-z0-9:_-]", "-", chart.id).strip("-_") or f"chart-{index}"
    return f"fig:{index}-{slug}"


def _render_block(block: object, chart_assets: dict[str, tuple[str, str]] | None = None) -> str:
    if isinstance(block, ProseBlock):
        return _prose(block.markdown)
    if isinstance(block, TableBlock):
        return _table(block)
    if isinstance(block, ChartBlock):
        asset = chart_assets.get(block.id) if chart_assets else None
        if asset is None:
            text = rf"\subsection{{{_inline(block.title or block.id)}}}"
            text += "\n" + rf"\noindent 图表数据见源表 ``{_inline(block.source_table)}''。"
            if block.caption:
                text += " " + _inline(block.caption)
            return text
        asset_name, label = asset
        caption = block.caption or f"数据取自源表 {block.source_table}，完整数值与引用见表格。"
        missing = (
            r"\fbox{\parbox{0.86\linewidth}{图形资产缺失；请查看源表 ``"
            + _inline(block.source_table)
            + r"''。}}"
        )
        return "\n".join(
            [
                r"\begin{figure}[htbp]",
                r"\centering",
                rf"\IfFileExists{{{asset_name}}}"
                rf"{{\includegraphics[width=\linewidth]{{{asset_name}}}}}{{{missing}}}",
                rf"\caption{{{_inline(caption)}}}",
                rf"\label{{{label}}}",
                r"\end{figure}",
            ]
        )
    return ""


def _section_command(section: PaperSection) -> str:
    return ("section", "subsection", "subsubsection")[min(section.level, 3) - 1]


def render_latex(
    document: ReportDocument,
    *,
    profile: ExportProfile = "academic",
    template: LatexTemplateName = "ctexart",
) -> str:
    """Render a deterministic XeLaTeX source from the whitelisted template registry."""

    if profile not in {"academic", "technical", "executive", "appendix"}:
        raise ValueError(f"unknown LaTeX export profile: {profile}")
    spec = get_latex_template(template)
    title = _display_title(document)
    chart_assets = {
        chart.id: (_chart_asset_name(chart, index), _chart_label(chart, index))
        for index, chart in enumerate(document.chart_blocks(), 1)
    }
    lines = [
        r"% Generated by Deep Research Agent. Edit the structured report, not this file.",
        spec.documentclass,
    ]
    if spec.uses_geometry:
        lines.append(r"\usepackage[left=25mm,right=25mm,top=24mm,bottom=24mm]{geometry}")
    lines.extend(spec.preamble)
    lines.extend(
        [
            rf"\usepackage{{{','.join(spec.packages)}}}",
            r"\usepackage{graphicx}",
            r"\hypersetup{hidelinks}",
            r"\setlength{\parindent}{2em}",
            r"\setlength{\parskip}{0.35em}",
            r"\setlist{nosep}",
        ]
    )
    if "fancyhdr" in spec.packages:
        lines.extend([r"\pagestyle{fancy}", r"\fancyhf{}", r"\fancyfoot[C]{\thepage}"])
    lines.extend(
        [
            rf"\title{{{_inline(title)}}}",
            rf"\date{{{_inline('生成于 Deep Research Agent')}}}",
        ]
    )
    if document.authors:
        lines.append(rf"\author{{{_inline(', '.join(document.authors))}}}")
    else:
        lines.append(r"\author{}")
    lines.extend([r"\begin{document}", r"\maketitle"])
    if document.institution:
        lines.append(rf"\begin{{center}}\small {_inline(document.institution)}\end{{center}}")
    if document.abstract:
        lines.extend([r"\begin{abstract}", _prose(document.abstract), r"\end{abstract}"])
    if document.keywords:
        lines.append(rf"\noindent\textbf{{关键词：}}{_inline('；'.join(document.keywords))}")
    if document.disclaimer:
        lines.extend([r"\begin{quote}\small", _inline(document.disclaimer), r"\end{quote}"])
    if document.sections:
        for section in document.sections:
            lines.append(rf"\{_section_command(section)}{{{_inline(section.title)}}}")
            for block in section.blocks:
                rendered = _render_block(block, chart_assets)
                if rendered:
                    lines.append(rendered)
    else:
        for block in document.blocks:
            rendered = _render_block(block, chart_assets)
            if rendered:
                lines.append(rendered)
    if document.references:
        lines.extend([r"\begin{thebibliography}{99}", r"\small"])
        for reference in document.references:
            rendered = _escape(reference.reference or reference.url)
            lines.append(
                rf"\bibitem{{ref{reference.index}}} {rendered}"
                rf"\\\url{{{_url(reference.url)}}}"
            )
        lines.append(r"\end{thebibliography}")
    if profile in {"academic", "appendix"} and document.evidence:
        lines.extend([r"\appendix", r"\section{证据附录}"])
        for record in document.evidence:
            lines.append(rf"\subsection{{来源 [{record.citation}]}}")
            if record.statement:
                lines.append(rf"\textbf{{论断：}}{_inline(record.statement)}\\")
            if record.quote:
                lines.append(rf"\begin{{quote}}{_inline(record.quote)}\end{{quote}}")
            status = "；".join(
                value
                for value in (
                    "原文匹配" if record.verbatim_verified else "未通过原文匹配",
                    record.semantic_status,
                    record.consistency_status,
                    record.corroboration_status,
                )
                if value
            )
            lines.append(rf"\small 验证状态：{_inline(status)}\\")
            if record.source_url:
                lines.append(rf"\small 来源：\url{{{_url(record.source_url)}}}")
    lines.append(r"\end{document}")
    return "\n\n".join(lines) + "\n"


def _bib_value(value: str) -> str:
    """Keep a BibTeX field single-line and protect structural delimiters."""

    return (
        value.replace("\r", " ").replace("\n", " ").replace("{", "\\{").replace("}", "\\}").strip()
    )


def render_bibtex(document: ReportDocument, *, sources: list[Source] | None = None) -> str:
    """Render the report references as a deterministic, importable BibTeX file.

    Metadata is intentionally never invented.  Sources with only a URL become
    ``@misc`` records; DOI values are copied only when they are already present
    in the persisted reference text or URL.
    """

    entries: list[str] = []
    source_by_url = {source.url: source for source in sources or []}
    doi_re = re.compile(r"10\.\d{4,9}/[^\s,}]+", re.IGNORECASE)
    for reference in document.references:
        rendered = reference.reference or reference.url
        doi_match = doi_re.search(rendered) or doi_re.search(reference.url)
        source = source_by_url.get(reference.url)
        scholarly = source.scholarly if source is not None else None
        title = source.title if source is not None and source.title else rendered
        fields = [f"  title = {{{_bib_value(title)}}},"]
        if scholarly is not None and scholarly.authors:
            fields.append(f"  author = {{{_bib_value(' and '.join(scholarly.authors))}}},")
        if scholarly is not None and scholarly.venue:
            fields.append(f"  journal = {{{_bib_value(scholarly.venue)}}},")
        if scholarly is not None and scholarly.year:
            fields.append(f"  year = {{{scholarly.year}}},")
        scholarly_doi = scholarly.doi if scholarly is not None else ""
        if doi_match or scholarly_doi:
            doi = (doi_match.group(0) if doi_match else scholarly_doi).rstrip(".")
            fields.append(f"  doi = {{{_bib_value(doi)}}},")
        fields.append(f"  url = {{{_bib_value(reference.url)}}},")
        entries.append("@misc{ref" + str(reference.index) + ",\n" + "\n".join(fields) + "\n}")
    return "\n\n".join(entries) + ("\n" if entries else "")


def render_latex_pdf(
    document: ReportDocument,
    *,
    profile: ExportProfile = "academic",
    template: LatexTemplateName = "ctexart",
    timeout_seconds: float = 120.0,
) -> bytes:
    """Compile source with a fixed XeLaTeX command and return the PDF bytes."""

    latexmk = shutil.which("latexmk")
    if not latexmk:
        raise LatexExportUnavailable(
            "论文版 PDF 需要安装 latexmk 与 XeLaTeX（例如 TeX Live）；可先下载 .tex 源文件"
        )
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    source = render_latex(document, profile=profile, template=template)
    with tempfile.TemporaryDirectory(prefix="deep-research-latex-") as raw_dir:
        workdir = Path(raw_dir)
        tex_path = workdir / "research.tex"
        tex_path.write_text(source, encoding="utf-8")
        # XeLaTeX reads only generated PDF snapshots; no shell escape or
        # external converter command is involved.
        figures = workdir / "figures"
        figures.mkdir()
        for index, chart in enumerate(document.chart_blocks(), 1):
            table = document.table(chart.source_table)
            if table is None:
                continue
            try:
                from .charts import render_chart_pdf

                (workdir / _chart_asset_name(chart, index)).write_bytes(
                    render_chart_pdf(chart, table)
                )
            except (ValueError, OSError, RuntimeError):
                # The source contains the fixed missing-asset fallback.
                continue
        argv = [
            latexmk,
            "-xelatex",
            "-pdf",
            "-no-shell-escape",
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-file-line-error",
            "-outdir=" + str(workdir),
            str(tex_path),
        ]
        env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "HOME", "TEMP", "TMP", "LANG", "LC_ALL", "TEXMFVAR"}
        }
        try:
            completed = subprocess.run(
                argv,
                cwd=workdir,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise LatexRenderError(f"LaTeX 编译超时（{timeout_seconds:.0f}s）") from exc
        pdf_path = workdir / "research.pdf"
        if completed.returncode != 0 or not pdf_path.is_file():
            detail = (completed.stderr or completed.stdout or "unknown compiler error")[-2000:]
            raise LatexRenderError(f"LaTeX 编译失败：{detail}")
        return pdf_path.read_bytes()


__all__ = [
    "ExportProfile",
    "LatexTemplateName",
    "LatexExportError",
    "LatexExportUnavailable",
    "LatexRenderError",
    "render_latex",
    "render_latex_pdf",
    "render_bibtex",
]
