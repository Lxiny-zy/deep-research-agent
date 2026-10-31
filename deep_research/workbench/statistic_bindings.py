"""Bind stated statistics to scoped, immutable computed values."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any

_METRICS = {
    "mean": r"均值|平均值|平均数|平均|(?<![A-Za-z0-9_])means?(?![A-Za-z0-9_])",
    "std": r"标准差|\b(?:std|sd|standard deviation)\b",
    "median": r"中位数|中位值|\bmedian\b",
    "min": r"最小值|最小|\bmin(?:imum)?\b",
    "max": r"最大值|最大|\bmax(?:imum)?\b",
    "n": (
        r"有效总样本量|有效样本量|样本量|样本数|\bsample size\b|"
        r"(?<![A-Za-z0-9_])n(?![A-Za-z0-9_])"
    ),
    "p_value": r"p值|(?<![A-Za-z0-9_])p(?:[-_ ]?value)?(?![A-Za-z0-9_])",
    "r": r"相关系数|(?<![A-Za-z0-9_])r(?![A-Za-z0-9_])",
    "statistic": r"统计量|\bstatistic\b",
    "mean_difference": r"平均差|均值差",
    "difference_std": r"差值标准差",
    "n_pairs": r"完整配对数|配对数",
    "excluded_pairs": r"排除不完整配对",
    "df": r"自由度|\bdf\b",
    "n_total": r"有效总样本量|\bn_total\b",
    "ci": r"均值差置信区间|差值置信区间|置信区间|\b(?:confidence interval|ci)\b",
    "df_between": r"组间自由度|\bdf_between\b",
    "df_within": r"组内自由度|\bdf_within\b",
    "ci_level": r"置信水平",
    "eta_squared": r"η²|η2|\beta_squared\b",
}
_METRIC_PATTERN = re.compile(
    "|".join(
        f"(?P<{key}>{_METRICS[key]})"
        for key in [
            "ci",
            "ci_level",
            "n_total",
            "df_between",
            "df_within",
            "mean_difference",
            "difference_std",
            "n_pairs",
            "excluded_pairs",
            "eta_squared",
            "statistic",
            "std",
            "median",
            "min",
            "max",
            "n",
            "p_value",
            "r",
            "df",
            "mean",
        ]
    ),
    re.I,
)
_NUMBER = r"[-+−]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?"
_VALUE = re.compile(
    r"\s*(?:\([^)]*\))?\s*(?:分别|均|都|约|大约|为|是|is|was|are|were|of|approximately|about)*"
    r"\s*(?P<op>[=:：<>≤≥]?=?)\s*(?P<value>" + _NUMBER + r")(?![0-9_.])",
    re.I,
)
_INTERVAL = re.compile(
    r"\s*(?:为|是|=|:)?\s*\[\s*(?P<low>" + _NUMBER + r")\s*[,，]\s*(?P<high>" + _NUMBER + r")\s*\]"
)
_CONFIDENCE_LEVEL = re.compile(
    r"\s*%\s*(?:的\s*)?(?:均值差|平均差|差值|均值)?\s*(?:置信|CI\b|confidence)", re.I
)
# 指标词后面的「12 个配对差」「表 2」不是该指标的取值；计数类指标除外，那里的数字就是取值
_COUNT_NOUN = re.compile(r"\s*(?:个|对|组|行|列|项|次|名|例|条|份)")
_COUNT_METRICS = {"n", "n_total", "n_pairs", "excluded_pairs", "df", "df_between", "df_within"}
_NUMBERED_LABEL = re.compile(r"(?:表|图|第|Table|Figure|Fig\.?)\s*$", re.I)
_PAIR_REFERENCE = re.compile(r"两列|两个?变量|两项测量|二者|两者")
_NONASSERTIVE = re.compile(
    r"是否|假设|如果|不是|并非|不等于|不能|不得|[？?]|\bif\b",
    re.I,
)
_TOTAL = re.compile(
    r"总体|整体|总计|合计|合并|总样本量|\b(?:overall|total|pooled|combined)\b", re.I
)
_EACH = re.compile(r"各组|每组|各分组|分组均|所有组|\b(?:each group|all groups)\b", re.I)
_VARIABLE_ALIASES = {
    "body_mass": ["体重"],
    "mass": ["体重", "质量"],
    "weight": ["体重"],
    "bill_length": ["喙长"],
    "bill_depth": ["喙深"],
    "flipper_length": ["鳍长", "鳍肢长度"],
    "length": ["长度"],
    "width": ["宽度"],
    "height": ["身高", "高度"],
}


def statistic_facts(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(scope: str, row: dict[str, Any], keys: list[str], **dimensions: Any) -> None:
        for key in keys:
            if key not in row:
                continue
            fact = {"scope": scope, **dimensions, "statistic": key, "value": row[key]}
            fact["id"] = hashlib.sha256(
                json.dumps(fact, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()[:24]
            if fact["id"] not in seen:
                seen.add(fact["id"])
                facts.append(fact)

    add("dataset", {"n": ledger.get("rows")}, ["n"], variable="")
    descriptive = ["n", "mean", "std", "median", "min", "max"]
    for row in ledger.get("describe", []):
        add("variable", row, descriptive, variable=row["variable"])
    for test in ledger.get("tests", []):
        dimensions = {"variable": test["variable"], "group_column": test["group"]}
        for row in test.get("group_summaries", []):
            add("group", row, descriptive, **dimensions, group=str(row["label"]))
        dimensions["variables"] = (
            [test["left"], test["right"]] if test.get("paired") else [test["variable"]]
        )
        dimensions["groups"] = [str(row["label"]) for row in test.get("group_summaries", [])]
        add(
            "comparison",
            test,
            [
                "p_value",
                "statistic",
                "n_pairs",
                "excluded_pairs",
                "mean_difference",
                "difference_std",
                "df",
                "df_between",
                "df_within",
                "n_total",
                "ci_low",
                "ci_high",
                "eta_squared",
            ],
            **dimensions,
            method=test.get("method", ""),
            robust=False,
        )
        if "ci_low" in test and "ci_high" in test:
            # This analyzer's recorded paired interval is the fixed 95% interval
            # printed by AnalysisResult.facts(); it has no configurable level.
            add(
                "comparison",
                {"ci_level": 95},
                ["ci_level"],
                **dimensions,
                method=test.get("method", ""),
                robust=False,
            )
        if "n_total" in test or "n_pairs" in test:
            add(
                "comparison",
                {"n": test.get("n_pairs", test.get("n_total"))},
                ["n"],
                **dimensions,
                method=test.get("method", ""),
                robust=False,
            )
        if test.get("robust_method"):
            add(
                "comparison",
                {"p_value": test.get("robust_p")},
                ["p_value"],
                **dimensions,
                method=test["robust_method"],
                robust=True,
            )
        for diagnostic in test.get("assumptions", []):
            diagnostic_dimensions = {
                **dimensions,
                "groups": [str(diagnostic["group"])]
                if "group" in diagnostic
                else dimensions["groups"],
            }
            values = {
                key: diagnostic[key] for key in ("statistic", "p_value", "n") if key in diagnostic
            }
            if diagnostic.get("kind") == "variance_homogeneity" and "n" in diagnostic:
                values["n_total"] = diagnostic["n"]
            add(
                "comparison",
                values,
                list(values),
                **diagnostic_dimensions,
                method=diagnostic.get("method", ""),
                diagnostic=True,
                robust=False,
            )
        for pair in test.get("posthoc", []):
            pair_dimensions = {
                **dimensions,
                "groups": [str(pair["left_group"]), str(pair["right_group"])],
                "left_group": str(pair["left_group"]),
                "right_group": str(pair["right_group"]),
            }
            values = {**pair, "ci_level": pair.get("confidence_level", 0.95) * 100}
            add(
                "comparison",
                values,
                ["mean_difference", "p_value", "ci_low", "ci_high", "ci_level"],
                **pair_dimensions,
                method=pair["method"],
                robust=False,
                posthoc=True,
            )
    for row in ledger.get("correlations", []):
        grouping = {key: row[key] for key in ("group_column", "group") if key in row}
        add(
            "correlation",
            row,
            ["n", "r", "p_value"],
            variable=row["a"],
            other_variable=row["b"],
            method="Pearson",
            **grouping,
        )
    for row in ledger.get("composition", []):
        add("composition", row, ["n"], variable=row["column"], group_column=row["column"])
        for level in row.get("levels", []):
            add(
                "composition_group",
                level,
                ["n"],
                variable=row["column"],
                group_column=row["column"],
                group=str(level["label"]),
            )
    return facts


def _contains(text: str, value: str) -> bool:
    return bool(
        value and re.search(rf"(?<![A-Za-z0-9_]){re.escape(value)}(?![A-Za-z0-9_])", text, re.I)
    )


def _position(text: str, value: str) -> int | None:
    if not value:
        return None
    found = re.search(rf"(?<![A-Za-z0-9_]){re.escape(value)}(?![A-Za-z0-9_])", text, re.I)
    return found.start() if found else None


def _variables(text: str, names: list[str]) -> list[str]:
    """按文中提及顺序返回，「A 与 B 分别为 x、y」靠这个顺序逐一对应。"""
    located = [(at, name) for name in names if (at := _position(text, name)) is not None]
    if not located:
        for name in names:
            leaf = name.rsplit(".", 1)[-1]
            stem = re.sub(r"_(?:mm|cm|kg|g|mg|s|ms)$", "", leaf, flags=re.I)
            aliases = [
                leaf, stem, stem.replace("_", " "), *_VARIABLE_ALIASES.get(stem.casefold(), [])
            ]
            hits = [at for alias in aliases if (at := _position(text, alias)) is not None]
            if hits:
                located.append((min(hits), name))
    return [name for _, name in sorted(located, key=lambda item: item[0])]


def _group_mentions(text: str, labels: list[str]) -> list[str]:
    located = []
    for label in labels:
        if re.fullmatch(_NUMBER, label):
            pattern = (
                rf"(?:组|group)\s*=?\s*{re.escape(label)}(?!\d)|"
                rf"(?<!\d){re.escape(label)}\s*组|(?<!\d){re.escape(label)}(?=\s*:)"
            )
        else:
            pattern = rf"(?<![A-Za-z0-9_]){re.escape(label)}(?![A-Za-z0-9_])"
        match = re.search(pattern, text, re.I)
        if match:
            located.append((match.start(), label))
    return [label for _, label in sorted(located)]


def _can_inherit_variable(
    prefix: str, labels: list[str], columns: list[str], methods: list[str]
) -> bool:
    remainder = prefix
    for name in sorted([*labels, *columns, *methods], key=len, reverse=True):
        remainder = re.sub(
            rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])",
            " ",
            remainder,
            flags=re.I,
        )
    remainder = _METRIC_PATTERN.sub(" ", remainder)
    remainder = re.sub(
        r"分组|各组|每组|组|总体|整体|有效|样本效应量|稳健性复核|差值方向|方差分析|"
        r"配对差值|配对差异|配对检验|配对|相关性|相关|显著性|检验|的|在|与|和|"
        r"局限|小结|结论|结果|前提检查|前提|描述统计|描述性统计|说明|备注|"
        r"\b(?:for|in|the|of|group|each|all|overall|total|pooled|combined)\b",
        " ",
        remainder,
        flags=re.I,
    )
    return not re.search(r"[A-Za-z\u4e00-\u9fff]", remainder)


def _matches(
    value: str, expected: Any, operator: str, metric: str, *, percent: bool = False
) -> bool:
    try:
        actual = Decimal(value.replace(",", "").replace("−", "-"))
        target = Decimal(str(expected))
        if not actual.is_finite() or not target.is_finite():
            return False
        exponent = actual.as_tuple().exponent
        if not isinstance(exponent, int):
            return False
        if percent and metric in {"n", "n_total", "n_pairs", "excluded_pairs", "df"}:
            return False
        if percent and metric in {"p_value", "r"}:
            actual /= 100
            exponent -= 2
        if operator in {"<", "<=", "≤", ">", ">=", "≥"}:
            return {
                "<": target < actual,
                "<=": target <= actual,
                "≤": target <= actual,
                ">": target > actual,
                ">=": target >= actual,
                "≥": target >= actual,
            }[operator]
        if metric == "p_value" and actual == 0:
            return target == 0
        if metric in {"p_value", "r"} and exponent >= 0:
            return actual == target
        tolerance = (
            Decimal(0)
            if metric in {"n", "n_total", "n_pairs", "excluded_pairs", "df_between"}
            else abs(Decimal(1).scaleb(exponent)) / 2
        )
        return abs(actual - target) <= tolerance
    except (InvalidOperation, ValueError):
        return False


def _clauses(line: str) -> list[str]:
    start = depth = 0
    result = []
    for index, character in enumerate(line):
        if character == "[":
            depth += 1
        elif character == "]":
            depth = max(0, depth - 1)
        separator = character in "。；;，" or (
            character == "," and not re.match(r"\d{3}(?:\D|$)", line[index + 1 :])
        )
        if separator and depth == 0:
            result.append(line[start:index])
            start = index + 1
    return [*result, line[start:]]


def bind_statistics(markdown: str, ledger: dict[str, Any]) -> dict[str, Any]:
    facts = statistic_facts(ledger)
    by_statistic: dict[tuple[str, str], list[dict[str, Any]]] = {}
    by_variable: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for fact in facts:
        by_statistic.setdefault((fact["scope"], fact["statistic"]), []).append(fact)
        by_variable.setdefault((fact["scope"], fact["statistic"], fact["variable"]), []).append(
            fact
        )
    variables = list(dict.fromkeys(row["variable"] for row in facts if row["scope"] == "variable"))
    labels = list(dict.fromkeys(row["group"] for row in facts if "group" in row))
    methods = list(dict.fromkeys(row["method"] for row in facts if row.get("method")))
    group_columns = list(
        dict.fromkeys(row["group_column"] for row in facts if "group_column" in row)
    )
    composition_columns = [row["column"] for row in ledger.get("composition", [])]
    paired_tests = [
        [test["left"], test["right"]] for test in ledger.get("tests", []) if test.get("paired")
    ]
    correlations = ledger.get("correlations", [])
    only_correlation = (
        [correlations[0]["a"], correlations[0]["b"]] if len(correlations) == 1 else []
    )
    claims: list[dict[str, Any]] = []
    issues: list[str] = []
    current_variables: list[str] = []
    current_groups: list[str] = []
    current_method = ""
    current_group_column = ""
    section = ""
    composition_column = ""
    previous_metric = ""
    fenced = False
    empty_heading: dict[str, Any] = {
        "variables": [],
        "groups": [],
        "metric": "",
        "method": "",
        "group_column": "",
        "section": "",
    }
    headings: list[tuple[int, dict[str, Any]]] = []
    for line_number, raw in enumerate(markdown.splitlines(), 1):
        line = unicodedata.normalize("NFKC", raw).replace("**", "").replace("`", "")
        if raw.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced or line.lstrip().startswith("|"):
            continue  # Bound data tables have a separate code-rendered table validator.
        if not line.strip() or line.lstrip().startswith("#"):
            if line.lstrip().startswith("#"):
                level = len(line.lstrip()) - len(line.lstrip().lstrip("#"))
                while headings and headings[-1][0] >= level:
                    headings.pop()
                heading = dict(headings[-1][1] if headings else empty_heading)
                named_variables = _variables(line, variables)
                named_groups = _group_mentions(line, labels)
                named_columns = [column for column in group_columns if _contains(line, column)]
                named_methods = [method for method in methods if _contains(line, method)]
                metrics = [match.lastgroup for match in _METRIC_PATTERN.finditer(line)]
                if named_variables:
                    heading["variables"] = named_variables
                if named_groups:
                    heading["groups"] = named_groups
                if named_columns:
                    heading["group_column"] = named_columns[-1]
                if named_methods:
                    heading["method"] = named_methods[-1]
                if metrics:
                    heading["metric"] = metrics[0] if len(metrics) == 1 else "ambiguous"
                if "构成" in line:
                    heading["section"] = "composition"
                elif "相关性" in line:
                    heading["section"] = "correlation"
                headings.append((level, heading))
            heading = headings[-1][1] if headings else empty_heading
            current_variables, current_groups = list(heading["variables"]), list(heading["groups"])
            previous_metric, current_method = heading["metric"], heading["method"]
            current_group_column, section = heading["group_column"], heading["section"]
            continue
        if section == "composition":
            named = [column for column in composition_columns if _contains(line, column)]
            if named:
                composition_column = named[0]
                current_groups = []
        line_methods = [method for method in methods if _contains(line, method)]
        if len(line_methods) == 1:
            current_method = line_methods[0]
        if "配对" in line or section == "correlation":
            line_variables = _variables(line, variables)
            if line_variables:
                current_variables = line_variables
            elif "配对" in line and len(paired_tests) == 1:
                current_variables = list(paired_tests[0])
        # 背景列不是测量变量：只在这一行讲它的时候，n 取它自己的构成计数
        background_column = ""
        following_correlation = False
        for clause in _clauses(line):
            if _NONASSERTIVE.search(clause):
                continue
            explicit_variables = _variables(clause, variables)
            group_names = _group_mentions(clause, labels)
            explicit_methods = [method for method in methods if _contains(clause, method)]
            explicit_group_columns = [
                column for column in group_columns if _contains(clause, column)
            ]
            named_background = [
                column for column in composition_columns if _contains(clause, column)
            ]
            if named_background and not explicit_variables:
                background_column = named_background[0]
            if explicit_variables:
                background_column = ""
                current_variables = explicit_variables
                if not group_names:
                    current_groups = []
            if explicit_methods:
                following_correlation = False
            if explicit_group_columns:
                current_group_column = explicit_group_columns[-1]
            if group_names:
                current_groups = group_names
            if explicit_methods:
                current_method = explicit_methods[-1]
            total = bool(_TOTAL.search(clause))
            if total:
                current_groups = []
            each = bool(_EACH.search(clause)) and not total
            matches = []
            unresolved = []
            for match in _METRIC_PATTERN.finditer(clause):
                if match.lastgroup == "ci":
                    interval = _INTERVAL.match(clause, match.end())
                    if interval:
                        confidence_match = re.search(
                            r"(" + _NUMBER + r")\s*%\s*$", clause[: match.start()]
                        )
                        if confidence_match:
                            level_value = _VALUE.match(confidence_match[1])
                            if level_value:
                                matches.append(("ci_level", match.start(), level_value))
                        for metric, group in (("ci_low", "low"), ("ci_high", "high")):
                            value_match = _VALUE.match(interval[group])
                            if value_match:
                                matches.append((metric, match.start(), value_match))
                        continue
                value_match = _VALUE.match(clause, match.end())
                if value_match:
                    matches.append((match.lastgroup or "", match.start(), value_match))
                else:
                    unresolved.append((match.start(), match.end(), match.lastgroup or "statistic"))
            if "自由度" in clause:
                for label, metric in (("组间", "df_between"), ("组内", "df_within")):
                    for item in re.finditer(label + r"\s*(?=[=:])", clause):
                        value_match = _VALUE.match(clause, item.end())
                        if value_match:
                            matches.append((metric, item.start(), value_match))
            if not matches and group_names and previous_metric:
                implicit = re.search(r"(?:为|是|=|:|is|was)\s*(?=" + _NUMBER + ")", clause, re.I)
                if implicit:
                    value_match = _VALUE.match(clause, implicit.start())
                    if value_match:
                        matches.append((previous_metric, implicit.start(), value_match))
            previous_end = 0
            for metric, offset, match in matches:
                previous_metric = metric
                prefix = clause[previous_end:offset]
                local_variables = _variables(prefix, variables)
                local_groups = _group_mentions(prefix, labels)
                if local_variables:
                    current_variables = local_variables
                if local_groups:
                    current_groups = local_groups
                claim_each = each and not local_groups
                selected_variables = current_variables
                inheritable = bool(local_variables) or _can_inherit_variable(
                    prefix, labels, group_columns, methods
                )
                if not inheritable:
                    selected_variables = []
                    current_variables = []
                if (
                    metric == "r"
                    and not selected_variables
                    and only_correlation
                    and (inheritable or _PAIR_REFERENCE.search(prefix))
                ):
                    # 全表只有一条相关记录时，「两列的相关结果 r=…」不会指向别的变量对；
                    # 点名了台账外的变量（如 height）则不能借用这条记录
                    selected_variables = current_variables = list(only_correlation)
                if metric == "std" and re.search(r"差值|配对差", prefix):
                    # 「配对差值（右 − 左）的标准差」是检验的差值标准差，不是各变量的标准差
                    metric = "difference_std"
                if not metric.startswith("ci_"):
                    previous_end = match.end()
                scope = "variable"
                if metric in {
                    "p_value",
                    "statistic",
                    "eta_squared",
                    "df",
                    "n_pairs",
                    "excluded_pairs",
                    "mean_difference",
                    "difference_std",
                    "n_total",
                    "df_between",
                    "df_within",
                    "ci_low",
                    "ci_high",
                    "ci_level",
                }:
                    scope = "comparison"
                paired = "配对" in current_method
                # 只点名一个变量的 n 是该变量自己的有效样本量，即使同一行后面提到配对检验
                if metric == "n" and paired and len(selected_variables) != 1:
                    scope = "comparison"
                if metric == "r" or (
                    len(selected_variables) == 2
                    and metric in {"n", "p_value"}
                    and (
                        following_correlation or not current_method or current_method == "Pearson"
                    )
                ):
                    scope = "correlation"
                elif scope == "variable" and (current_groups or claim_each):
                    scope = "group"
                # 「r=0.98（p=…，n=12）」括号里的 p 与 n 仍属于这条相关，直到换了方法或指标
                following_correlation = metric == "r" or (
                    following_correlation and metric in {"n", "p_value"}
                )
                if (
                    not local_groups
                    and not claim_each
                    and not total
                    and re.search(r"组|\bgroups?\b", prefix, re.I)
                    and scope in {"variable", "group"}
                ):
                    scope = "unresolved_group"
                if section == "composition" and metric == "n" and composition_column:
                    scope = "composition_group" if current_groups else "composition"
                    selected_variables = [composition_column]
                elif metric == "n" and background_column:
                    scope = "composition_group" if current_groups else "composition"
                    selected_variables = [background_column]
                if metric == "n" and re.match(r"\s*行", clause[match.end() :]):
                    scope, selected_variables = "dataset", [""]
                if scope == "comparison" and not selected_variables and current_method:
                    owners = {
                        tuple(sorted(row.get("variables", [row["variable"]])))
                        for row in by_statistic.get((scope, metric), [])
                        if row.get("method") == current_method
                    }
                    if len(owners) == 1:
                        # 台账里这种方法只有一项检验：「配对 t 检验记录中排除配对为 0」只能指它
                        selected_variables = list(next(iter(owners)))
                candidates = by_statistic.get((scope, metric), [])
                if not selected_variables and scope != "dataset":
                    candidates = []
                if len(selected_variables) == 1 and scope not in {"comparison", "correlation"}:
                    candidates = by_variable.get((scope, metric, selected_variables[0]), [])
                if selected_variables:
                    if scope == "correlation":
                        candidates = [
                            row
                            for row in candidates
                            if {row["variable"], row["other_variable"]} == set(selected_variables)
                        ]
                    elif scope == "comparison":
                        candidates = [
                            row
                            for row in candidates
                            if set(row.get("variables", [row["variable"]]))
                            == set(selected_variables)
                        ]
                    else:
                        candidates = [
                            row for row in candidates if row["variable"] in selected_variables
                        ]
                if current_groups and not claim_each and scope in {"group", "composition_group"}:
                    candidates = [row for row in candidates if row.get("group") in current_groups]
                if (
                    current_group_column
                    and scope in {"group", "comparison", "composition_group", "correlation"}
                    and (scope != "correlation" or current_groups)
                ):
                    candidates = [
                        row for row in candidates if row.get("group_column") == current_group_column
                    ]
                if scope == "correlation":
                    candidates = [
                        row
                        for row in candidates
                        if (
                            row.get("group") in current_groups
                            if current_groups
                            else "group" not in row
                        )
                    ]
                    if explicit_group_columns and not current_groups and not total:
                        candidates = []
                if scope == "comparison":
                    if current_groups and metric in {
                        "p_value",
                        "statistic",
                        "mean_difference",
                        "ci_low",
                        "ci_high",
                        "ci_level",
                    }:
                        candidates = [
                            row
                            for row in candidates
                            if set(row.get("groups", [])) == set(current_groups)
                        ]
                    if current_method and (
                        line_methods or metric in {"p_value", "statistic", "n", "n_pairs", "df"}
                    ):
                        candidates = [
                            row for row in candidates if row.get("method") == current_method
                        ]
                    else:
                        candidates = [
                            row
                            for row in candidates
                            if not row.get("robust") and not row.get("diagnostic")
                        ]
                values = [match["value"]]
                percent = (
                    bool(re.match(r"\s*%", clause[match.end() :]))
                    if not metric.startswith("ci_")
                    else False
                )
                expression = (
                    bool(re.match(r"\s*[/×*+−-]\s*\d", clause[match.end() :]))
                    if not metric.startswith("ci_")
                    else False
                )
                each_group = len(current_groups) > 1
                each_variable = not each_group and len(selected_variables) > 1
                if "分别" in clause and (each_group or each_variable):
                    tail = re.match(
                        r"(?:\s*(?:[、,与和及]|以及)\s*" + _NUMBER + ")+", clause[match.end() :]
                    )
                    if tail:
                        values.extend(re.findall(_NUMBER, tail[0]))
                for index, value in enumerate(values):
                    chosen = candidates
                    if len(values) > 1 and each_group and index < len(current_groups):
                        chosen = [
                            row for row in chosen if row.get("group") == current_groups[index]
                        ]
                    elif len(values) > 1 and each_variable and index < len(selected_variables):
                        chosen = [
                            row for row in chosen if row["variable"] == selected_variables[index]
                        ]
                    claim = {
                        "line": line_number,
                        "statistic": metric,
                        "value": value,
                        "variables": selected_variables,
                        "groups": current_groups,
                        "fact_ids": [row["id"] for row in chosen],
                        "scope": scope,
                        "unit": "%" if percent else "",
                    }
                    claims.append(claim)
                    if expression:
                        issues.append(
                            f"第 {line_number} 行：统计值包含未绑定的运算，请明确写出对应台账值"
                        )
                    elif not chosen:
                        issues.append(
                            f"第 {line_number} 行：{metric}={value} 缺少对应范围的统计台账"
                        )
                    elif any(
                        not _matches(value, row["value"], match["op"], metric, percent=percent)
                        for row in chosen
                    ):
                        target = "；".join(
                            f"{row.get('variable', '')}/{row.get('group', '总体')}/"
                            f"{metric}={row['value']}"
                            for row in chosen[:6]
                        )
                        issues.append(
                            f"第 {line_number} 行：{metric}={value} 与所述变量/分组的台账不符"
                            f"（{target}）"
                        )
            for start, end, metric in unresolved:
                rest = clause[end:]
                # 方法说明里「95% 均值差置信区间」的 95 是置信水平，不是这个统计量的取值
                number = next(
                    (
                        item
                        for item in re.finditer(_NUMBER, rest)
                        if not _CONFIDENCE_LEVEL.match(rest, item.end())
                        and not _NUMBERED_LABEL.search(rest[: item.start()])
                        and not (
                            metric not in _COUNT_METRICS and _COUNT_NOUN.match(rest, item.end())
                        )
                    ),
                    None,
                )
                if number is not None and not any(
                    start <= offset <= end + number.start() for _, offset, _ in matches
                ):
                    issues.append(
                        f"第 {line_number} 行：{metric} 的数值表述无法明确绑定，"
                        "请写清变量、分组与统计量"
                    )
    return {"claims": claims, "issues": list(dict.fromkeys(issues))}
