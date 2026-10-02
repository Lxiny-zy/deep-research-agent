"""Statistical scope evidence and narrow, provable count contradictions."""

from __future__ import annotations

import re
from typing import Any

STATISTICS_POLICY_VERSION = 1
STATISTICS_RULES = (
    "本任务是统计解释核验。数字在台账出现并不代表任意位置都能使用："
    "必须同时对应变量、分组、统计量、有效观测范围与检验方法。"
    "严格区分整表行数、各变量有效总样本量、单个分组样本量和变量对有效样本量；"
    "同一句把总样本量用于各分组、把合并统计用于组内统计，即使句中其他数字正确也不支持。"
    "对‘均、每组、各组、其余及其分组’等全称描述，逐项对照所涵盖分组，不能只看整表总数。"
)


def sample_size_scopes(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    scopes = [
        {"scope": "variable_total", "variable": row["variable"], "n": row["n"]}
        for row in ledger.get("describe", [])
        if "variable" in row and "n" in row
    ]
    for test in ledger.get("tests", []):
        scopes.extend(
            {
                "scope": "single_group",
                "variable": test["variable"],
                "group_column": test["group"],
                "group": row["label"],
                "n": row["n"],
            }
            for row in test.get("group_summaries", [])
            if "label" in row and "n" in row
        )
    scopes.extend(
        {"scope": "variable_pair", "variables": [row["a"], row["b"]], "n": row["n"]}
        for row in ledger.get("correlations", [])
        if "n" in row
    )
    return scopes


def count_scope_issues(markdown: str, ledger: dict[str, Any]) -> list[str]:
    """Reject group counts larger than every recorded group; not a general parser.

    A count shared by all groups cannot exceed every group in the ledger,
    regardless of which variable the sentence refers to. Ambiguous counts,
    subgroup comparisons and other statistics remain subject to model review.
    """
    counts = [row["n"] for row in sample_size_scopes(ledger) if row["scope"] == "single_group"]
    if not counts:
        return []
    largest = max(counts)
    scope = r"(?:每组|各组|各分组|所有分组|所有组|分组均|\beach\s+group\b|\ball\s+groups\b)"
    value = (
        r"(?:(?<![A-Za-z0-9_])n\s*=\s*|样本量\s*(?:均|都)?\s*(?:为|是|=)?\s*|"
        r"sample\s+size\s*(?:is|of|=)?\s*)(\d+)(?!\d|[eE]|\.\d)"
    )
    pattern = re.compile(scope + r"(?P<between>[^。；;，,\n]{0,30}?)" + value, re.I)
    issues = []
    for line_number, line in enumerate(markdown.splitlines(), 1):
        for clause in re.split(r"[。；;，,]", line):
            if re.search(
                r"是否|假设|如果|不(?:是|为|等于)|并非|不能|不得|[？?]|\b(?:if|not)\b", clause, re.I
            ):
                continue
            for match in pattern.finditer(clause):
                if re.match(r"\s*[/×*+−-]", clause[match.end() :]):
                    continue
                if re.search(r"总|合并|整体|相加|之和|合计|combined|total", match["between"], re.I):
                    continue
                n = int(match[2])
                if n > largest:
                    issues.append(
                        f"第 {line_number} 行：分组样本量 n={n} 大于台账中任一分组的有效样本量"
                        f"（最大 {largest}），不能把总体样本量用于各分组"
                    )
    return issues
