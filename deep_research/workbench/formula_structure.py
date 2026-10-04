"""A bounded, non-executing TeX expression parser for conservative comparisons.

It normalizes grouping and numeric fractions, not arbitrary algebra or domains.
Unsupported notation is left to the separate source-grounded formula review.
"""

from __future__ import annotations

import re
from collections import Counter
from fractions import Fraction
from functools import lru_cache
from typing import Any

Node = tuple[Any, ...]
_TOKENS = re.compile(r"\\[A-Za-z]+|\\.|(?:\d+(?:\.\d*)?|\.\d+)|[^\s]")
_GREEK = dict(
    zip(
        (
            "alpha beta gamma delta epsilon varepsilon zeta eta theta iota kappa lambda "
            "mu nu xi pi rho sigma tau upsilon phi varphi chi psi omega "
            "Gamma Delta Theta Lambda Xi Pi Sigma Phi Psi Omega"
        ).split(),
        "α β γ δ ϵ ε ζ η θ ι κ λ μ ν ξ π ρ σ τ υ ϕ φ χ ψ ω Γ Δ Θ Λ Ξ Π Σ Φ Ψ Ω".split(),
        strict=True,
    )
)
_OPERATORS = {"sum", "prod", "int", "min", "max", "lim", "argmin", "argmax"}
_COMMANDS = {
    r"\lVert": "‖",
    r"\rVert": "‖",
    r"\Vert": "‖",
    r"\|": "‖",
    r"\vert": "|",
    r"\cdot": "·",
    r"\times": "×",
    r"\le": "≤",
    r"\leq": "≤",
    r"\ge": "≥",
    r"\geq": "≥",
    r"\ne": "≠",
    r"\neq": "≠",
    r"\in": "∈",
    r"\approx": "≈",
    r"\equiv": "≡",
    "−": "-",
    "µ": "μ",
}
_PRIORITY = {
    "=": 1,
    "≤": 1,
    "≥": 1,
    "<": 1,
    ">": 1,
    "≠": 1,
    "≈": 1,
    "≡": 1,
    "∈": 1,
    "+": 2,
    "-": 2,
    "*": 3,
    "/": 3,
    "·": 3,
    "×": 3,
}


class UnsupportedFormula(ValueError):
    pass


def _num(value: Fraction | int) -> Node:
    number = Fraction(value)
    if max(number.numerator.bit_length(), number.denominator.bit_length()) > 512:
        raise UnsupportedFormula("numeric reduction too large")
    return ("number", number.numerator, number.denominator)


def _value(node: Node) -> Fraction | None:
    return Fraction(node[1], node[2]) if node[0] == "number" else None


def _mul(*nodes: Node) -> Node:
    flattened: list[Node] = []
    for node in nodes:
        for factor in node[1:] if node[0] == "mul" else [node]:
            if factor == _num(1):
                continue
            previous = _value(flattened[-1]) if flattened else None
            current = _value(factor)
            if previous is not None and current is not None:
                flattened[-1] = _num(previous * current)
            else:
                flattened.append(factor)
    return (
        _num(1) if not flattened else flattened[0] if len(flattened) == 1 else ("mul", *flattened)
    )


def _div(left: Node, right: Node) -> Node:
    denominator = _value(right)
    if denominator is not None and denominator:
        return _mul(_num(1 / denominator), left)
    if right[0] == "mul" and (factor := _value(right[1])):
        return _mul(_num(1 / factor), _div(left, _mul(*right[2:])))
    return ("div", left, right)


def _pow(base: Node, exponent: Node) -> Node:
    power = _value(exponent)
    if power is not None and power.denominator == 1:
        if power < 0:
            return _div(_num(1), _pow(base, _num(-power)))
        if power == 1:
            return base
        value = _value(base)
        if value is not None and 0 <= power <= 16 and not (value == 0 and power == 0):
            if max(value.numerator.bit_length(), value.denominator.bit_length()) * int(power) > 512:
                raise UnsupportedFormula("numeric power too large")
            return _num(value ** int(power))
    return ("pow", base, exponent)


