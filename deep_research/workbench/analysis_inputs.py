"""Conservative input notes and column-role checks for tabular analysis."""

from __future__ import annotations

import csv
import io
import re
import threading
from collections import Counter
from typing import Any

_CALENDAR = {
    "year", "years", "month", "date", "datetime", "timestamp", "calendar_year",
    "birth_year", "birth_date", "dob", "年份", "年度", "月份", "日期", "时间戳", "出生年份",
}
_BACKGROUND = (
    {"sex", "gender", "性别", "雌雄"},
    {"island", "岛屿"},
    {"site", "location", "地区", "地点", "采集地点"},
)
_MISSING = {"?", "？", "na", "n/a", "nan", "null", "none", "#na", "#n/a", "<na>"}
_CSV_LIMIT_LOCK = threading.Lock()


def calendar_column(column: str) -> bool:
    name = column.strip().casefold().replace(" ", "_")
    if name.startswith(("age_", "duration_", "elapsed_", "followup_", "follow_up_")):
        return False
    return name in _CALENDAR or name.endswith(("_year", "_date", "_timestamp"))


def background_aliases(column: str) -> set[str]:
    name = column.strip().casefold()
    if calendar_column(column):
        if "year" in name or name in {"年份", "年度", "出生年份"}:
            return {name, "year", "years", "年份", "年度", "逐年"}
        return {name, "date", "time", "日期", "时间", "月份"}
    return next((aliases | {name} for aliases in _BACKGROUND if name in aliases), set())


def explicitly_grouped(column: str, question: str) -> bool:
    aliases = background_aliases(column)
    protected = bool(aliases)
    aliases = aliases or {column.strip().casefold()}
    requested = False
    for clause in re.split(r"[，,。.;；\n]|\b(?:but|and)\b|但是|但|且", question.casefold()):
        if not any(
            re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", clause)
            for alias in aliases
        ):
            continue
        if re.search(
            r"作(?:为)?背景|(?:只|仅)(?:作|描述|说明)|"
            r"不(?:做|用|作|按|比较|分析|进行|检验|分组)|不要|无需|排除|"
            r"\b(?:as\s+background|background\s+only|exclude|without)\b|"
            r"\b(?:not|never|don't)\s+(?:use|test\w*|compar\w*|group\w*|analy\w*)\b|"
            r"\bonly\s+(?:describ\w*|report\w*)\b",
            clause,
        ):
            return False
        if re.search(
            r"比较|差异|分组|组间|影响|效应|不同|分层|按|"
            r"\b(?:compar\w*|differ\w*|group\w*|effect\w*|influence\w*|across|by)\b",
            clause,
        ):
            requested = True
    return requested or not protected


def normalize_missing_markers(text: str, delimiter: str) -> tuple[str, list[str]]:
    # Preserve pandas' support for large cells within the admitted input. The
    # csv module's limit is process-wide; never lower it under another parser.
    with _CSV_LIMIT_LOCK:
        csv.field_size_limit(max(csv.field_size_limit(), len(text)))
    reader = csv.reader(io.StringIO(text), delimiter=delimiter, strict=True)
    header = next(reader)
    counts: dict[str, Counter[str]] = {}
    output = io.StringIO()
    writer = csv.writer(output, delimiter=delimiter)
    writer.writerow(header)
    for row in reader:
        for index, value in enumerate(row):
            marker = value.strip()
            if marker.casefold() not in _MISSING:
                continue
            column = header[index].strip() if index < len(header) else f"col{index}"
            counts.setdefault(column, Counter())[marker] += 1
            # A nonempty NA token preserves a one-column missing observation;
            # replacing its whole line by whitespace would let pandas drop it.
            row[index] = "NaN"
        writer.writerow(row)
    notes = [
        f"{column}：" + "、".join(f"{marker} 共 {count} 个" for marker, count in markers.items())
        + "已按缺失值处理，未补值。"
        for column, markers in counts.items()
    ]
    return output.getvalue(), notes


def suspicious_values(frame: Any, columns: list[str]) -> list[str]:
    notes = []
    for column in columns:
        values = frame[column].dropna()
        if values.empty:
            continue
        sentinel = values.isin([-999, -9999, -99999, -999999])
        extreme = sentinel & False
        if len(values) >= 8:
            lower, upper = values.quantile([0.25, 0.75])
            spread = upper - lower
            if spread > 0:
                extreme = (values < lower - 3 * spread) | (values > upper + 3 * spread)
            elif spread == 0:
                extreme = (values - lower).abs() > max(abs(float(lower)), 1) * 3
        selected = values[sentinel | extreme]
        if selected.empty:
            continue
        examples = []
        selected_values = set(selected)
        for position, value in enumerate(frame[column], 1):
            if value not in selected_values:
                continue
            kind = "疑似缺失编码" if value in {-999, -9999, -99999, -999999} else "极端值"
            examples.append(f"第 {position} 条记录={value:g}（{kind}）")
            if len(examples) == 5:
                break
        notes.append(
            f"{column} 检出 {len(selected)} 条需核对的观测：{'、'.join(examples)}。"
            "本轮保留原值，未自动删除或改成缺失；统计可能受这些观测影响，请核对原始记录。"
        )
    return notes
