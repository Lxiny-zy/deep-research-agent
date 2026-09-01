"""Markdown 投影：三种格式里最"窄"的那一种，因此规则是**只用最保守的子集**。

## 为什么保守

HTML 与打印的渲染器是我们自己（浏览器行为确定），但 Markdown 的消费者是未知的——
GitHub、Obsidian、Typora、VS Code、pandoc 对脚注语法、``<details>``、数学公式、
标题锚点 slug 的支持各不相同。押注任何一个都会在别处碎掉。

所以只用四样东西：**标题、GFM 表格、链接、引用块（加粗）**。不用脚注语法
``[^1]``，不用 ``<details>``，不依赖锚点跳转——``[n]`` 本身就是人类可读的键，
读者用它在文末附录里查，不需要点击。

## 图表怎么办

Markdown 渲染不了矢量图。但因为 ``ChartBlock`` 结构上必须指向一个 ``TableBlock``
（见 ``document`` 的不变量），降级成**源表**是无损的——图从来只是表的一种投影，
这里只是选择了另一种投影。所以 MD 里不会出现"这里本来有张图"的空洞，而是出现
那张图背后的全部数字。
"""

from __future__ import annotations

from .charts import ChartDataError
from .document import (
    ChartBlock,
    EvidenceRecord,
    ProseBlock,
    ReportDocument,
    TableBlock,
    TableCell,
    TableColumn,
)

_SEMANTIC_LABEL = {
    "not_checked": "语义未检查",
    "supported": "语义支持",
    "unsupported": "语义不支持",
    "uncertain": "语义存疑",
}
_CONSISTENCY_LABEL = {
    "not_checked": "一致性未检查",
    "clear": "未检测到冲突",
    "conflicted": "存在冲突",
}
_CORROBORATION_LABEL = {
    "not_checked": "交叉印证未检查",
    "single_source": "单一来源",
    "corroborated": "已交叉印证",
    "disputed": "来源存在争议",
}


def render_markdown(doc: ReportDocument) -> str:
    """把结构化文档投影成 Markdown。"""
    sections: list[str] = []
    if doc.query:
        sections.append(f"# {_inline(doc.query)}")
    sections.append(f"> {_inline(doc.disclaimer)}")

    overview = _overview(doc)
    if overview:
        sections.append(overview)

    for block in doc.blocks:
        rendered = _block(block, doc)
        if rendered:
            sections.append(rendered)

    if doc.references:
        lines = [f"[{entry.index}] {_inline(entry.render())}" for entry in doc.references]
        sections.append("## 参考来源\n\n" + "\n\n".join(lines))

    if doc.evidence:
        sections.append(_evidence_appendix(doc))

    return "\n\n".join(sections).strip() + "\n"


def _block(block: object, doc: ReportDocument) -> str:
    if isinstance(block, ProseBlock):
        return block.markdown.strip()
    if isinstance(block, TableBlock):
        return _table(block)
    if isinstance(block, ChartBlock):
        return _chart_as_table(block, doc)
    return ""


def _table(table: TableBlock, *, heading_level: int = 2) -> str:
    parts: list[str] = []
    if table.title:
        parts.append(f"{'#' * heading_level} {_inline(table.title)}")
    if not table.columns:
        return "\n\n".join(parts)

    headers = ["对象"] + [_column_header(column) for column in table.columns]
    # GFM 对齐语法是最广泛支持的那一小撮之一，数值列右对齐可以放心用。
    aligns = ["---"] + ["---:" if column.numeric else "---" for column in table.columns]
    lines = [f"| {' | '.join(headers)} |", f"| {' | '.join(aligns)} |"]

    for row in table.rows:
        label = _inline(row.label)
        if row.citation:
            label = f"{label} [{row.citation}]"
        cells = [label]
        for column in table.columns:
            cells.append(_cell_text(row.cell(column.key)))
        lines.append(f"| {' | '.join(cells)} |")
    parts.append("\n".join(lines))

    if table.notes:
        notes = "\n".join(f"{index}. {_inline(note)}" for index, note in enumerate(table.notes, 1))
        parts.append(f"**口径脚注**\n\n{notes}")
    if table.caption:
        parts.append(f"*{_inline(table.caption)}*")
    return "\n\n".join(parts)