class _Parser:
    def __init__(self, tex: str) -> None:
        if len(tex) > 16000:
            raise UnsupportedFormula("formula too long")
        tex = re.sub(r"\\arg\s*\\(min|max)(?![A-Za-z])", r"\\operatorname{arg\1}", tex)
        tex = re.sub(
            r"\\(?:left|right|bigl|bigr|Bigl|Bigr|big|Big|displaystyle|textstyle|limits)"
            r"(?![A-Za-z])|\\[,;! ]",
            "",
            tex,
        )
        tex = re.sub(r"\\(?:label|tag)\{[^{}]*\}", "", tex).strip().rstrip(".;")
        self.text = tex
        self.tokens = list(_TOKENS.finditer(tex))
        if len(self.tokens) > 4000:
            raise UnsupportedFormula("too many formula tokens")
        self.index = 0
        self.depth = 0

    def peek(self) -> str:
        raw = self.tokens[self.index][0] if self.index < len(self.tokens) else ""
        return _COMMANDS.get(raw, raw)

    def pop(self) -> str:
        value = self.peek()
        self.index += 1
        return value

    def group(self, opening: str = "{") -> Node:
        if self.pop() != opening:
            raise UnsupportedFormula("explicit group required")
        closing = {"{": "}", "(": ")", "[": "]"}[opening]
        self.depth += 1
        if self.depth > 96:
            raise UnsupportedFormula("formula nesting too deep")
        parts = [self.expression(stops={closing, ","})]
        while self.peek() == ",":
            self.pop()
            parts.append(self.expression(stops={closing, ","}))
        if self.pop() != closing:
            raise UnsupportedFormula("unclosed group")
        self.depth -= 1
        return parts[0] if len(parts) == 1 else ("tuple", *parts)

    def raw_group(self) -> str:
        if self.peek() != "{":
            raise UnsupportedFormula("text group missing")
        start = self.tokens[self.index].end()
        self.pop()
        depth = 1
        while self.index < len(self.tokens):
            token = self.tokens[self.index]
            value = self.pop()
            depth += (value == "{") - (value == "}")
            if depth == 0:
                return self.text[start : token.start()].strip()
        raise UnsupportedFormula("text group unclosed")

    def script(self) -> Node:
        if self.peek() == "{":
            return self.group()
        if re.fullmatch(r"\d{2,}(?:\.\d*)?", self.peek()):
            raise UnsupportedFormula("unbraced multi-digit script")
        return self.atom()

    def atom(self) -> Node:
        value = self.peek()
        if not value:
            raise UnsupportedFormula("missing expression")
        if value in {"{", "(", "["}:
            return self.group(value)
        if value in {"‖", "|"}:
            self.pop()
            inside = self.expression(stops={value})
            if self.pop() != value:
                raise UnsupportedFormula("unclosed norm")
            return ("norm" if value == "‖" else "abs", inside)
        self.pop()
        if re.fullmatch(r"\d+(?:\.\d*)?|\.\d+", value):
            if len(value) > 64:
                raise UnsupportedFormula("numeric literal too long")
            return _num(Fraction(value))
        if value in {r"\frac", r"\dfrac", r"\tfrac"}:
            return _div(self.group(), self.group())
        if value == r"\sqrt":
            degree = self.group("[") if self.peek() == "[" else _num(2)
            return ("root", self.group(), degree)
        if value in {r"\operatorname", r"\mathrm", r"\text", r"\mathbf", r"\mathbb", r"\mathcal"}:
            name = self.raw_group()
            if name in _OPERATORS:
                return ("operator", name)
            return ("symbol", name, value)
        if value.startswith("\\"):
            name = value[1:]
            if name in _GREEK:
                return ("symbol", _GREEK[name])
            if name in _OPERATORS:
                return ("operator", name)
            raise UnsupportedFormula("unknown TeX command")
        if value.isalpha():
            return ("symbol", value)
        raise UnsupportedFormula("unsupported token")

    def factor(self) -> Node:
        sign = 1
        while self.peek() in {"+", "-"}:
            sign *= -1 if self.pop() == "-" else 1
        node = self.atom()
        lower: Node | None = None
        upper: Node | None = None
        while self.peek() in {"_", "^"}:
            kind = self.pop()
            value = self.script()
            if kind == "_":
                if lower is not None:
                    raise UnsupportedFormula("duplicate subscript")
                lower = value
            else:
                if upper is not None:
                    raise UnsupportedFormula("duplicate superscript")
                upper = value
        if node[0] == "operator" and (lower is not None or upper is not None):
            node = ("bounds", node, lower, upper)
        else:
            if lower is not None:
                node = ("sub", node, lower)
            if upper is not None:
                node = _pow(node, upper)
        if node[0] in {"symbol", "sub"} and self.peek() == "(":
            node = ("call", node, self.group("("))
        return _mul(_num(sign), node)

    def expression(self, minimum: int = 0, *, stops: set[str] | None = None) -> Node:
        stops = stops or set()
        left = self.factor()
        while (token := self.peek()) and token not in stops and token not in {"}", ")", "]", ","}:
            explicit = token in _PRIORITY
            priority = _PRIORITY.get(token, 3)
            if priority < minimum:
                break
            operator = self.pop() if explicit else "implicit"
            right = self.expression(priority + 1, stops=stops)
            if operator == "/":
                left = _div(left, right)
            elif operator == "implicit":
                left = _mul(left, right)
            else:
                left = (operator, left, right)
        return left


