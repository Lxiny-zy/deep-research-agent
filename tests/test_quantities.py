"""数值与单位的确定性校验。

逐字证据校验回答"这句话在不在原文里"；本层回答"句子里的数字有没有被抄对、
有没有错配到别的指标上"。这两类失败完全不同——被改的是数值与单位的对应关系，
不是措辞，所以逐字匹配对它们全部无感。

测试覆盖的性质:

* 数值必须真的出现在证据原文里，容差按论断自身的有效位数定；
* **单位不符要单独报出来**——数字是真的、含义是错的，这是最危险的一类；
* 未声明数值不受影响（``not_applicable``），既有定性论断的准入行为不变；
* 声明了却对不上 = 编造的数字，拒入报告；
* 比较符不被吞掉（"超过 35" ≠ "等于 35"）。
"""

from __future__ import annotations

import pytest

from deep_research.guardrails import EvidenceVerifier, report_eligible
from deep_research.models import ExperimentConditions, Finding, Quantity, Source
from deep_research.quantities import (
    comparison_supported,
    detect_comparator,
    measurement_supported,
    normalize_unit,
    parse_measurements,
    tolerance_for,
)

_QUOTE = "Our method achieves 38.36 dB in PSNR and 0.967 in SSIM on the KAIST simulation set."


def _source(content: str = _QUOTE) -> Source:
    return Source(title="DAUHST", url="https://arxiv.org/abs/2205.10102v3", content=content)


def _finding(
    *,
    quote: str,
    quantity: Quantity | None = None,
    conditions: ExperimentConditions | None = None,
) -> Finding:
    return Finding(
        statement="该方法达到 38.36 dB",
        source_url="https://arxiv.org/abs/2205.10102v3",
        evidence_quote=quote,
        quantity=quantity,
        conditions=conditions,
    )


# ── 解析 ────────────────────────────────────────────────────────────────────


def test_parses_number_unit_pairs() -> None:
    found = parse_measurements("PSNR 38.36 dB, SSIM 0.967, params 2.03 M, 450 nm")
    pairs = [(m.value, m.unit) for m in found]

    assert (38.36, "db") in pairs
    assert (0.967, "") in pairs
    assert (450.0, "nm") in pairs
    # M 是量级后缀：2.03 M → 2.03e6（浮点乘法有表示误差，按近似比）
    magnitudes = [value for value, unit in pairs if unit == "" and value > 1000]
    assert magnitudes and magnitudes[0] == pytest.approx(2_030_000.0)


def test_micrometre_variants_normalise_to_nanometres() -> None:
    """μ 有两个码位（U+03BC 与 U+00B5），论文里两种都出现。"""
    for text in ("5 μm", "5 µm", "5 um"):
        assert parse_measurements(text)[0].value == pytest.approx(5_000.0)
        assert parse_measurements(text)[0].unit == "nm"


def test_thousands_separator_is_not_a_different_number() -> None:
    assert parse_measurements("44,250 parameters")[0].value == pytest.approx(44_250.0)


def test_compound_units_are_matched_before_their_prefixes() -> None:
    """先匹配 gflops 再匹配 g，否则 GFLOPs 会被切成 G + FLOPs。"""
    found = parse_measurements("22.05 GFLOPs")

    assert found[0].unit == "flops"
    assert found[0].value == pytest.approx(22.05e9)


def test_a_unit_glued_to_a_word_is_not_treated_as_a_unit() -> None:
    """ "35 mask" 里的 m 不能被当成兆——单位后必须是非字母。"""
    found = parse_measurements("35 mask patterns")

    assert found[0].value == 35.0
    assert found[0].unit == ""


def test_unknown_units_are_kept_verbatim_without_conversion() -> None:
    assert normalize_unit("furlong") == ("furlong", 1.0)


# ── 容差 ────────────────────────────────────────────────────────────────────


def test_tolerance_follows_the_significant_figures_of_the_claim() -> None:
    """声明两位小数就按 ±0.005 判；不同精度的断言有各自的容差。"""
    assert tolerance_for("38.36", 38.36) == pytest.approx(0.005)
    assert tolerance_for("38.4", 38.4) == pytest.approx(0.05)
    assert tolerance_for("38", 38.0) == pytest.approx(0.5)