def _column_header(column: TableColumn) -> str:
    label = _inline(column.label)
    if column.unit:
        label = f"{label}（{_inline(column.unit)}）"
    # 同一指标在不同口径下会被拆成多列，列头必须指向脚注说明为什么不能直接比。
    if column.note_ref:
        label = f"{label}（注 {column.note_ref}）"
    return label


def _cell_text(cell: TableCell) -> str:
    """单元格文本。

    未报告就写"未报告"——空白会被读者当成"零"或"排版问题"，而这两种误读都比
    显式承认缺失更糟。
    """
    if not cell.reported:
        return "未报告"
    text = _inline(cell.value)
    # 多来源就把来源都列出来——交叉印证过的数值显示两个引用，正是双源门禁的产物。
    for citation in cell.citations:
        text = f"{text} [{citation}]"
    if cell.note_ref:
        text = f"{text}（注 {cell.note_ref}）"
    if cell.disputed:
        text = f"{text} ⚠"
    return text


def _chart_as_table(chart: ChartBlock, doc: ReportDocument) -> str:
    """图降级成"指向源表的一节"。

    降级说明只说"图形见 HTML/PDF"，不说"此处缺一张图"——数字一个没少，缺的只是那层
    视觉编码。源表本身一定会在文档里单独渲染（``ChartBlock.source_table`` 必须指向
    ``blocks`` 里的某个 ``TableBlock``），所以这里只引用、不重复整张表：同一份数字在
    一份文档里出现两遍，读者会以为那是两组不同的数据。
    """
    table = doc.table(chart.source_table)
    if table is None:
        raise ChartDataError(f"图 {chart.id} 的源表 {chart.source_table} 不在文档中")
    title = chart.title or table.title
    table_name = table.title or table.id
    parts = [
        f"## {_inline(title)}",
        f"> 本格式不渲染矢量图形；该图的完整源数据见表《{_inline(table_name)}》。",
    ]
    if chart.caption:
        parts.append(f"*{_inline(chart.caption)}*")
    return "\n\n".join(parts)


def _overview(doc: ReportDocument) -> str:
    o = doc.overview
    if not o.records:
        return ""
    rows = [
        ("证据记录", o.records),
        ("原文匹配", o.verbatim_matched),
        ("语义支持", o.semantically_supported),
        ("已交叉印证", o.corroborated),
        ("存在冲突", o.conflicted),
    ]
    lines = ["| 指标 | 数量 |", "| --- | ---: |"]
    lines += [f"| {name} | {value} |" for name, value in rows]
    if o.blocked_sources is None:
        lines.append("| 来源被拦截 | 不可用（本次事件流未含来源策略审计事件） |")
    else:
        lines.append(f"| 来源被拦截 | {o.blocked_sources} |")
    return "## 证据链概览\n\n" + "\n".join(lines)


def _evidence_appendix(doc: ReportDocument) -> str:
    """证据附录：交互侧栏在纯文本里的等价物。

    按 ``[n]`` 分组，顺序与参考来源一致，读者拿正文里的角标就能直接定位——
    不依赖锚点跳转，因为各家 Markdown 的标题 slug 规则并不一致。
    """
    # 免责声明已在文首出现一次。线性文档里再重复一遍只是噪声——它之所以在 HTML
    # 侧栏里重复，是因为侧栏会被单独打开阅读，而附录不会脱离文档存在。
    parts = ["## 证据附录"]
    by_citation: dict[int, list[EvidenceRecord]] = {}
    for record in doc.evidence:
        by_citation.setdefault(record.citation, []).append(record)

    for citation in sorted(by_citation):
        records = by_citation[citation]
        reference = next((r.reference for r in records if r.reference), "")
        head = reference or next((r.source_url for r in records if r.source_url), "")
        parts.append(f"### [{citation}] {_inline(head)}")
        for record in records:
            parts.append(_evidence_record(record))
    return "\n\n".join(parts)


