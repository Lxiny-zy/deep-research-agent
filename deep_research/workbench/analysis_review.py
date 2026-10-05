"""Statistical scope evidence and narrow, provable count contradictions."""

from __future__ import annotations

import re
from typing import Any

STATISTICS_POLICY_VERSION = 3
STATISTICS_RULES = (
    "本任务是统计解释核验。数字在台账出现并不代表任意位置都能使用："
    "必须同时对应变量、分组、统计量、有效观测范围与检验方法。"
    "严格区分整表行数、各变量有效总样本量、单个分组样本量和变量对有效样本量；"
    "同一句把总样本量用于各分组、把合并统计用于组内统计，即使句中其他数字正确也不支持。"
    "对‘均、每组、各组、其余及其分组’等全称描述，逐项对照所涵盖分组，不能只看整表总数。"
    "样本构成按分类列本身计数，不自动等于删除测量缺失值后的有效分组样本量。"
    "总体检验显著只说明至少一组不同，不能推出所有组两两不同；"
    "具体组间差异须使用对应事后比较及其调整后的 p 值，不得用总体 p 值替代。"
    "方差齐性未确认时以 Welch/Games-Howell 为主；未拒绝正态性或方差齐性不是证明前提成立。"
    "正态性检验不验证观测独立，组内相关不能用总体相关代替；未显著不等于证明相同或等效。"
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
        {
            "scope": "group_variable_pair" if row.get("group_column") else "variable_pair",
            "variables": [row["a"], row["b"]],
            "n": row["n"],
            **{key: row[key] for key in ("group_column", "group") if key in row},
        }
        for row in ledger.get("correlations", [])
        if "n" in row
    )
    scopes.extend(
        {
            "scope": "composition_group",
            "group_column": item["column"],
            "group": level["label"],
            "n": level["n"],
        }
        for item in ledger.get("composition", [])
        for level in item.get("levels", [])
    )
    return scopes


def count_scope_issues(markdown: str, ledger: dict[str, Any]) -> list[str]:
    """Reject group counts larger than every recorded group; not a general parser.

    A count shared by all groups cannot exceed every group in the ledger,
    regardless of which variable the sentence refers to. Ambiguous counts,
    subgroup comparisons and other statistics remain subject to model review.
    """
    counts = [
        row["n"]
        for row in sample_size_scopes(ledger)
        if row["scope"] in {"single_group", "composition_group"}
    ]
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


def statistic_scope_issues(markdown: str, ledger: dict[str, Any]) -> list[str]:
    from .statistic_bindings import bind_statistics

    return list(
        dict.fromkeys(
            [
                *count_scope_issues(markdown, ledger),
                *bind_statistics(markdown, ledger)["issues"],
                *posthoc_scope_issues(markdown, ledger),
                *assumption_scope_issues(markdown, ledger),
            ]
        )
    )