def test_a_correct_rounding_is_accepted_but_an_incorrect_one_is_not() -> None:
    """按有效位数给容差，恰好把"降低精度"与"抄错"分开。

    原文 38.36：声明 38.4 通过（这是它在一位小数下的正确写法），声明 38.3 不通过
    （四舍五入应得 38.4）。既不因为报告取整就误判编造，也不放过真的抄错。
    """
    rounded, _ = measurement_supported(value=38.4, unit="dB", rendered="38.4", evidence=_QUOTE)
    wrong, reason = measurement_supported(value=38.3, unit="dB", rendered="38.3", evidence=_QUOTE)

    assert rounded
    assert not wrong
    assert "38.36" in reason


def test_trailing_zeros_do_not_break_a_match() -> None:
    supported, _ = measurement_supported(value=38.36, unit="dB", rendered="38.360", evidence=_QUOTE)

    assert supported


# ── 支持判定 ────────────────────────────────────────────────────────────────


def test_a_value_present_in_the_evidence_is_supported() -> None:
    supported, reason = measurement_supported(
        value=38.36, unit="dB", rendered="38.36", evidence=_QUOTE
    )

    assert supported
    assert reason == "quantity_found_in_evidence"


def test_a_digit_shifted_by_one_place_is_caught() -> None:
    """34.26 → 3.426 这类抄错逐字匹配完全无感，因为引文本身是对的。"""
    supported, reason = measurement_supported(
        value=3.836, unit="dB", rendered="3.836", evidence=_QUOTE
    )

    assert not supported
    assert "quantity_not_in_evidence" in reason


def test_a_number_borrowed_from_another_metric_is_reported_as_a_unit_mismatch() -> None:
    """把 SSIM 的 0.967 报成 PSNR：数字是真的，含义是错的——最危险的一类。

    所以它有独立的原因码，而不是笼统说"没找到"。
    """
    supported, reason = measurement_supported(
        value=0.967, unit="dB", rendered="0.967", evidence=_QUOTE
    )

    assert not supported
    assert reason.startswith("unit_mismatch")
    assert "0.967" in reason


def test_a_number_borrowed_from_another_metric_is_rejected_with_metric_context() -> None:
    supported, reason = measurement_supported(
        value=0.967,
        unit="",
        rendered="0.967",
        metric="PSNR",
        evidence=_QUOTE,
    )

    assert not supported
    assert reason.startswith("metric_mismatch")


def test_metric_context_accepts_the_matching_number() -> None:
    supported, _ = measurement_supported(
        value=38.36,
        unit="dB",
        rendered="38.36",
        metric="PSNR",
        evidence=_QUOTE,
    )

    assert supported


def test_evidence_without_any_number_cannot_support_a_numeric_claim() -> None:
    supported, reason = measurement_supported(
        value=38.36, unit="dB", rendered="38.36", evidence="The method performs well."
    )

    assert not supported
    assert reason == "no_measurement_in_evidence"


def test_percent_is_not_silently_converted_to_a_bare_ratio() -> None:
    """SSIM 有报 0.948 也有报 94.8% 的。自动换算会抹掉"单位不符"这个真实信号。"""
    supported, reason = measurement_supported(
        value=94.8, unit="%", rendered="94.8", evidence="SSIM reaches 0.948 on the test set."
    )

    assert not supported
    assert "quantity_not_in_evidence" in reason


# ── 比较符 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("超过 35 dB", ">"),
        ("至少 35 dB", ">="),
        ("不低于 35 dB", ">="),
        ("低于 35 dB", "<"),
        ("不超过 35 dB", "<="),
        ("达到 35 dB", ""),
    ],
)
def test_comparators_are_detected(text: str, expected: str) -> None:
    """ "超过 35 dB" 是下界，"等于 35 dB" 是点值。吞掉比较符会让结论变强。"""
    assert detect_comparator(text) == expected


@pytest.mark.parametrize(
    ("comparator", "evidence", "expected"),
    [
        (">", "PSNR > 35 dB", True),
        (">", "PSNR = 35 dB", False),
        (">=", "PSNR > 35 dB", True),
        ("<", "PSNR <= 35 dB", False),
        (">", "PSNR 35 dB", False),
        ("", "PSNR 35 dB", True),
    ],
)
def test_comparison_relation_is_verified_independently(
    comparator: str, evidence: str, expected: bool
) -> None:
    supported, _ = comparison_supported(comparator, evidence)
    assert supported is expected


