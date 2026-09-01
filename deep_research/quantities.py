"""数值与单位的确定性校验。

## 为什么科学场景必须有这一层

现有的逐字证据校验回答的是"这句话在不在原文里"。但科学论断的核心不是句子，是
**"在什么条件下，某指标 = 某数值"**。而这两个问题的失败模式完全不同：

* 模型可以逐字引对一句话，同时把句子里的数字抄错一位（34.26 → 3.426）；
* 也可以引对一句提到 SSIM 的话，然后把那个数字当成 PSNR 报出来；
* 也可以把"超过 35 dB"变成"等于 35 dB"——比较符被吞掉，结论就从下界变成点值。

逐字匹配对这三种全部无感，因为被改的是**数值与单位的对应关系**，不是措辞。

所以本模块做一件确定性的事：把模型声明的 ``Quantity`` 拿去和 ``evidence_quote``
里真实出现的"数值+单位"比对。**纯代码、无 LLM**，与逐字校验同源——判定器被操控
也不会让一个原文里不存在的数字进入报告。

## 容差怎么定

按论断自己的有效位数定，而不是拍一个固定 epsilon：声明 ``38.36`` 就允许
±0.005（末位的一半），声明 ``38.4`` 就允许 ±0.05，声明 ``38`` 就允许 ±0.5。

这条规则的好处是它**恰好把"正确的四舍五入"与"抄错"分开**：原文 ``38.36`` 时，
声明 ``38.4`` 通过（这是它在一位小数下的正确写法），声明 ``38.3`` 不通过（四舍五入
应得 38.4）。既不会因为报告降低了精度就误判为编造，也不会放过真的抄错。

## 单位换算的边界

只换算**同一物理量内部**确定无争议的进制（nm/μm/mm、M/G 量级后缀、ms/s）。
刻意**不做** ``%`` ↔ 无单位的换算：SSIM 既有报 ``0.948`` 也有报 ``94.8%`` 的，
但也有本来就是百分数的指标，自动换算会把"单位不符"这个真实信号抹掉。宁可判不
一致让人去看，也不要猜对了却掩盖了口径差异。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# 单位 → (归一化单位, 相对归一化单位的倍数)。
# 只收录本领域真实出现的单位；未知单位保持原样参与"字面相等"比较，不做换算。
_UNIT_SCALE: dict[str, tuple[str, float]] = {
    # 长度（光谱波长、像元尺寸）
    "nm": ("nm", 1.0),
    "μm": ("nm", 1_000.0),
    "µm": ("nm", 1_000.0),  # U+00B5 MICRO SIGN，与上面的 U+03BC 是不同码位
    "um": ("nm", 1_000.0),
    "mm": ("nm", 1_000_000.0),
    "cm": ("nm", 10_000_000.0),
    # 计算量与规模
    "k": ("", 1_000.0),
    "m": ("", 1_000_000.0),
    "g": ("", 1_000_000_000.0),
    "b": ("", 1_000_000_000.0),
    "flops": ("flops", 1.0),
    "kflops": ("flops", 1e3),
    "mflops": ("flops", 1e6),
    "gflops": ("flops", 1e9),
    "tflops": ("flops", 1e12),
    # 时间
    "ms": ("s", 0.001),
    "s": ("s", 1.0),
    "sec": ("s", 1.0),
    "min": ("s", 60.0),
    "h": ("s", 3600.0),
    # 无需换算但需要归一化写法的
    "db": ("db", 1.0),
    "%": ("%", 1.0),
    "°": ("deg", 1.0),
    "deg": ("deg", 1.0),
    "degree": ("deg", 1.0),
    "degrees": ("deg", 1.0),
}

# 按长度倒序拼进正则：先匹配 gflops 再匹配 g，否则 "GFLOPs" 会被切成 "G" + "FLOPs"。
_UNIT_PATTERN = "|".join(re.escape(unit) for unit in sorted(_UNIT_SCALE, key=len, reverse=True))

# 数值：可带千分位逗号、小数、科学计数法与符号。
_NUMBER = r"[-+]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"

# 数值 + 可选单位。单位后要求非字母，避免 "35 mask" 里的 m 被当成兆。
_MEASUREMENT_RE = re.compile(
    rf"(?P<number>{_NUMBER})\s*(?P<unit>{_UNIT_PATTERN})?(?![A-Za-z])",
    re.IGNORECASE,
)

# 比较符：把"超过 35 dB"与"等于 35 dB"区分开。前者是下界，后者是点值，
# 当成同一件事会让报告给出比证据更强的结论。
_COMPARATORS: tuple[tuple[str, str], ...] = (
    (">=", "至少|不低于|不少于|大于等于|≥|>="),
    ("<=", "至多|不高于|不超过|小于等于|≤|<="),
    (">", "超过|高于|多于|大于|>"),
    ("<", "低于|少于|小于|<"),
)


# Explicit relations used by the deterministic quantity gate.  The public
# ``detect_comparator`` helper keeps its historical vocabulary; these patterns
# additionally cover common English and mathematical spellings.
_RELATION_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        ">=",
        r"(?:>=|≥|at\s+least|no\s+less\s+than|not\s+less\s+than|"
        r"至少|不低于|不少于|大于等于)",
    ),
    (
        "<=",
        r"(?:<=|≤|at\s+most|no\s+more\s+than|not\s+more\s+than|"
        r"至多|不高于|不超过|小于等于)",
    ),
    (
        ">",
        r"(?:>|greater\s+than|more\s+than|higher\s+than|above|"
        r"超过|高于|多于|大于)",
    ),
    (
        "<",
        r"(?:<|less\s+than|lower\s+than|below|低于|少于|小于)",
    ),
    ("=", r"(?:=|＝|equals?|equal\s+to|reaches?|achieves?|reported\s+as)"),
)


@dataclass(frozen=True)
class Measurement:
    """从文本里抽出的一个"数值+单位"。"""

    value: float
    unit: str  # 归一化后的单位（"" 表示无单位）
    raw: str  # 原文片段，便于把失败讲清楚

    def __str__(self) -> str:  # pragma: no cover - 仅用于错误信息
        return self.raw


def normalize_unit(unit: str) -> tuple[str, float]:
    """把单位写法归一化成 (归一化单位, 倍数)。未知单位原样返回、倍数 1。"""
    key = unit.strip().casefold()
    if not key:
        return ("", 1.0)
    return _UNIT_SCALE.get(key, (key, 1.0))


def parse_measurements(text: str) -> list[Measurement]:
    """抽出文本里所有"数值+单位"。

    带千分位的数字先去掉逗号再转 float——``44,250`` 与 ``44250`` 是同一个数。
    """
    out: list[Measurement] = []
    for match in _MEASUREMENT_RE.finditer(text or ""):
        raw_number = match.group("number")
        try:
            value = float(raw_number.replace(",", ""))
        except ValueError:  # pragma: no cover - 正则已保证可转换
            continue
        if not math.isfinite(value):
            continue
        unit, scale = normalize_unit(match.group("unit") or "")
        out.append(Measurement(value=value * scale, unit=unit, raw=match.group(0).strip()))
    return out


def _metric_aliases(metric: str) -> tuple[str, ...]:
    normalized = re.sub(r"[^a-z0-9]+", " ", (metric or "").casefold()).strip()
    aliases = {
        "peak signal to noise ratio": ("psnr", "peak signal to noise ratio"),
        "structural similarity": ("ssim", "structural similarity"),
        "spectral angle mapper": ("sam", "spectral angle mapper"),
        "parameter count": ("parameters", "parameter", "params"),
        "model parameters": ("parameters", "parameter", "params"),
        "inference time": ("inference time", "latency", "runtime"),
    }
    return aliases.get(normalized, (normalized,)) if normalized else ()


_KNOWN_METRIC_ALIASES = (
    "psnr",
    "ssim",
    "sam",
    "parameter",
    "parameters",
    "flops",
    "latency",
    "runtime",
    "inference time",
)


def _metric_context_supported(
    metric: str,
    value: float,
    unit: str,
    rendered: str,
    evidence: str,
) -> bool:
    """Bind a matching number to the nearest metric label when labels exist."""

    aliases = _metric_aliases(metric)
    if not aliases:
        return True
    labels: list[tuple[int, str]] = []
    lowered = evidence.casefold()
    for token in _KNOWN_METRIC_ALIASES:
        labels.extend(
            (match.start(), token)
            for match in re.finditer(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", lowered)
        )
    if not labels:
        # A quote containing only a table cell (``38.36 dB``) has no metric
        # label to bind; preserve the existing permissive behaviour there.
        return True
    canonical_unit, scale = normalize_unit(unit)
    target = value * scale
    tolerance = tolerance_for(rendered or repr(value), target)
    candidates: list[int] = []
    for match in _MEASUREMENT_RE.finditer(evidence):
        parsed = parse_measurements(match.group(0))
        if not parsed:
            continue
        item = parsed[0]
        if item.unit == canonical_unit and abs(item.value - target) <= tolerance:
            candidates.append(match.start())
    if not candidates:
        return True
    number_start = min(
        candidates, key=lambda position: min(abs(position - label) for label, _ in labels)
    )
    nearest_label = min(
        labels,
        key=lambda pair: (abs(pair[0] - number_start), pair[0] < number_start),
    )[1]
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", nearest_label)
        for alias in aliases
    )


def detect_comparator(text: str) -> str:
    """识别比较符。命中多个时取最先出现的那个。"""
    lowered = text or ""
    best: tuple[int, str] | None = None
    for symbol, alternatives in _COMPARATORS:
        match = re.search(alternatives, lowered, re.IGNORECASE)
        if match is None:
            continue
        if best is None or match.start() < best[0]:
            best = (match.start(), symbol)
    return best[1] if best else ""


def comparison_supported(
    comparator: str,
    evidence: str,
    *,
    value: float | None = None,
    unit: str = "",
    rendered: str = "",
    metric: str = "",
) -> tuple[bool, str]:
    """Check that a declared comparison relation is present in the quote.

    A strict bound must not be silently downgraded to a point value.  The
    function is deliberately conservative when the quote omits a relation;
    qualitative and legacy quantities pass by leaving ``comparator`` empty.
    """

    if not comparator:
        return (True, "comparator_not_declared")
    detected = ""
    bound_value = value if value is not None and math.isfinite(value) else None
    quantity_is_bound = bound_value is not None
    if bound_value is not None:
        detected = _relation_for_quantity(
            value=bound_value,
            unit=unit,
            rendered=rendered,
            metric=metric,
            evidence=evidence,
        )
    # Once a concrete quantity is supplied, a relation from another metric or
    # clause must never satisfy this claim.  The global fallback remains only
    # for legacy callers that ask about a relation without a target value.
    if not detected and quantity_is_bound:
        return (False, "comparator_not_in_evidence")
    if not detected:
        detected = detect_comparator(evidence)
    if not detected:
        lowered = (evidence or "").casefold()
        for relation, pattern in _RELATION_PATTERNS:
            if re.search(pattern, lowered, re.IGNORECASE):
                detected = relation
                break
    if not detected:
        return (False, "comparator_not_in_evidence")
    if comparator == detected:
        return (True, "comparator_found_in_evidence")
    # A strict bound implies its inclusive counterpart, but not vice versa.
    if comparator == ">=" and detected == ">":
        return (True, "comparator_found_in_evidence")
    if comparator == "<=" and detected == "<":
        return (True, "comparator_found_in_evidence")
    return (False, f"comparator_mismatch: evidence {detected}, claim {comparator}")


def _relation_for_quantity(
    *,
    value: float,
    unit: str,
    rendered: str,
    metric: str,
    evidence: str,
) -> str:
    canonical_unit, scale = normalize_unit(unit)
    target = value * scale
    tolerance = tolerance_for(rendered or repr(value), target)
    candidates: list[tuple[int, int]] = []
    for match in _MEASUREMENT_RE.finditer(evidence or ""):
        parsed = parse_measurements(match.group(0))
        if not parsed:
            continue
        measurement = parsed[0]
        if measurement.unit == canonical_unit and abs(measurement.value - target) <= tolerance:
            candidates.append((match.start(), match.end()))
    if not candidates:
        return ""
    if metric:
        aliases = _metric_aliases(metric)
        scored: list[tuple[int, tuple[int, int]]] = []
        for candidate in candidates:
            start, _ = candidate
            score = 0
            window = evidence[max(0, start - 64) : start + 64].casefold()
            if any(
                re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", window)
                for alias in aliases
            ):
                score = 1
            scored.append((score, candidate))
        best_score = max(score for score, _ in scored)
        candidates = [candidate for score, candidate in scored if score == best_score]
    start, end = candidates[0]
    left = evidence[max(0, start - 64) : start]
    right = evidence[end : min(len(evidence), end + 48)]
    # Commas/colons and their full-width variants commonly separate metric
    # clauses in paper tables.  Treating them as boundaries prevents a
    # relation from the preceding metric from being borrowed by this one.
    left = re.split(r"[,，;；:：.!?。！？\n]", left)[-1]
    right = re.split(r"[,，;；:：.!?。！？\n]", right)[0]
    matches: list[tuple[int, str]] = []
    for relation, pattern in _RELATION_PATTERNS:
        matches.extend(
            (match.start() - len(left), relation)
            for match in re.finditer(pattern, left, re.IGNORECASE)
        )
        matches.extend(
            (match.start(), relation) for match in re.finditer(pattern, right, re.IGNORECASE)
        )
    return min(matches, key=lambda pair: abs(pair[0]))[1] if matches else ""


def tolerance_for(rendered: str, value: float) -> float:
    """按论断自身的有效位数给容差：末位的一半。

    ``38.36`` → 0.005，``38`` → 0.5。这样不同精度的两个断言不会被当成同一个。
    对科学计数法与极大数额外叠加一个相对项，吸收浮点表示误差。
    """
    text = (rendered or "").strip().replace(",", "")
    decimals = 0
    if "e" in text.casefold():
        # 科学计数法没有直观的"末位"，退回相对容差
        return max(abs(value) * 1e-9, 1e-12)
    if "." in text:
        decimals = len(text.split(".", 1)[1])
    absolute = 0.5 * (10.0**-decimals)
    return max(absolute, abs(value) * 1e-9)


def measurement_supported(
    *,
    value: float,
    unit: str,
    rendered: str,
    evidence: str,
    metric: str = "",
) -> tuple[bool, str]:
    """声明的数值+单位是否真的出现在证据原文里。

    返回 ``(是否支持, 原因)``。原因是给审计与报告用的说明，不是给模型看的。
    """
    if not math.isfinite(value):
        return (False, "quantity_value_not_finite")

    canonical_unit, scale = normalize_unit(unit)
    target = value * scale
    tolerance = tolerance_for(rendered or repr(value), target)

    found = parse_measurements(evidence)
    if not found:
        return (False, "no_measurement_in_evidence")
    if metric and not _metric_context_supported(metric, value, unit, rendered, evidence):
        return (False, f"metric_mismatch: value is not attached to {metric}")

    # 第一轮：数值与单位都要对上。单位是"同一个数字属于哪个指标"的唯一线索，
    # 放宽它就等于允许把 SSIM 的 0.948 报成 PSNR。
    for measurement in found:
        if measurement.unit == canonical_unit and abs(measurement.value - target) <= tolerance:
            return (True, "quantity_found_in_evidence")

    # 第二轮：数值对上但单位不符——单独报出来。这是最危险的一类错误
    # （数字是真的，含义是错的），必须让人看到而不是笼统说"没找到"。
    for measurement in found:
        if abs(measurement.value - target) <= tolerance:
            return (
                False,
                f"unit_mismatch: 原文为 {measurement.raw}，论断声明单位 {unit or '无'}",
            )

    nearest = min(found, key=lambda m: abs(m.value - target))
    return (
        False,
        f"quantity_not_in_evidence: 原文最接近的是 {nearest.raw}",
    )