def posthoc_scope_issues(markdown: str, ledger: dict[str, Any]) -> list[str]:
    import itertools
    import unicodedata

    from .statistic_bindings import _can_inherit_variable, _clauses, _group_mentions, _variables

    tests = [test for test in ledger.get("tests", []) if test.get("group_summaries")]
    variables = list(dict.fromkeys(test["variable"] for test in tests))
    issues = []
    fenced = False
    all_groups = list(
        dict.fromkeys(str(row["label"]) for test in tests for row in test["group_summaries"])
    )
    all_columns = list(dict.fromkeys(test["group"] for test in tests))
    for line_number, raw in enumerate(markdown.splitlines(), 1):
        if raw.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced or raw.lstrip().startswith("|"):
            continue
        context_variables: list[str] = []
        for clause in _clauses(unicodedata.normalize("NFKC", raw)):
            selected = _variables(clause, variables)
            if selected:
                context_variables = selected
            if re.search(
                r"是否|假设|如果|不意味着|不代表|不保证|不证明|不能|并非|并不是|无法确定|[?？]|"
                r"\bif\b|not all|does not mean",
                clause,
                re.I,
            ):
                continue
            if re.search(r"正态|方差齐|Shapiro|Levene|normality|homogeneity", clause, re.I):
                continue
            universal = bool(
                re.search(
                    r"都不同|均不同|均不相同|各不相同|彼此(?:均|都)?不同|"
                    r"两两.{0,20}(?:不同|显著|存在差异|有差异)|"
                    r"all.*(?:different|\bdiffer\b)|(?:every|each).*pair.*(?:different|significant)",
                    clause,
                    re.I,
                )
            ) and bool(re.search(r"不同|差异|显著|different|\bdiffer\b|significant", clause, re.I))
            plural_groups = (
                bool(
                    re.search(
                        r"各组|每组|所有组|所有物种|各物种|[三四五六七八九十两2-9]+(?:个|种)?(?:组|物种)",
                        clause,
                    )
                )
                or len(_group_mentions(clause, all_groups)) > 2
            )
            if plural_groups and re.search(
                r"(?:均|都)(?:存在|有|表现出|呈现|具有)?显著差异|(?:均|都)(?:不|无|未达统计|未达)显著",
                clause,
            ):
                universal = True
            negative = bool(
                re.search(
                    r"未达(?:到)?(?:统计)?显著|未发现(?:统计)?显著|未见(?:统计)?显著|"
                    r"没有(?:统计)?显著|无(?:统计)?显著|不显著|"
                    r"not significant(?:ly)?|no significant",
                    clause,
                    re.I,
                )
            )
            direction = re.search(
                r"高于|大于|低于|小于|higher than|greater than|lower than|less than", clause, re.I
            )
            descriptive = (
                bool(re.search(r"样本均值|描述性|sample mean", clause, re.I))
                and "显著" not in clause
            )
            equivalence = bool(
                re.search(
                    r"(?<!不)相同|(?<!不)等效|(?<!不)等同|一样|没有差异|无差异|"
                    r"\bequivalent\b|\bequal\b|no difference",
                    clause,
                    re.I,
                )
            )
            if (
                not universal
                and not negative
                and not direction
                and not equivalence
                and not re.search(r"显著|significant", clause, re.I)
            ):
                continue
            if not selected and context_variables:
                predicate = re.search(
                    r"差异|不同|显著|高于|大于|低于|小于|significant|differ|higher|lower",
                    clause,
                    re.I,
                )
                prefix = clause[: predicate.start()] if predicate else clause
                prefix = re.sub(
                    r"未发现|未达|统计|所有|全部|物种|各|每|都|[一二三四五六七八九十两]个?|\b(?:not|no|and|statistically)\b",
                    " ",
                    prefix,
                    flags=re.I,
                )
                if _can_inherit_variable(prefix, all_groups, all_columns, []):
                    selected = context_variables
            for test in tests:
                if selected and test["variable"] not in selected:
                    continue
                summaries = {str(item["label"]): item for item in test["group_summaries"]}
                names = _group_mentions(clause, list(summaries))
                pairs = (
                    list(itertools.combinations(names if len(names) >= 2 else summaries, 2))
                    if universal
                    else []
                )
                directional: list[tuple[str, str]] = []
                if direction:
                    before = _group_mentions(clause[: direction.start()], list(summaries))
                    after = _group_mentions(clause[direction.end() :], list(summaries))
                    if (
                        before
                        and not after
                        and re.search(r"其余|其他|other|remaining", clause[direction.end() :], re.I)
                    ):
                        after = [name for name in summaries if name not in before]
                    directional = [(a, b) for a in before for b in after if a != b]
                    pairs = directional
                elif not universal and len(names) == 2:
                    pairs = [(names[0], names[1])]
                if not pairs:
                    continue
                if not selected:
                    issues.append(
                        f"第 {line_number} 行：分组差异断言未明确对应的变量，不能借用其他统计结论"
                    )
                    break
                comparisons = list(test.get("posthoc", []))
                if len(summaries) == 2 and not comparisons:
                    left, right = summaries
                    comparisons = [
                        {
                            "left_group": left,
                            "right_group": right,
                            "method": test.get("method"),
                            "significant": test.get("significant"),
                            "p_value": test.get("p_value"),
                        }
                    ]
                for left, right in pairs:
                    comparison = next(
                        (
                            row
                            for row in comparisons
                            if {row["left_group"], row["right_group"]} == {left, right}
                        ),
                        None,
                    )
                    if equivalence and not descriptive:
                        issues.append(
                            f"第 {line_number} 行：{test['variable']} 的 {left}/{right} "
                            "未执行等效性检验，差异未显著不能证明两组相同或没有差异"
                        )
                        continue
                    if equivalence and descriptive:
                        a, b = summaries[left].get("mean"), summaries[right].get("mean")
                        if (
                            not isinstance(a, (int, float))
                            or not isinstance(b, (int, float))
                            or a != b
                        ):
                            issues.append(
                                f"第 {line_number} 行：{test['variable']} 的 {left}/{right} "
                                "样本均值未记录或并不相同"
                            )
                        continue
                    if descriptive:
                        significant_ok = True
                    else:
                        significant_ok = comparison is not None and comparison.get(
                            "significant"
                        ) is (not negative)
                    order_ok = True
                    if directional and not negative:
                        higher = direction is not None and direction[0].casefold() in {
                            "高于",
                            "大于",
                            "higher than",
                            "greater than",
                        }
                        a, b = summaries[left].get("mean"), summaries[right].get("mean")
                        order_ok = (
                            isinstance(a, (int, float))
                            and isinstance(b, (int, float))
                            and (a > b if higher else a < b)
                        )
                    if not significant_ok or not order_ok:
                        evidence = (
                            f"{comparison['method']}，p={comparison['p_value']}"
                            if comparison
                            else "缺少可用的两两比较"
                        )
                        issues.append(
                            f"第 {line_number} 行：{test['variable']} 的 {left}/{right} "
                            "差异结论与台账不符"
                            f"（{evidence}）；总体显著不能替代事后比较，方向也必须与组均值一致"
                        )
    return list(dict.fromkeys(issues))


