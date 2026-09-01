"""从已通过证据门禁的 findings 透视出对照表。

## 这一层的位置

它是"报告里的表格由代码渲染"这条原则的落地点：``TableBlock`` 的**生产者**。
上游是 ``Finding``（带 ``entity`` / ``Quantity`` / ``ExperimentConditions``），
下游是 Markdown / HTML / 打印三个渲染器与图表（图必有源表）。

LLM 全程不参与：它只负责在正文里叙述，表格的每一个格子都来自一条通过门禁的
finding，因此都带 ``[n]``、都能回溯到逐字原文与内容哈希。

## 最关键的一条规则：口径不同的数值不进同一列

同一个 PSNR 数字在 28 波段与 31 波段、256×256 裁剪与全图、不同 mask 与训练集下
**并不可比**。把它们放进同一列，表格越整齐越误导——读者会直接横向比较。

所以透视按 ``(指标, 实验条件签名)`` 分列：条件不同就是两列，列头各自指向脚注写明
口径差异。**未标注条件自成一签名**（标注为"口径未标注"），不与任何已知口径合并——
"不知道"不是"相同"，静默合并等于替读者做了一个我们没有依据的判断。

## 数值冲突不静默裁决

同一 ``(对象, 指标, 口径)`` 出现多个不同数值时，表格**并列呈现**并标注冲突，而不是
挑一个。但这样的格子会被**排除出图表**：把有争议的值画成一个点等于替读者做了裁决，
而图上看不出那里有分歧。表里能看到，图上不出现——这是刻意的不对称。
"""

from __future__ import annotations

import math
import re

from ..guardrails import report_eligible
from ..models import ExperimentConditions, Finding, Quantity, ResearchResult
from .document import TableBlock, TableCell, TableColumn, TableRow

# 一张表至少要有两个可比对象或两个指标才有意义。1×1 的"表"只是把一句话画上框。
_MIN_ROWS = 2
_MIN_CELLS = 2

_UNKNOWN_CONDITIONS = "口径未标注"
_SLUG_RE = re.compile(r"[^0-9a-z]+")


def pivot_tables(
    results: list[ResearchResult],
    index_by_url: dict[str, int],
    *,
    require_corroboration: bool = False,
    table_id: str = "quantitative_comparison",
    title: str = "定量对照表",
) -> list[TableBlock]:
    """把带数值的 findings 透视成对照表。

    只收录**通过报告准入**的 findings（含数值校验：声明了数值却在原文里找不到的
    直接不参与建表），且必须同时有 ``entity`` 与可用的 ``Quantity``——缺任一项就
    无法确定它在表里的行与列。

    数据不足以成表时返回空列表，不产出退化的 1×1 表。
    """
    usable = [
        finding
        for result in results
        for finding in result.findings
        if _usable(finding, index_by_url, require_corroboration=require_corroboration)
    ]
    if not usable:
        return []

    # 条件签名 → 脚注序号。按首次出现顺序编号，保证同一输入下表稳定可比。
    signatures: dict[tuple, int] = {}
    notes: list[str] = []
    for finding in usable:
        signature = _signature(finding.conditions)
        if signature not in signatures:
            signatures[signature] = len(signatures) + 1
            notes.append(_describe_signature(finding.conditions))

    # 列 = (指标, 条件签名)。指标只有一种口径时不加脚注，避免给单口径表凭空添噪声。
    metric_signatures: dict[str, set[tuple]] = {}
    for finding in usable:
        assert finding.quantity is not None  # _usable 已保证
        metric_signatures.setdefault(_metric_key(finding.quantity.metric), set()).add(
            _signature(finding.conditions)
        )

    columns: dict[str, TableColumn] = {}
    # (行, 列) → 收集到的数值。冲突要能被发现，所以先收集再定稿。
    collected: dict[tuple[str, str], list[Finding]] = {}
    row_order: list[str] = []

    for finding in usable:
        quantity = finding.quantity
        assert quantity is not None
        metric = _metric_key(quantity.metric)
        signature = _signature(finding.conditions)
        multi = len(metric_signatures[metric]) > 1
        key = f"{metric}__{signatures[signature]}" if multi else metric
        if key not in columns:
            columns[key] = TableColumn(
                key=key,
                label=quantity.metric.strip() or metric,
                unit=quantity.unit.strip(),
                align="right",
                numeric=True,
                note_ref=signatures[signature] if multi else None,
            )
        entity = finding.entity.strip()
        if entity not in row_order:
            row_order.append(entity)
        collected.setdefault((entity, key), []).append(finding)

    rows = [
        TableRow(label=entity, cells=_cells(entity, columns, collected, index_by_url))
        for entity in row_order
    ]

    reported = sum(1 for row in rows for cell in row.cells.values() if cell.reported)
    if len(rows) < _MIN_ROWS or reported < _MIN_CELLS:
        return []

    return [
        TableBlock(
            id=table_id,
            title=title,
            columns=list(columns.values()),
            rows=rows,
            notes=notes if len(signatures) > 1 else [],
            caption=(
                "每格数值均来自通过证据门禁的论断，角标指向其出处；"
                "空格为原文未报告。口径不同的数值已分列，不可跨列直接比较。"
            ),
        )
    ]


