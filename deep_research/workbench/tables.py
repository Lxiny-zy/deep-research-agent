"""Compile table specifications from admitted evidence; audit cells independently."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..guardrails import report_eligible
from ..models import ExperimentConditions, Finding, ResearchResult
from ..report.document import TableBlock, TableCell, TableColumn, TableRow
from ..report.markdown import _table
from ..report.pivot import _cell
from .delivery.markdown import _parser, parse_blocks, plain
from .support import (
    SUPPORT_POLICY_VERSION,
    SupportDecision,
    SupportReviewer,
    SupportUnit,
    digest,
    evidence_id,
    evidence_records,
)

TABLES_KEY = "_evidence_tables"
TABLES_VERSION = 1
COMPARISON_CONTEXT_VERSION = 1
TABLE_RULES = (
    "逐格核对表格。每个单元格只允许使用指定的发现，不得借其他格的证据。"
    "同时检查表题、行名、列名、单位与口径。训练块尺寸不能放在仿真输入尺寸列，"
    "标准差不能当作均值，不同任务、数据范围、条件不能静默合并。"
    "原始表格引用包含完整表头与行名，必须按真实交叉位置核对。"
    "‘未报告’若明确限定为本次已核验发现未列，只是本表的材料范围说明，"
    "不得将其理解为全文没有报告。其他来源缺失断言仍须原文支持。"
)
TABLE_INSTRUCTIONS = (
    "所有表格用 evidence-table 代码块输出 JSON 规格，不直接写 Markdown/HTML/LaTeX 表格。"
    '规格格式：{"id":"t1","title":"表 1 比较",'
    '"columns":[{"key":"m","label":"指标","field":"quantity",'
    '"metric":"原始指标名"}],"rows":[{"label":"原始对象名",'
    '"cells":{"m":["发现ID"]}}]}。每格只填发现 ID 数组，不填数值、单位或自编结论。'
    "field 可为 quantity、statement、conditions 或 conditions.字段名；"
    "quantity 必须填写原始 metric。条件字段见素材结构，不能重命名条件含义。"
    "同指标的不同实验条件用列的 scope 对象限定，键为 conditions 中的字段，值须与素材完全相同。"
    "空数组表示本次已核验发现未列，该范围内已有的同对象同指标数值会由代码补入。"
    "单位、数值、误差和条件由代码取值。定性列从 statement 取完整已核验陈述。"
    '要重现引文中的整张原始表，使用 {"id":"t1","title":"表 1 …",'
    '"source_finding_id":"发现ID","source_table_index":0}，保留原表全部行列。'
    "如果原文结构无法解析，返回问题说明并修订规格，不得手抄数值绕过检查。"
)


class SpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ColumnSpec(SpecModel):
    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    field: str
    metric: str = ""
    scope: dict[str, str | int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_field(self) -> ColumnSpec:
        allowed = {"quantity", "statement", "conditions"} | {
            "conditions." + key for key in ExperimentConditions.model_fields
        }
        if self.field not in allowed or (self.field == "quantity" and not self.metric.strip()):
            raise ValueError("表格字段无效或数值列未指定原始指标")
        if any(key not in ExperimentConditions.model_fields for key in self.scope):
            raise ValueError("表格指定了未知的条件字段")
        return self


class RowSpec(SpecModel):
    label: str = Field(min_length=1)
    cells: dict[str, list[str]]


class TableSpec(SpecModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    title: str = Field(min_length=1)
    columns: list[ColumnSpec] = Field(default_factory=list)
    rows: list[RowSpec] = Field(default_factory=list)
    source_finding_id: str = ""
    source_table_index: int = Field(0, ge=0)
    ledger_path: list[str | int] = Field(default_factory=list)

    @model_validator(mode="after")
    def rectangular(self) -> TableSpec:
        if self.source_finding_id or self.ledger_path:
            if self.rows or self.columns or (self.source_finding_id and self.ledger_path):
                raise ValueError("原始表格导入不能同时指定改写行列")
            return self
        keys = [column.key for column in self.columns]
        if not keys or len(set(keys)) != len(keys) or not self.rows:
            raise ValueError("表格必须有非重复列和数据行")
        if len({row.label for row in self.rows}) != len(self.rows):
            raise ValueError("表格行名重复")
        if any(set(row.cells) != set(keys) for row in self.rows):
            raise ValueError("表格规格必须显式列出每一格")
        return self


@dataclass
class RenderedTable:
    block: TableBlock
    markdown: str = ""
    units: list[SupportUnit] = field(default_factory=list)
    allowed: dict[str, list[str]] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)


def admitted_findings(
    results: list[ResearchResult], mapping: dict[str, int], corroboration: bool = False
) -> dict[str, Finding]:
    output: dict[str, Finding] = {}
    for result in results:
        for finding in result.findings:
            if finding.source_url in mapping and report_eligible(
                finding, require_corroboration=corroboration
            ):
                key = evidence_id(finding)
                previous = output.get(key)
                if previous is not None and (
                    previous.quantity != finding.quantity
                    or previous.conditions != finding.conditions
                ):
                    raise ValueError("同一发现存在冲突的结构化数值或条件")
                output[key] = finding
    return output


def _clean_latex(text: str) -> str:
    text = text.strip().strip("$")
    text = text.replace(r"\pm", "±").replace(r"\%", "%")
    text = re.sub(r"\\(?:textbf|textit|mathrm|text)\{([^{}]*)\}", r"\1", text)
    if "\\" in text or "{" in text or "}" in text:
        raise ValueError("原表单元格含未支持的 LaTeX 结构，不能猜测其值")
    return re.sub(r"\s+", " ", text).strip()


def source_tables(quote: str) -> list[list[list[str]]]:
    """Only unambiguous rectangular tables; preserve quoted row/column order."""
    tables = [
        [[plain(cell) for cell in row] for row in block.rows]
        for block in parse_blocks(quote)
        if block.kind == "table"
    ]
    for match in re.finditer(
        r"\\begin\{tabular\}\{[^{}]+\}([\s\S]*?)(?:\\end\{tabular\}|$)", quote
    ):
        raw = re.sub(r"\\(?:toprule|midrule|bottomrule|hline)\b", "", match[1])
        rows = []
        for line in re.split(r"\\\\", raw):
            if not line.strip():
                continue
            if "&" not in line:
                raise ValueError("原始表格含无法定位行列的内容")
            rows.append([_clean_latex(cell) for cell in re.split(r"(?<!\\)&", line)])
        tables.append(rows)
    for rows in tables:
        if len(rows) < 2 or len(rows[0]) < 2 or any(len(row) != len(rows[0]) for row in rows):
            raise ValueError("原始表格行列不完整，不能将解析失败当作未报告")
    return tables


def _unit(
    rendered: RenderedTable, row: str, column: str, value: str, ids: list[str], citations: list[int]
) -> None:
    uid = f"{rendered.block.id}-cell-{len(rendered.units)}"
    rendered.units.append(
        SupportUnit(
            uid,
            f"{row}；{column}：{value}",
            context=rendered.block.title,
            citations=citations,
        )
    )
    rendered.allowed[uid] = ids


def _conditions_key(finding: Finding) -> str:
    return digest(finding.conditions.model_dump() if finding.conditions else None)


def render_table(
    spec: TableSpec,
    results: list[ResearchResult],
    mapping: dict[str, int],
    *,
    corroboration: bool = False,
    ledger: dict[str, Any] | None = None,
    comparison_context: bool = False,
) -> RenderedTable:
    findings = admitted_findings(results, mapping, corroboration)
    rendered = RenderedTable(TableBlock(id=spec.id, title=spec.title))
    block = rendered.block
    if spec.ledger_path:
        value: Any = ledger
        for part in spec.ledger_path:
            if isinstance(value, dict) and isinstance(part, str):
                value = value.get(part)
            elif isinstance(value, list) and isinstance(part, int) and 0 <= part < len(value):
                value = value[part]
            else:
                raise ValueError("统计表路径不在当前台账中")
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(row, dict) for row in value)
        ):
            raise ValueError("统计表路径必须指向非空记录数组")
        keys = list(dict.fromkeys(key for row in value for key in row))
        block.columns = [TableColumn(key=key, label=key) for key in keys]
        for row in value:
            cells = {
                key: TableCell(
                    value=json.dumps(row[key], ensure_ascii=False)
                    if isinstance(row.get(key), (dict, list))
                    else str(row[key])
                    if row.get(key) is not None
                    else ""
                )
                for key in keys
            }
            label = str(row.get("variable") or row.get("label") or row.get("method") or "记录")
            block.rows.append(TableRow(label=label, cells=cells))
        block.caption = (
            "表注：统计范围："
            + "/".join(map(str, spec.ledger_path))
            + "；未报告表示该统计台账字段没有结果。"
        )
    elif spec.source_finding_id:
        finding = findings.get(spec.source_finding_id)
        if finding is None:
            raise ValueError("原始表格没有已核验发现依据")
        tables = source_tables(finding.evidence_quote)
        if spec.source_table_index >= len(tables):
            raise ValueError("所选发现中没有可完整解析的原始表格")
        rows = tables[spec.source_table_index]
        block.columns = [TableColumn(key=str(i), label=name) for i, name in enumerate(rows[0][1:])]
        citation = mapping[finding.source_url]
        for row in rows[1:]:
            cells = {}
            for source_column, raw in zip(block.columns, row[1:], strict=True):
                value = (
                    "" if raw.strip() in {"", "-", "--", "—", "–", "N/A", "NA", "未报告"} else raw
                )
                cells[source_column.key] = TableCell(value=value, citations=[citation])
                _unit(
                    rendered,
                    row[0],
                    source_column.label,
                    value or "未报告",
                    [spec.source_finding_id],
                    [citation],
                )
            block.rows.append(TableRow(label=row[0], citation=citation, cells=cells))
        block.caption = "表注：按所引原始表格的行列呈现；未报告表示原表该格为空或使用缺失标记。"
    else:
        block.columns = [
            TableColumn(key=c.key, label=c.label, numeric=c.field == "quantity")
            for c in spec.columns
        ]
        for row in spec.rows:
            cells = {}
            for column in spec.columns:
                ids = list(dict.fromkeys(row.cells[column.key]))
                if any(key not in findings for key in ids):
                    raise ValueError("表格引用了不存在或未通过核验的发现")
                # A model cannot erase an admitted value by returning an empty list.
                if column.field == "quantity":

                    def matches_scope(
                        finding: Finding, scope: dict[str, str | int] = column.scope
                    ) -> bool:
                        return all(
                            getattr(finding.conditions, key, None) == value
                            for key, value in scope.items()
                        )

                    candidates = [
                        key
                        for key, finding in findings.items()
                        if finding.entity.casefold() == row.label.casefold()
                        and finding.quantity is not None
                        and finding.quantity.value is not None
                        and finding.quantity.metric.casefold() == column.metric.casefold()
                        and matches_scope(finding)
                    ]
                    if ids and not column.scope:
                        selected_scopes = {_conditions_key(findings[key]) for key in ids}
                        candidates = [
                            key
                            for key in candidates
                            if _conditions_key(findings[key]) in selected_scopes
                        ]
                    ids = list(dict.fromkeys([*ids, *candidates]))
                selected = [findings[key] for key in ids]
                if not selected and column.field == "quantity":
                    for candidate in findings.values():
                        if (
                            row.label.casefold() in candidate.evidence_quote.casefold()
                            and column.metric.casefold() in candidate.evidence_quote.casefold()
                            and (
                                "\\begin{tabular}" in candidate.evidence_quote
                                or _table_texts(candidate.evidence_quote)
                            )
                        ):
                            raise ValueError(
                                "引文包含该对象和指标的原始表格，不能因没有独立数值发现而写未报告；"
                                "请按发现 ID 导入原表"
                            )
                citations = sorted({mapping[f.source_url] for f in selected})
                if column.field == "quantity" and selected:
                    if any(
                        f.quantity is None
                        or f.quantity.value is None
                        or not math.isfinite(f.quantity.value)
                        or (
                            f.quantity.uncertainty is not None
                            and not math.isfinite(f.quantity.uncertainty)
                        )
                        or f.quantity.metric.casefold() != column.metric.casefold()
                        for f in selected
                    ):
                        raise ValueError("表格数值列引用了其他指标或缺少有效数值")
                    if any(not matches_scope(finding) for finding in selected):
                        raise ValueError("表格发现与指定实验范围不一致")
                    # Never silently combine distinct units or experimental conditions.
                    signatures = {
                        (
                            f.quantity.unit,
                            digest(f.conditions.model_dump() if f.conditions else None),
                        )
                        for f in selected
                        if f.quantity
                    }
                    if len(signatures) != 1:
                        raise ValueError("同一格含不同单位或实验口径，须拆分规格")
                    cell = _cell(selected, mapping)
                    unit = selected[0].quantity.unit if selected[0].quantity else ""
                    if unit:
                        cell.value += " " + unit
                else:
                    values = []
                    for finding in selected:
                        if column.field == "statement":
                            value = finding.statement
                        elif column.field == "conditions":
                            value = finding.conditions.describe() if finding.conditions else ""
                        else:
                            value = str(
                                getattr(finding.conditions, column.field.split(".")[1], "") or ""
                            )
                        if value:
                            values.append(value)
                    cell = TableCell(value="；".join(dict.fromkeys(values)), citations=citations)
                if selected and cell.reported:
                    contexts = list(dict.fromkeys(f.statement for f in selected))
                    condition_text = ""
                    if comparison_context and column.field == "quantity":
                        condition_text = "；".join(dict.fromkeys(
                            f.conditions.describe() for f in selected
                            if f.conditions is not None and not f.conditions.is_empty()
                        ))
                        note = (
                            f"{row.label} / {column.label}：实验条件：{condition_text}"
                            if condition_text else
                            f"{row.label} / {column.label}：本次数值没有已记录的实验条件，"
                            "暂不建立跨行可比性。"
                        )
                        block.notes.append(note + " " + " ".join(f"[{i}]" for i in citations))
                        cell.note_ref = len(block.notes)
                    if column.field != "statement":
                        note = (
                            row.label
                            + " / "
                            + column.label
                            + "："
                            + "；".join(contexts)
                            + " "
                            + " ".join(f"[{i}]" for i in citations)
                        )
                        block.notes.append(note)
                    checked_value = cell.value + (
                        f"；实验条件：{condition_text}" if condition_text else ""
                    )
                    _unit(rendered, row.label, column.label, checked_value, ids, citations)
                cells[column.key] = cell
            block.rows.append(TableRow(label=row.label, cells=cells))
        block.caption = (
            "表注：未报告表示本次已核验发现中未列出该字段，不代表原文全文没有报告；"
            "适用条件见口径脚注。"
        )
    used = {key for ids in rendered.allowed.values() for key in ids}
    rendered.evidence = [
        item
        for item in evidence_records(results, mapping, corroboration=corroboration)
        if item["id"] in used
    ]
    rendered.markdown = _table(block, heading_level=3)
    return rendered


def _table_texts(markdown: str) -> list[str]:
    lines = markdown.splitlines()
    return [
        "\n".join(lines[token.map[0] : token.map[1]]).strip()
        for token in _parser().parse(markdown)
        if token.type == "table_open" and token.map
    ]


def table_preview(markdown: str) -> str:
    """Keep model-only specifications out of the live report preview."""
    lines = markdown.splitlines(keepends=True)
    for token in reversed(_parser().parse(markdown)):
        if token.type == "fence" and token.info.strip() == "evidence-table" and token.map:
            lines[token.map[0] : token.map[1]] = ["\n正在整理表格…\n\n"]
    return "".join(lines)


def render_specs(
    markdown: str,
    results: list[ResearchResult],
    mapping: dict[str, int],
    *,
    corroboration: bool = False,
    previous: dict[str, Any] | None = None,
    ledger: dict[str, Any] | None = None,
    comparison_context: bool = False,
) -> tuple[str, dict[str, Any]]:
    lines = markdown.splitlines(keepends=True)
    tokens = [
        token
        for token in _parser().parse(markdown)
        if token.type == "fence" and token.info.strip() == "evidence-table" and token.map
    ]
    if not tokens:
        return markdown, previous or {"version": TABLES_VERSION, "specs": [], "errors": []}
    record: dict[str, Any] = {
        "version": TABLES_VERSION, "specs": [], "errors": [],
        **(
            {"comparison_context_version": COMPARISON_CONTEXT_VERSION} if comparison_context else {}
        ),
    }
    replacements: list[tuple[int, int, str]] = []
    for token in tokens:
        assert token.map is not None
        try:
            spec = TableSpec.model_validate_json(token.content)
            table = render_table(
                spec, results, mapping, corroboration=corroboration, ledger=ledger,
                comparison_context=comparison_context,
            )
            record["specs"].append(spec.model_dump(mode="json"))
            replacements.append((token.map[0], token.map[1], table.markdown + "\n"))
        except ValueError as exc:
            record["errors"].append(str(exc))
    for start, end, text in reversed(replacements):
        lines[start:end] = [text]
    return "".join(lines), record


def _compiled(
    record: dict[str, Any],
    results: list[ResearchResult],
    mapping: dict[str, int],
    corroboration: bool,
    ledger: dict[str, Any] | None = None,
) -> list[RenderedTable]:
    if record.get("comparison_context_version") not in {None, COMPARISON_CONTEXT_VERSION}:
        raise ValueError("比较条件展示版本不受支持")
    specs = [TableSpec.model_validate(spec) for spec in record["specs"]]
    if len({spec.id for spec in specs}) != len(specs):
        raise ValueError("表格规格编号重复")
    return [
        render_table(
            spec, results, mapping, corroboration=corroboration, ledger=ledger,
            comparison_context=(
                record.get("comparison_context_version") == COMPARISON_CONTEXT_VERSION
            ),
        )
        for spec in specs
    ]


def _signature(tables: list[RenderedTable]) -> str:
    return digest(
        {
            "version": TABLES_VERSION,
            "support_policy": SUPPORT_POLICY_VERSION,
            "rules": TABLE_RULES,
            "tables": [
                {"block": t.block.model_dump(), "allowed": t.allowed, "evidence": t.evidence}
                for t in tables
            ],
        }
    )


def table_issues(
    markdown: str,
    results: list[ResearchResult],
    mapping: dict[str, int],
    record: Any,
    *,
    corroboration: bool = False,
    require_review: bool = True,
    ledger: dict[str, Any] | None = None,
) -> list[str]:
    texts = _table_texts(markdown)
    if any(
        token.type == "fence" and token.info.strip() == "evidence-table"
        for token in _parser().parse(markdown)
    ):
        return ["表格规格尚未成功渲染"]
    if re.search(r"<table\b|\\begin\{(?:tabular|longtable)\}", markdown, re.I):
        return ["正文含未绑定规格的 HTML/LaTeX 表格"]
    if not texts:
        return []
    if not isinstance(record, dict) or record.get("version") != TABLES_VERSION:
        return ["表格缺少代码渲染规格，不能交付模型直接写出的表格"]
    try:
        tables = _compiled(record, results, mapping, corroboration, ledger)
    except (KeyError, TypeError, ValueError) as exc:
        return ["表格规格不能从当前已核验发现重建：" + str(exc)]
    expected = [text for table in tables for text in _table_texts(table.markdown)]
    if Counter(texts) != Counter(expected):
        return ["表格内容与代码按已核验发现渲染的结果不同"]
    # Captions and scope notes are part of the same frozen table, not editable model prose.
    if any(table.markdown.strip() not in markdown for table in tables):
        return ["表格标题、单位或口径脚注已被改写"]
    if record.get("errors"):
        return ["表格规格存在未解决错误：" + str(record["errors"])]
    if not require_review:
        return []
    if record.get("review_hash") != _signature(tables):
        return ["缺少与当前表格和来源一致的逐格核验"]
    try:
        decisions = [SupportDecision.model_validate(d) for d in record["decisions"]]
    except (KeyError, TypeError, ValueError):
        return ["表格逐格核验记录无法解析"]
    units = {u.id: (u, t) for t in tables for u in t.units}
    if len(decisions) != len(units) or {d.unit_id for d in decisions} != set(units):
        return ["表格核验未处理每一个有依据的单元格"]
    issues = []
    for decision in decisions:
        unit, table = units[decision.unit_id]
        checker = SupportReviewer(
            None, table.evidence, 0, check_fulltext=False, check_formulas=False
        )
        if (
            decision.verdict != "supported"
            or not decision.evidence_ids
            or not set(decision.evidence_ids) <= set(table.allowed[unit.id])
            or checker.alignment_issue(unit, decision)
        ):
            issues.append(f"表格 {unit.id} 未通过逐格依据检查：{decision.reason}")
    return issues


async def review_tables(
    markdown: str,
    record: dict[str, Any],
    results: list[ResearchResult],
    mapping: dict[str, int],
    llm: Any,
    capacity: int,
    *,
    corroboration: bool = False,
    ledger: dict[str, Any] | None = None,
) -> list[str]:
    issues = table_issues(
        markdown,
        results,
        mapping,
        record,
        corroboration=corroboration,
        require_review=False,
        ledger=ledger,
    )
    if issues or not _table_texts(markdown):
        return issues
    tables = _compiled(record, results, mapping, corroboration, ledger)
    signature = _signature(tables)
    if record.get("review_hash") != signature:
        decisions = []
        for table in tables:
            # Separate evidence scopes prevent a sibling cell from lending its citation.
            for unit in table.units:
                checker = SupportReviewer(
                    llm,
                    [e for e in table.evidence if e["id"] in table.allowed[unit.id]],
                    capacity,
                    system_rules=TABLE_RULES,
                    check_fulltext=False,
                    check_formulas=False,
                )
                decisions.extend(await checker.review([unit]))
        record["review_hash"] = signature
        record["decisions"] = [decision.model_dump(mode="json") for decision in decisions]
    return table_issues(
        markdown, results, mapping, record, corroboration=corroboration, ledger=ledger
    )