@lru_cache(maxsize=128)
def formula_tree(tex: str) -> Node | None:
    try:
        parser = _Parser(tex)
        tree = parser.expression()
        stack = [(tree, 0)]
        while stack:
            node, depth = stack.pop()
            if depth > 96:
                raise UnsupportedFormula("expression nesting too deep")
            stack.extend((child, depth + 1) for child in node[1:] if isinstance(child, tuple))
        return tree if parser.index == len(parser.tokens) else None
    except (UnsupportedFormula, ValueError, ZeroDivisionError, RecursionError):
        return None


def _symbols(node: Any) -> Counter[str]:
    if not isinstance(node, tuple):
        return Counter()
    if node[0] == "symbol":
        return Counter([str(node)])
    return sum((_symbols(child) for child in node[1:]), Counter())


def _differences(left: Any, right: Any) -> list[str]:
    if left == right:
        return []
    if not isinstance(left, tuple) or not isinstance(right, tuple):
        return ["unresolved"]
    if left == ("div", _num(1), right) or right == ("div", _num(1), left):
        return ["倒数或分母位置不同"]
    if left[0] == right[0] == "number":
        return ["数值系数不同"]
    if left[0] == right[0] and left[0] in {"pow", "sub", "bounds"} and left[1] == right[1]:
        return [
            {"pow": "上标或指数不同", "sub": "下标不同", "bounds": "求和或算子范围不同"}[left[0]]
        ]
    if left[0] != right[0] or len(left) != len(right):
        return ["unresolved"]
    return [issue for a, b in zip(left[1:], right[1:], strict=True) for issue in _differences(a, b)]


def compare_formulas(reported: str, reference: str) -> tuple[str, str]:
    """Prove literal/numeric equivalence or a single clear transcription change."""
    left, right = formula_tree(reported), formula_tree(reference)
    if left is None or right is None:
        return "unresolved", "公式语法需要专门核对"
    if left == right:
        return "equal", "公式结构一致"
    stack = [left, right]
    while stack:
        node = stack.pop()
        if node[0] in {"≈", "≤", "≥", "<", ">", "≠", "≡", "∈"}:
            return "unresolved", "近似、关系或取值范围的改写需要专门核对"
        stack.extend(child for child in node[1:] if isinstance(child, tuple))
    same_left = left[0] == right[0] == "=" and left[1] == right[1]
    if not same_left and _symbols(left) != _symbols(right):
        return "unresolved", "符号或变量映射需要专门核对"
    differences = _differences(left[2], right[2]) if same_left else _differences(left, right)
    if len(differences) == 1 and differences[0] != "unresolved":
        return "different", differences[0]
    return "unresolved", "公式结构不同，需核对是否为有依据的等价改写"