def _usable(finding: Finding, index_by_url: dict[str, int], *, require_corroboration: bool) -> bool:
    """能否参与建表。

    四个条件缺一不可：通过报告准入（含数值校验）、有出处角标、有对象（行）、
    有可用数值（列）。任一缺失时这条 finding 仍会出现在正文与证据附录里，
    只是无法定位到表格的某一格。
    """
    if not report_eligible(finding, require_corroboration=require_corroboration):
        return False
    if finding.source_url not in index_by_url:
        return False
    if not finding.entity.strip():
        return False
    quantity = finding.quantity
    if quantity is None or quantity.value is None or not math.isfinite(quantity.value):
        return False
    return bool(quantity.metric.strip())


def _cells(
    entity: str,
    columns: dict[str, TableColumn],
    collected: dict[tuple[str, str], list[Finding]],
    index_by_url: dict[str, int],
) -> dict[str, TableCell]:
    cells: dict[str, TableCell] = {}
    for key in columns:
        findings = collected.get((entity, key))
        if not findings:
            # 留空＝原文未报告。渲染器负责显示"未报告"，这里不填占位数字。
            cells[key] = TableCell()
            continue
        cells[key] = _cell(findings, index_by_url)
    return cells


def _cell(findings: list[Finding], index_by_url: dict[str, int]) -> TableCell:
    """把一格里收集到的（可能多条）findings 定稿成一个单元格。

    多条来源报告**同一**数值 → 并列引用（这正是交叉印证的产物）。
    多条来源报告**不同**数值 → 并列数值 + 标注冲突，并把 ``numeric`` 置空把它
    排除出图表：有争议的值画成一个点等于替读者做了裁决。
    """
    citations: list[int] = []
    for finding in findings:
        index = index_by_url[finding.source_url]
        if index not in citations:
            citations.append(index)
    citations.sort()

    # 按显示写法去重：38.36 与 38.360 是同一个数，不该被当成分歧。
    by_display: dict[str, Finding] = {}
    for finding in findings:
        assert finding.quantity is not None
        by_display.setdefault(_display(finding.quantity), finding)

    first = findings[0]
    assert first.quantity is not None
    note_ref = None
    if len(by_display) == 1:
        return TableCell(
            value=_display(first.quantity),
            numeric=first.quantity.value,
            citations=citations,
            note_ref=note_ref,
        )

    return TableCell(
        value=" / ".join(by_display),
        numeric=None,  # 有分歧的值不进图表
        citations=citations,
        note_ref=note_ref,
        disputed=True,
    )


def _display(quantity: Quantity) -> str:
    """单元格显示文本。保留比较符：">38.36" 与 "38.36" 是不同的断言。"""
    number = quantity.rendered.strip() or f"{quantity.value:g}"
    comparator = quantity.comparator if quantity.comparator not in ("", "=") else ""
    text = f"{comparator}{number}"
    if quantity.uncertainty is not None:
        text += f" ± {quantity.uncertainty:g}"
    return text


def _signature(conditions: ExperimentConditions | None) -> tuple:
    """实验条件的可比性签名。

    ``None`` 与全空条件都落到同一个"未标注"签名，且它**不与任何已知口径合并**——
    "不知道条件"不等于"条件相同"，静默合并等于替读者做一个没有依据的判断。
    """
    if conditions is None or conditions.is_empty():
        return ("",)
    return (
        conditions.dataset.strip().casefold(),
        conditions.split.strip().casefold(),
        conditions.bands,
        conditions.spectral_range.strip().casefold(),
        conditions.scenes.strip().casefold(),
        conditions.acquisition.strip().casefold(),
        conditions.spatial_size.strip().casefold(),
        conditions.calibration.strip().casefold(),
        conditions.prototype_validation.strip().casefold(),
        conditions.coding_mode.strip().casefold(),
        conditions.dispersive_element.strip().casefold(),
        conditions.protocol.strip().casefold(),
        conditions.train_data.strip().casefold(),
        conditions.hardware.strip().casefold(),
        conditions.notes.strip().casefold(),
    )


def _describe_signature(conditions: ExperimentConditions | None) -> str:
    if conditions is None or conditions.is_empty():
        return _UNKNOWN_CONDITIONS
    return conditions.describe()


def _metric_key(metric: str) -> str:
    """指标名归一化成列键。``PSNR`` 与 ``psnr`` 是同一列；未知字符折叠成下划线。"""
    slug = _SLUG_RE.sub("_", metric.strip().casefold()).strip("_")
    return slug or "metric"
