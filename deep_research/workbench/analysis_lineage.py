"""Frozen analysis design and missing-row provenance without raw identifier values."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

LINEAGE_VERSION = 1
MAX_BITMAP_BYTES = 1_000_000


def describe_design(result: Any, *, repeated: bool) -> dict[str, Any]:
    scope = result.scope or {}
    pairing = scope.get("pairing")
    kind = "repeated_unsupported" if repeated else "paired" if pairing else "independent"
    return {
        "version": LINEAGE_VERSION,
        "comparison": kind,
        "subject_columns": list(scope.get("subject_columns", [])),
        "measures": list(result.numeric),
        "groups": list(result.categorical),
        "background": list(scope.get("background", [])),
        "unused": [
            column
            for column in result.columns
            if column not in result.numeric + result.categorical + list(scope.get("background", []))
        ],
        "pairing": pairing,
        "independence": "not_assessed",
        "missing_policy": "per_statistic_complete_cases",
        "imputation": "none",
        "outlier_policy": "retain_and_flag",
        "source_sha256": result.input_sha256,
    }


def build_lineage(frame: Any, result: Any) -> dict[str, Any]:
    """At most 50,000×60 missing bits, not a repeated copy of sensitive cells."""
    rows = len(frame)
    bitmap_size = (rows + 7) // 8
    if bitmap_size * len(frame.columns) > MAX_BITMAP_BYTES:
        raise ValueError("缺失记录索引超过容量，不能静默截断")
    columns = []
    for column in frame.columns:
        bitmap = bytearray(bitmap_size)
        missing = frame[column].isna().to_numpy().nonzero()[0]
        for position in missing:
            bitmap[int(position) // 8] |= 1 << (int(position) % 8)
        columns.append(
            {
                "column": str(column),
                "missing": len(missing),
                "bitmap": base64.b64encode(bitmap).decode("ascii"),
            }
        )
    rules: dict[str, dict[str, Any]] = {}

    def add(label: str, required: list[str], where: dict[str, str] | None = None) -> None:
        where = where or {}
        required = list(dict.fromkeys(required))
        identity = json.dumps([required, where], ensure_ascii=False, sort_keys=True)
        key = hashlib.sha256(identity.encode()).hexdigest()[:20]
        if key in rules:
            if label not in rules[key]["uses"]:
                rules[key]["uses"].append(label)
            return
        population = frame.index.to_series().notna()
        for column, value in where.items():
            population &= frame[column].notna() & (frame[column].astype(str) == value)
        complete = population & frame[required].notna().all(axis=1)
        n_population, n_complete = int(population.sum()), int(complete.sum())
        rules[key] = {
            "id": key,
            "uses": [label],
            "required_columns": required,
            "where": where,
            "population_rows": n_population,
            "included_rows": n_complete,
            "excluded_missing_rows": n_population - n_complete,
            "outside_scope_rows": rows - n_population,
        }

    for item in result.describe:
        add("描述统计：" + item["variable"], [item["variable"]])
    for test in result.tests:
        if test.get("paired"):
            add("配对差异：" + test["variable"], [test["left"], test["right"]])
        else:
            add(test["method"] + "：" + test["variable"], [test["variable"], test["group"]])
        for group in test.get("group_summaries", []):
            add(
                "分组描述：" + test["variable"],
                [test["variable"]],
                {test["group"]: str(group["label"])},
            )
    for item in result.correlations:
        where = {item["group_column"]: str(item["group"])} if item.get("group_column") else {}
        add("Pearson：" + item["a"] + " / " + item["b"], [item["a"], item["b"]], where)
    return {
        "version": LINEAGE_VERSION,
        "rows": rows,
        "source_sha256": result.input_sha256,
        "columns": columns,
        "rules": list(rules.values()),
        "row_numbering": "解析后的输入记录，从1开始，不含表头；不是物理CSV行号",
    }


def missing_rows(lineage: dict[str, Any], *, expected_hash: str) -> Any:
    """Decode only known bounded masks; emit positions, never raw subject IDs."""
    rows = lineage.get("rows")
    if (
        lineage.get("version") != LINEAGE_VERSION
        or lineage.get("source_sha256") != expected_hash
        or not isinstance(rows, int)
        or isinstance(rows, bool)
        or not 0 <= rows <= 50_000
    ):
        raise ValueError("缺失记录索引与冻结输入不一致")
    specs = lineage.get("columns", [])
    if not isinstance(specs, list) or len(specs) > 60:
        raise ValueError("缺失记录索引列数无效")
    masks = []
    for spec in specs:
        encoded = spec["bitmap"]
        if not isinstance(encoded, str) or len(encoded) > ((rows + 7) // 8) * 2 + 8:
            raise ValueError("缺失记录位图超出容量")
        bitmap = base64.b64decode(encoded, validate=True)
        if len(bitmap) != (rows + 7) // 8 or sum(v.bit_count() for v in bitmap) != spec["missing"]:
            raise ValueError("缺失记录位图与计数不一致")
        masks.append((spec["column"], bitmap))
    for position in range(rows):
        absent = [name for name, bitmap in masks if bitmap[position // 8] & (1 << (position % 8))]
        if absent:
            yield position + 1, absent


def design_markdown(design: dict[str, Any] | None, lineage: dict[str, Any] | None) -> str:
    if not design:
        return ""
    labels = {
        "independent": "按独立样本方法分析，独立性仍依赖实验设计",
        "paired": "同一行、同一对象的两列配对测量",
        "repeated_unsupported": "重复观测，仅描述统计；本轮不支持重复测量推断",
    }
    lines = [
        "### 分析设计与记录范围",
        "",
        "- 设计：" + labels[design["comparison"]] + "。",
        "- 观测对象标识列："
        + ("、".join(design["subject_columns"]) or "未明确")
        + "；未把整表行数认定为独立对象数。",
        "- 测量列："
        + ("、".join(design["measures"]) or "无")
        + "；比较分组："
        + ("、".join(design["groups"]) or "无")
        + "。",
        "- 仅说明背景："
        + ("、".join(design["background"]) or "无")
        + "；本轮未分析的列："
        + ("、".join(design["unused"]) or "无")
        + "。",
        "- 缺失处理：按每项统计所需字段选取完整记录，未补值；不同统计项的有效记录可能不同。",
        "- 极端值处理：保留原值并提示核对，未自动删除；原始输入记录保持不变。",
        "- 工作簿的“设计与纳入规则”“缺失记录”“统计记录索引”“正文结果定位”可用于逐项复核。",
    ]
    if design.get("pairing"):
        pairing = design["pairing"]
        lines.append(
            "- 配对差值方向："
            + pairing["right"]["column"]
            + " − "
            + pairing["left"]["column"]
            + "；缺少任一测量的记录不进入该配对检验。"
        )
    return "\n".join(lines)


def attach_design(
    markdown: str, design: dict[str, Any] | None, lineage: dict[str, Any] | None
) -> str:
    import re

    summary = design_markdown(design, lineage)
    if not summary or summary in markdown:
        return markdown
    heading = re.search(r"(?m)^##\s+分析计划\s*$", markdown)
    if heading is None:
        return markdown  # The existing structure gate diagnoses a missing plan.
    return markdown[: heading.end()] + "\n\n" + summary + "\n" + markdown[heading.end() :]


def add_xlsx_traceability(workbook: Any, result: Any) -> None:
    """Project frozen design plus existing fact IDs; do not rerun statistics."""
    from .statistic_bindings import bind_statistics, statistic_facts

    design = getattr(result, "design", None)
    lineage = getattr(result, "lineage", None)
    source_hash = getattr(result, "input_sha256", "")
    design_sheet = workbook.create_sheet("设计与纳入规则")
    design_sheet.append(["项目", "说明"])
    if design:
        for key, label in [
            ("comparison", "比较设计"),
            ("subject_columns", "对象标识列（不导出原始标识值）"),
            ("measures", "测量列"),
            ("groups", "比较分组"),
            ("background", "背景列"),
            ("unused", "本轮未分析列"),
            ("pairing", "配对列与差值方向：right-left"),
        ]:
            value = design.get(key)
            if key == "comparison":
                value = {
                    "independent": "独立样本方法；观测独立性尚未验证",
                    "paired": "同一行、同一对象的两列配对测量",
                    "repeated_unsupported": "重复观测；仅描述统计，不进行重复测量推断",
                }.get(value, "未记录")
            design_sheet.append(
                [
                    label,
                    json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (dict, list))
                    else value
                    if value is not None
                    else "未记录",
                ]
            )
        design_sheet.append(["推断前提", "观测独立性依赖实验设计，不能由分布检验证明"])
        design_sheet.append(["处理规则", "逐统计项选取完整记录；未填补缺失，未自动删除极端值"])
    else:
        design_sheet.append(["历史设计", "本快照未记录新增设计摘要；未重新推断或补算"])
    design_sheet.append(["输入文本SHA-256", source_hash or "未记录"])
    design_sheet.append([])
    design_sheet.append(
        [
            "统计用途",
            "必须非缺失的列",
            "限定分组",
            "范围内记录",
            "纳入记录",
            "因缺失未纳入",
            "范围外记录",
            "规则ID",
        ]
    )
    for rule in (lineage or {}).get("rules", []):
        design_sheet.append(
            [
                "；".join(rule["uses"]),
                "、".join(rule["required_columns"]),
                json.dumps(rule["where"], ensure_ascii=False),
                *[
                    rule[key]
                    for key in (
                        "population_rows",
                        "included_rows",
                        "excluded_missing_rows",
                        "outside_scope_rows",
                        "id",
                    )
                ],
            ]
        )
    missing_sheet = workbook.create_sheet("缺失记录")
    missing_sheet.append(["解析后的记录序号（非CSV物理行号）", "缺失字段（不含原始标识值）"])
    known_counts = getattr(result, "missing", {})
    if (
        not lineage
        or not source_hash
        or any(
            item.get("column") not in known_counts for item in (lineage or {}).get("columns", [])
        )
    ):
        missing_sheet.append(["未记录", "原输入哈希或逐行缺失索引不完整，未根据新规则补造"])
    else:
        if lineage["rows"] != result.rows or any(
            item["missing"] != known_counts[item["column"]] for item in lineage["columns"]
        ):
            raise ValueError("逐行缺失索引与冻结行数或字段计数不一致")
        for position, columns in missing_rows(lineage, expected_hash=source_hash):
            missing_sheet.append([position, "、".join(columns)])
    ledger = {
        key: getattr(result, key, [] if key != "rows" else None)
        for key in (
            "rows",
            "describe",
            "tests",
            "correlations",
            "composition",
        )
    }
    facts = statistic_facts(ledger)
    index = workbook.create_sheet("统计记录索引")
    index.append(
        [
            "统计记录ID",
            "范围",
            "变量",
            "另一变量",
            "分组列",
            "组/比较对象",
            "方法",
            "统计量",
            "值",
            "输入SHA-256",
        ]
    )
    indexed = {}
    for row, fact in enumerate(facts, 2):
        indexed[fact["id"]] = row
        scope_label = {
            "dataset": "整表记录",
            "variable": "单变量有效观测",
            "group": "单个分组",
            "comparison": "组间或配对检验",
            "correlation": "组内相关" if fact.get("group_column") else "总体相关",
            "composition": "分组/背景列构成",
            "composition_group": "构成中的单组",
        }.get(fact["scope"], fact["scope"])
        index.append(
            [
                fact["id"],
                scope_label,
                fact.get("variable", ""),
                fact.get("other_variable", ""),
                fact.get("group_column", ""),
                json.dumps(fact.get("groups", fact.get("group", "")), ensure_ascii=False),
                fact.get("method", ""),
                fact["statistic"],
                fact["value"] if fact["value"] is not None else "未记录",
                source_hash or "未记录",
            ]
        )
    locations = workbook.create_sheet("正文结果定位")
    locations.append(
        ["核验正文行号（不含交付页眉）", "定位摘录", "统计量", "引用值", "统计记录ID", "机械核对"]
    )
    body = getattr(result, "report_markdown", "")
    if body:
        bindings = bind_statistics(body, ledger)
        lines = body.splitlines()
        for claim in bindings["claims"]:
            line = claim["line"]
            excerpt = lines[line - 1] if 0 < line <= len(lines) else ""
            if len(excerpt) > 1000:
                excerpt = excerpt[:1000] + "…（仅定位摘录，完整内容见报告）"
            problems = [issue for issue in bindings["issues"] if issue.startswith(f"第 {line} 行")]
            for fact_id in claim["fact_ids"] or [""]:
                locations.append(
                    [
                        line,
                        excerpt,
                        claim["statistic"],
                        claim["value"],
                        fact_id,
                        "；".join(problems) if problems else "数值绑定可定位；不替代语义核验",
                    ]
                )
                if fact_id in indexed:
                    from openpyxl.worksheet.hyperlink import Hyperlink

                    cell = locations.cell(locations.max_row, 5)
                    cell.hyperlink = Hyperlink(
                        ref=cell.coordinate, location=f"'统计记录索引'!A{indexed[fact_id]}",
                        display=fact_id,
                    )
                    cell.style = "Hyperlink"
    else:
        locations.append(["未记录", "未提供对应核验正文，未虚构结果定位"])
    for sheet in (missing_sheet, index, locations):
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
    index.column_dimensions["A"].width = 28
    locations.column_dimensions["B"].width = 64
