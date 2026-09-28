"""结构化报告文档与多格式渲染。

设计要点在 ``document`` 的模块 docstring 里：**报告先是结构，再是文本**，且
**图表结构上必须指向源表**，所以"凭空画一张图"不可表达。

格式的分工：

* ``markdown``——最保守子集（标题 / GFM 表格 / 链接 / 引用块），图降级成源表；
* ``charts``——内联 SVG，HTML 与打印共用同一份矢量图；
* HTML / 打印布局——由前端消费本模型渲染（复用已有的 react-markdown 管线），
  因此不需要在 Python 侧引入 Markdown→HTML 依赖。
* ``latex``——固定的 ctexart/XeLaTeX 模板，面向论文和文献综述；没有 TeX
  运行时仍可下载源码，不把不可用的编译器伪装成可用能力。
"""

from __future__ import annotations

from .assemble import assemble_document
from .bundle import render_reproducibility_bundle
from .charts import (
    CHART_CSS,
    ChartAssetUnavailable,
    ChartDataError,
    render_chart,
    render_chart_pdf,
    render_chart_svg,
)
from .csv import (
    CsvExportError,
    CsvTableNotFoundError,
    CsvTableSelectionError,
    render_csv,
)
from .document import (
    DISCLAIMER,
    MAX_CHART_SERIES,
    Block,
    ChartBlock,
    ChartForm,
    EvidenceRecord,
    Overview,
    PaperSection,
    ProseBlock,
    ReferenceEntry,
    ReportDocument,
    SectionKind,
    TableBlock,
    TableCell,
    TableColumn,
    TableRow,
)
from .hsi_tables import (
    DATASET_PROTOCOL_TABLE_ID,
    EVIDENCE_STRENGTH_TABLE_ID,
    OPTICAL_CODING_TABLE_ID,
    RECONSTRUCTION_TABLE_ID,
    HsiDomainRecord,
    build_hsi_tables,
    hsi_table_schemas,
    hsi_tables_from_results,
)
from .latex import (
    ExportProfile,
    LatexExportError,
    LatexExportUnavailable,
    LatexRenderError,
    render_bibtex,
    render_latex,
    render_latex_pdf,
)
from .markdown import render_markdown
from .pdf import PdfExportError, PdfExportUnavailable, PdfRenderError, render_pdf, render_pdf_html
from .pivot import pivot_tables
from .templates import LatexTemplate, LatexTemplateName, get_latex_template, latex_template_names
from .xlsx import (
    XlsxDependencyError,
    XlsxExportError,
    XlsxTableNotFoundError,
    XlsxTableSelectionError,
    render_xlsx,
)

__all__ = [
    "CHART_CSS",
    "DISCLAIMER",
    "MAX_CHART_SERIES",
    "Block",
    "ChartBlock",
    "ChartAssetUnavailable",
    "ChartDataError",
    "ChartForm",
    "CsvExportError",
    "CsvTableNotFoundError",
    "CsvTableSelectionError",
    "XlsxDependencyError",
    "XlsxExportError",
    "XlsxTableNotFoundError",
    "XlsxTableSelectionError",
    "EvidenceRecord",
    "Overview",
    "PaperSection",
    "HsiDomainRecord",
    "OPTICAL_CODING_TABLE_ID",
    "RECONSTRUCTION_TABLE_ID",
    "DATASET_PROTOCOL_TABLE_ID",
    "EVIDENCE_STRENGTH_TABLE_ID",
    "PdfExportError",
    "PdfExportUnavailable",
    "PdfRenderError",
    "ProseBlock",
    "ReferenceEntry",
    "ReportDocument",
    "SectionKind",
    "TableBlock",
    "TableCell",
    "TableColumn",
    "TableRow",
    "assemble_document",
    "render_reproducibility_bundle",
    "pivot_tables",
    "render_chart",
    "render_chart_pdf",
    "render_chart_svg",
    "render_csv",
    "render_pdf",
    "render_pdf_html",
    "render_xlsx",
    "render_markdown",
    "ExportProfile",
    "LatexExportError",
    "LatexExportUnavailable",
    "LatexRenderError",
    "LatexTemplate",
    "LatexTemplateName",
    "get_latex_template",
    "latex_template_names",
    "render_latex",
    "render_latex_pdf",
    "render_bibtex",
    "build_hsi_tables",
    "hsi_table_schemas",
    "hsi_tables_from_results",
]
