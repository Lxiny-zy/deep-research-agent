"""Explicit bounded table joins; original tables and join decisions stay recoverable."""

from __future__ import annotations

import csv
import hashlib
import io
from typing import Any, Literal

from pydantic import BaseModel, Field

from .analysis import MAX_COLUMNS, MAX_ROWS, DatasetError
from .analysis_inputs import normalize_missing_markers
from .contract import DATASET_MAX_CHARS
from .datasets import profile_csv


class MergeTable(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    csv: str = Field(min_length=1, max_length=DATASET_MAX_CHARS)


class JoinStep(BaseModel):
    sheet: str = Field(min_length=1, max_length=120)
    left_keys: list[str] = Field(min_length=1, max_length=4)
    right_keys: list[str] = Field(min_length=1, max_length=4)
    how: Literal["inner", "left"] = "inner"
    relationship: Literal["one_to_one", "many_to_one"] = "one_to_one"


class DatasetMerge(BaseModel):
    tables: list[MergeTable] = Field(min_length=2, max_length=8)
    base: str = Field(min_length=1, max_length=120)
    joins: list[JoinStep] = Field(min_length=1, max_length=7)


def _frame(table: MergeTable) -> Any:
    import pandas as pd

    profile_csv(table.name, table.csv)
    text = table.csv.strip()
    delimiter = "\t" if text.splitlines()[0].count("\t") > text.splitlines()[0].count(",") else ","
    normalized, _ = normalize_missing_markers(text, delimiter)
    reader = csv.reader(io.StringIO(normalized), delimiter=delimiter, strict=True)
    header = [name.strip().lstrip("\ufeff") for name in next(reader)]
    if not all(header) or len(set(header)) != len(header):
        raise DatasetError(f"工作表 {table.name} 的列名为空或重复，无法明确连接键")
    if any(row and len(row) != len(header) for row in reader):
        raise DatasetError(f"工作表 {table.name} 的记录列数不一致，未进行合并")
    frame = pd.read_csv(io.StringIO(normalized), sep=delimiter, dtype=str)
    frame.columns = header
    return frame


def _keys(frame: Any, keys: list[str], table: str, *, unique: bool) -> None:
    if len(set(keys)) != len(keys) or not set(keys).issubset(frame.columns):
        raise DatasetError(f"{table} 的连接键不存在或重复")
    values = frame[keys]
    if values.isna().any().any() or values.apply(lambda col: col.str.strip().eq("")).any().any():
        raise DatasetError(f"{table} 的连接键包含缺失值，不能把未知对象连接在一起")
    if unique and values.duplicated().any():
        raise DatasetError(f"{table} 的连接键不唯一；当前关系不允许重复键，未放大记录数")


def merge_tables(request: DatasetMerge) -> dict[str, Any]:
    import pandas as pd

    if sum(len(table.csv) for table in request.tables) > DATASET_MAX_CHARS * 2:
        raise DatasetError("合并输入过大，请先选择需要的表或抽样；不会截断使用")
    tables = {table.name: table for table in request.tables}
    order = [request.base, *(step.sheet for step in request.joins)]
    if (
        len(tables) != len(request.tables)
        or len(order) != len(set(order))
        or set(order) != set(tables)
    ):
        raise DatasetError("须明确选择主表和每张连接表，表名不能重复、遗漏或重复连接")
    frames = {name: _frame(tables[name]) for name in order}
    joined = frames[request.base].copy()
    provenance: dict[str, Any] = {
        "base": request.base,
        "tables": [
            {
                "name": name, "rows": len(frames[name]),
                "input_sha256": hashlib.sha256(tables[name].csv.strip().encode()).hexdigest(),
            }
            for name in order
        ],
        "joins": [],
        "columns": [
            {"output": column, "table": request.base, "column": column}
            for column in joined.columns
        ],
        "notes": [],
    }
    for step in request.joins:
        if len(step.left_keys) != len(step.right_keys):
            raise DatasetError("左右连接键数量必须相同，并按相同顺序对应")
        right = frames[step.sheet]
        _keys(joined, step.left_keys, "当前主表", unique=step.relationship == "one_to_one")
        _keys(right, step.right_keys, step.sheet, unique=True)
        left_index = pd.MultiIndex.from_frame(joined[step.left_keys])
        right_index = pd.MultiIndex.from_frame(right[step.right_keys])
        unmatched_left = int((~left_index.isin(right_index)).sum())
        unmatched_right = int((~right_index.isin(left_index)).sum())
        renames = {
            column: f"{step.sheet}.{column}"
            for column in right.columns if column not in step.right_keys
        }
        if set(renames.values()) & set(joined.columns):
            raise DatasetError("连接后的列名与已有列冲突，请修改来源表的列名")
        left_rows = len(joined)
        joined = joined.merge(
            right.set_index(step.right_keys).rename(columns=renames),
            left_on=step.left_keys, right_index=True, how=step.how,
            validate=step.relationship, sort=False,
        ).reset_index(drop=True)
        if joined.empty:
            raise DatasetError(f"与 {step.sheet} 连接后没有匹配记录，无法执行分析")
        if len(joined) > MAX_ROWS or len(joined.columns) > MAX_COLUMNS:
            raise DatasetError("连接结果超过行列上限，未截断或使用部分数据")
        provenance["joins"].append({
            **step.model_dump(), "left_rows": left_rows, "right_rows": len(right),
            "rows": len(joined),
            "unmatched_left": unmatched_left, "unmatched_right": unmatched_right,
        })
        provenance["columns"].extend(
            {"output": output, "table": step.sheet, "column": column}
            for column, output in renames.items()
        )
        if unmatched_left or unmatched_right:
            action = (
                "保留未匹配的主表行，连接字段为空"
                if step.how == "left" else "未匹配的主表行未纳入结果"
            )
            provenance["notes"].append(
                f"连接 {step.sheet}：主表 {unmatched_left} 行、右表 {unmatched_right} 行未匹配；"
                f"{action}。"
            )
    output = joined.to_csv(index=False, lineterminator="\n")
    table = profile_csv("合并结果", output)
    provenance["input_sha256"] = hashlib.sha256(table.csv.encode()).hexdigest()
    return {**table.profile(), "csv": table.csv, "merge": provenance}