def test_comparison_relation_is_bound_to_the_claimed_metric() -> None:
    supported, reason = comparison_supported(
        ">",
        "PSNR = 35 dB; SSIM > 0.9",
        value=35,
        unit="dB",
        rendered="35",
        metric="PSNR",
    )

    assert not supported
    assert reason.startswith("comparator_mismatch")


@pytest.mark.parametrize(
    ("comparator", "evidence", "value", "metric", "expected"),
    [
        (">", "PSNR = 35 dB；SSIM 超过 0.9", 0.9, "SSIM", True),
        ("=", "PSNR = 35 dB；SSIM 超过 0.9", 35, "PSNR", True),
        (">", "PSNR 超过 35 dB；SSIM = 0.9", 35, "PSNR", True),
        ("=", "PSNR 超过 35 dB；SSIM = 0.9", 0.9, "SSIM", True),
        (">", "PSNR = 35 dB；SSIM 超过 0.9", 35, "PSNR", False),
    ],
)
def test_comparison_relation_handles_chinese_multi_metric_quotes(
    comparator: str,
    evidence: str,
    value: float,
    metric: str,
    expected: bool,
) -> None:
    supported, _ = comparison_supported(
        comparator,
        evidence,
        value=value,
        unit="dB" if metric == "PSNR" else "",
        rendered=str(value),
        metric=metric,
    )

    assert supported is expected


def test_comparison_relation_does_not_borrow_another_metric_bound() -> None:
    supported, reason = comparison_supported(
        ">",
        "PSNR 35 dB；SSIM 超过 0.9",
        value=35,
        unit="dB",
        rendered="35",
        metric="PSNR",
    )

    assert not supported
    assert reason == "comparator_not_in_evidence"


# ── 与证据门禁的衔接 ────────────────────────────────────────────────────────


def test_verifier_marks_a_supported_quantity_as_verified() -> None:
    check = EvidenceVerifier().verify(
        _finding(
            quote="achieves 38.36 dB in PSNR",
            quantity=Quantity(metric="PSNR", value=38.36, unit="dB", rendered="38.36"),
        ),
        _source(),
    )

    assert check.accepted
    assert check.finding is not None
    assert check.finding.verification.quantity_status == "verified"


def test_verifier_rejects_a_strict_comparator_when_quote_is_a_point_value() -> None:
    check = EvidenceVerifier().verify(
        _finding(
            quote="achieves 38.36 dB in PSNR",
            quantity=Quantity(
                metric="PSNR",
                value=38.36,
                unit="dB",
                rendered="38.36",
                comparator=">",
            ),
        ),
        _source(),
    )

    assert check.finding is not None
    assert check.finding.verification.quantity_status == "unsupported"
    assert "comparator" in check.finding.verification.quantity_reason


def test_verifier_marks_a_fabricated_quantity_as_unsupported() -> None:
    check = EvidenceVerifier().verify(
        _finding(
            quote="achieves 38.36 dB in PSNR",
            quantity=Quantity(metric="PSNR", value=41.02, unit="dB", rendered="41.02"),
        ),
        _source(),
    )

    # 逐字引文本身是对的，所以 finding 仍然通过原文匹配……
    assert check.accepted
    assert check.finding is not None
    assert check.finding.verification.status == "verified"
    # ……但数值校验独立判定为不支持
    assert check.finding.verification.quantity_status == "unsupported"
    assert "38.36" in check.finding.verification.quantity_reason


def test_a_finding_without_a_quantity_is_not_applicable() -> None:
    """绝大多数定性论断如此。它不该因为"没有数值"被拦。"""
    check = EvidenceVerifier().verify(
        _finding(quote="achieves 38.36 dB in PSNR"),
        _source(),
    )

    assert check.finding is not None
    assert check.finding.verification.quantity_status == "not_applicable"