def _evidence_record(record: EvidenceRecord) -> str:
    lines = [f"**论断**：{_inline(record.statement)}"]
    if record.quantity_label:
        lines.append(f"**数值**：{_inline(record.quantity_label)}")
    if record.conditions_label:
        # 条件与数值同等重要：没有条件的数值不可比，对照表里就不能与别人并列。
        lines.append(f"**成立条件**：{_inline(record.conditions_label)}")
    if record.context:
        # 引用块承载检索快照上下文；逐字引文在其中加粗，替代 HTML 的 <mark> 高亮。
        lines.append("")
        lines.append(f"> {_inline(_emphasise(record.context, record.quote))}")
    elif record.quote:
        lines.append("")
        lines.append(f"> {_inline(record.quote)}")

    status = [
        "原文匹配" if record.verbatim_verified else "未通过原文匹配",
        _SEMANTIC_LABEL.get(record.semantic_status, record.semantic_status),
        _CONSISTENCY_LABEL.get(record.consistency_status, record.consistency_status),
        _CORROBORATION_LABEL.get(record.corroboration_status, record.corroboration_status),
    ]
    if record.corroboration_status != "not_checked":
        status[-1] += f" · {record.independent_source_count} 个独立来源"
    if record.semantic_status != "not_checked":
        status[1] += f"（模型判定置信度 {round(record.semantic_confidence * 100)}%）"
    lines.append("")
    lines.append(f"**验证状态**：{' / '.join(status)}")
    if record.source_section:
        lines.append(f"**原文节**：{_inline(record.source_section)}")

    # 原先只活在 HTML tooltip 里的三个 reason 字段在这里是正式内容。
    # tooltip 在触屏不可达、打印不输出，靠它承载信息本身就是缺陷。
    if record.quantity_status != "not_applicable":
        label = {"verified": "数值已在原文中核对", "unsupported": "数值未通过原文核对"}.get(
            record.quantity_status, record.quantity_status
        )
        lines.append(f"**数值校验**：{label}")

    for label, value in (
        ("验证说明", record.verification_reason),
        ("数值校验说明", record.quantity_reason),
        ("语义判定理由", record.semantic_reason),
        ("印证说明", record.corroboration_reason),
        ("冲突原因", record.contradiction_reason),
    ):
        if value.strip():
            lines.append(f"**{label}**：{_inline(value)}")
    if record.contradicts_claim_ids:
        lines.append(f"**与以下论断矛盾**：{', '.join(record.contradicts_claim_ids)}")
    if record.claim_id:
        lines.append(f"**claim**：`{record.claim_id}`")
    if record.content_hash:
        # 完整哈希，不截断：截断版本无法用来核对快照，那就失去了留证的意义。
        lines.append(f"**检索快照哈希**：`{record.content_hash}`")
    if record.source_url:
        lines.append(f"**来源**：{_inline(record.source_url)}")
    return "\n\n".join(lines)


def _emphasise(context: str, quote: str) -> str:
    """在上下文中把逐字引文加粗——HTML 里 ``<mark>`` 的纯文本等价物。"""
    needle = quote.strip()
    if not needle:
        return context
    index = context.find(needle)
    if index < 0:
        return context
    return f"{context[:index]}**{needle}**{context[index + len(needle) :]}"


def _inline(text: str) -> str:
    """转义会破坏表格与行结构的字符。

    只处理管道符与换行:表格里裸 ``|`` 会多切一列,证据上下文里的换行会把引用块
    截断成两段。其余 Markdown 字符保持原样——过度转义会让正文里的正常标点变成
    一串反斜杠。
    """
    return text.replace("|", "\\|").replace("\r\n", " ").replace("\n", " ").strip()
