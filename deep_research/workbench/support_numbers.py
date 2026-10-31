"""Normalize explicit scientific literals without interpreting symbolic formulas."""

from __future__ import annotations

import re

_SUPERSCRIPTS = "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻"
_SUPER_TRANSLATION = str.maketrans(_SUPERSCRIPTS + "−", "0123456789+-" + "-")
_DECIMAL = r"[+−-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)"
_TIMES = r"(?:\\times|\\cdot|×|·)"
_SCIENTIFIC = re.compile(
    rf"(?<![A-Za-z0-9_.\\])(?:(?P<mantissa>{_DECIMAL})\s*{_TIMES}\s*)?"
    rf"10\s*(?:\^\s*(?:\{{\s*(?P<braced>[+−-]?[0-9]+)\s*\}}|"
    rf"(?P<plain>[+−-]?[0-9]+))|(?P<super>[{_SUPERSCRIPTS}]+))"
    rf"(?![A-Za-z0-9_{_SUPERSCRIPTS}]|\.[0-9])"
)
_TRAILING_MULTIPLICATION = re.compile(rf"{_TIMES}\s*$")
# arXiv 的 LaTeXML 页面经文本抽取后，常见可见渲染紧跟其 TeX 源：4×10−44\times 10^{-4}。
# 两者尾数与指数一致时只保留 TeX 形式，否则粘连的数字会被读成另一个数。
_RENDERED_THEN_TEX = re.compile(
    r"(?<![A-Za-z0-9_.\\])(?:(?P<mantissa>[0-9]+(?:\.[0-9]+)?)\s*[×·]\s*)?"
    r"10\s*(?P<sign>[+−-]?)\s*(?P<exponent>[0-9]+)"
    r"(?P<tex>(?(mantissa)(?P=mantissa)\s*(?:\\times|\\cdot)\s*)"
    r"10\s*\^\s*\{\s*(?P<tex_sign>[+−-]?)\s*(?P=exponent)\s*\})"
)


def _keep_tex(match: re.Match[str]) -> str:
    negative = match["sign"] in {"−", "-"}
    return match["tex"] if negative == (match["tex_sign"] in {"−", "-"}) else match[0]


def normalize_scientific_numbers(text: str) -> str:
    """Return Decimal-compatible literals, preserving units and surrounding text.

    Exponents are bounded at 10,000 and mantissas at 128 characters. Conversion
    only changes notation; it neither allocates expanded powers nor guesses at
    flattened PDF exponents, symbolic expressions, or percentage conversions. A
    flattened rendering is dropped only when its own TeX source follows it.
    """
    text = _RENDERED_THEN_TEX.sub(_keep_tex, text)

    def replace(match: re.Match[str]) -> str:
        mantissa = match["mantissa"] or "1"
        exponent = (match["braced"] or match["plain"] or match["super"]).translate(
            _SUPER_TRANSLATION
        )
        if len(mantissa) > 128 or len(exponent.lstrip("+-")) > 5:
            return match[0]
        # Do not salvage a bare power from an unsupported mantissa expression.
        if not match["mantissa"] and _TRAILING_MULTIPLICATION.search(text[:match.start()]):
            return match[0]
        if not re.fullmatch(r"[+-]?[0-9]+", exponent) or abs(int(exponent)) > 10_000:
            return match[0]
        return f"{mantissa.replace('−', '-')}e{int(exponent)}"

    return _SCIENTIFIC.sub(replace, text)