def test_report_eligibility_bars_a_fabricated_number() -> None:
    """带假数字的对照表比一句假话危险得多，因为它看起来是"数据"。"""
    finding = _finding(
        quote="achieves 38.36 dB in PSNR",
        quantity=Quantity(metric="PSNR", value=41.02, unit="dB", rendered="41.02"),
    )
    check = EvidenceVerifier().verify(finding, _source())
    assert check.finding is not None
    verified = check.finding
    verified.verification.semantic_status = "supported"

    assert not report_eligible(verified)


def test_report_eligibility_is_unchanged_for_qualitative_findings() -> None:
    """not_applicable 不影响准入——既有行为必须逐条保持。"""
    check = EvidenceVerifier().verify(_finding(quote="achieves 38.36 dB in PSNR"), _source())
    assert check.finding is not None
    verified = check.finding
    verified.verification.semantic_status = "supported"

    assert report_eligible(verified)


def test_report_eligibility_allows_a_verified_quantity() -> None:
    check = EvidenceVerifier().verify(
        _finding(
            quote="achieves 38.36 dB in PSNR",
            quantity=Quantity(metric="PSNR", value=38.36, unit="dB", rendered="38.36"),
        ),
        _source(),
    )
    assert check.finding is not None
    verified = check.finding
    verified.verification.semantic_status = "supported"

    assert report_eligible(verified)


def test_report_eligibility_rejects_a_quantity_with_default_not_applicable_status() -> None:
    finding = _finding(
        quote="achieves 38.36 dB in PSNR",
        quantity=Quantity(metric="PSNR", value=38.36, unit="dB", rendered="38.36"),
    )
    finding.verification.status = "verified"
    finding.verification.semantic_status = "supported"
    # Simulates a legacy/manual object that bypassed EvidenceVerifier.
    finding.verification.quantity_status = "not_applicable"

    assert not report_eligible(finding)


# ── 实验条件 ────────────────────────────────────────────────────────────────


def test_conditions_describe_reads_as_a_comparability_note() -> None:
    conditions = ExperimentConditions(
        dataset="KAIST",
        split="10 scenes",
        bands=28,
        spatial_size="256×256",
        protocol="2px shift, real mask",
    )

    described = conditions.describe()

    assert "KAIST" in described
    assert "28 波段" in described
    assert "256×256" in described


def test_empty_conditions_are_distinguishable_from_populated_ones() -> None:
    assert ExperimentConditions().is_empty()
    assert not ExperimentConditions(dataset="KAIST").is_empty()


def test_structured_hsi_conditions_are_included_in_description_and_empty_check() -> None:
    conditions = ExperimentConditions(
        spectral_range="400-700 nm",
        scenes="S1-S10",
        acquisition="real capture",
    )

    assert not conditions.is_empty()
    described = conditions.describe()
    assert "400-700 nm" in described
    assert "S1-S10" in described
    assert "real capture" in described


def test_quantities_and_conditions_reach_the_evidence_appendix() -> None:
    """数值与条件必须出现在读者看得到的地方，不能只留在数据库里。"""
    from deep_research.models import Report, ResearchResult
    from deep_research.report import assemble_document, render_markdown

    check = EvidenceVerifier().verify(
        _finding(
            quote="achieves 38.36 dB in PSNR",
            quantity=Quantity(metric="PSNR", value=38.36, unit="dB", rendered="38.36"),
            conditions=ExperimentConditions(dataset="KAIST", split="10 scenes", bands=28),
        ),
        _source(),
    )
    assert check.finding is not None
    url = "https://arxiv.org/abs/2205.10102v3"
    md = render_markdown(
        assemble_document(
            Report(query="q", markdown="正文 [1]", citations=[url]),
            [ResearchResult(sub_question="sq", findings=[check.finding])],
        )
    )

    assert "**数值**：PSNR = 38.36 dB" in md
    assert "**成立条件**：KAIST；10 scenes；28 波段" in md
    assert "**数值校验**：数值已在原文中核对" in md


def test_a_lower_bound_claim_keeps_its_comparator_in_the_appendix() -> None:
    """把"超过 38.36 dB"渲染成"= 38.36 dB"会让报告给出比证据更强的结论。"""
    from deep_research.report.assemble import _quantity_label

    label = _quantity_label(
        Quantity(metric="PSNR", value=38.36, unit="dB", rendered="38.36", comparator=">")
    )

    assert label == "PSNR > 38.36 dB"
