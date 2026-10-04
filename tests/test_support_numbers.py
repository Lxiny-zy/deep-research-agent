from decimal import Decimal

import pytest

from deep_research.workbench.support_numbers import normalize_scientific_numbers


@pytest.mark.parametrize(
    "rendered, normalized",
    [
        ("$10^{-3}$", "$1e-3$"),
        (r"$1 \times 10^{-3}$", "$1e-3$"),
        (r"$-2.50 \cdot 10^{+4}$", "$-2.50e4$"),
        ("误差为2 × 10^−3 [1]。", "误差为2e-3 [1]。"),
        ("10⁻³", "1e-3"),
        ("3.2 × 10⁺²", "3.2e2"),
        (".5 · 10 ^ { -2 }", ".5e-2"),
        ("10^0 and 10^10000", "1e0 and 1e10000"),
        ("The error is 10^-3.", "The error is 1e-3."),
    ],
)
def test_explicit_scientific_numbers_have_equivalent_decimal_spelling(rendered, normalized):
    assert normalize_scientific_numbers(rendered) == normalized
    assert normalize_scientific_numbers(normalized) == normalized


@pytest.mark.parametrize(
    "text",
    [
        "x10^k", "x10^-3", "O(n^2)", "10^{k}", "10^2.5", "95%", "0.95", "1e-3",
        "2 × 103", "2 × 10 -3", "10^10001", "10^" + "9" * 1000,
        "1" * 129 + " × 10^-3", "1e2 × 10^-3",
    ],
)
def test_symbolic_ambiguous_and_unbounded_forms_are_not_rewritten(text):
    assert normalize_scientific_numbers(text) == text


def test_equivalent_scientific_forms_can_be_compared_without_expanding_large_numbers():
    assert Decimal(normalize_scientific_numbers("10^{-3}")) == Decimal("0.001")
    assert len(normalize_scientific_numbers("10^10000")) < 20