def assumption_scope_issues(markdown: str, ledger: dict[str, Any]) -> list[str]:
    from .statistic_bindings import _group_mentions, _variables

    diagnostics = [item for test in ledger.get("tests", []) for item in test.get("assumptions", [])]
    variables = list(
        dict.fromkeys(item["variable"] for item in diagnostics if item.get("variable"))
    )
    groups = list(dict.fromkeys(str(item["group"]) for item in diagnostics if "group" in item))
    issues = []
    fenced = False
    for line_number, line in enumerate(markdown.splitlines(), 1):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced or line.lstrip().startswith("|"):
            continue
        if re.search(
            r"是否|如果|不能|不等于|不代表|不意味着|未自动验证|未认定|无法确认|[?？]", line
        ):
            continue
        if (
            "独立" in line
            and re.search(r"证明|确认|因此|说明", line)
            and re.search(r"正态|方差|Shapiro|Levene", line, re.I)
        ):
            issues.append(
                f"第 {line_number} 行：分布或方差检验不能证明观测独立，独立性仍依赖实验设计"
            )
        kind = (
            "normality"
            if re.search(r"正态|Shapiro|normality", line, re.I)
            else (
                "variance_homogeneity" if re.search(r"方差齐|Levene|homogene", line, re.I) else ""
            )
        )
        if not kind:
            continue
        if re.search(r"证明|确认.*成立|proved|proven", line, re.I):
            issues.append(f"第 {line_number} 行：未拒绝分布前提不等于证明前提成立")
        if re.search(r"未拒绝|not reject", line, re.I):
            expected = "not_rejected"
        elif re.search(r"拒绝|非正态|不齐|不满足|不符合", line):
            expected = "rejected"
        elif re.search(r"满足|符合|通过|成立|服从正态|呈正态", line):
            expected = "not_rejected"
        else:
            continue
        selected = _variables(line, variables)
        named_groups = _group_mentions(line, groups)
        rows = [
            row
            for row in diagnostics
            if row.get("kind") == kind
            and (not selected or row.get("variable") in selected)
            and (not named_groups or str(row.get("group")) in named_groups)
        ]
        if not rows or any(row.get("status") != expected for row in rows):
            issues.append(
                f"第 {line_number} 行：分布前提结论与已记录的 {kind} 检查不一致或不可确认"
            )
    return list(dict.fromkeys(issues))
