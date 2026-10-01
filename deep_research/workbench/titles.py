"""Display titles derived from completed analysis, separate from task instructions."""

from __future__ import annotations

from typing import Any


def analysis_title(ledger: dict[str, Any]) -> str:
    tests = ledger.get("tests") or []
    if any(test.get("paired") for test in tests):
        title = "配对比较"
    elif tests:
        title = "差异检验"
    elif ledger.get("numeric"):
        title = "描述统计"
    else:
        return "数据概况报告"
    if ledger.get("correlations"):
        title += "与相关性"
    return title + "分析报告"


def analysis_meta(ledger: dict[str, Any]) -> str:
    source = ledger.get("source") or {}
    label = "合成示例数据" if ledger.get("synthetic") else source.get("filename", "用户提供的数据")
    rows = ledger.get("rows")
    return f"数据：{label}" + (f" · {rows} 行" if isinstance(rows, int) else "")


def without_repeated_title(markdown: str, title: str) -> str:
    first, _, rest = markdown.lstrip().partition("\n")
    return rest.lstrip() if first == "# " + title else markdown
