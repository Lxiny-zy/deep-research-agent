"""TeX math -> checked MathML, SVG paths and editable Office Math.

This parser never executes TeX. Assets are local and deterministic; unsupported
syntax or missing glyphs are errors, not silently omitted formula content.
"""

from __future__ import annotations

import copy
import hashlib
import io
import re
import tempfile
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

_LOCK = threading.RLock()
_MATHML_TAGS = {
    "math",
    "mrow",
    "mi",
    "mo",
    "mn",
    "ms",
    "mtext",
    "mspace",
    "msqrt",
    "mroot",
    "msub",
    "msup",
    "msubsup",
    "munder",
    "mover",
    "munderover",
    "mmultiscripts",
    "mprescripts",
    "none",
    "mfrac",
    "mtable",
    "mtr",
    "mtd",
    "mstyle",
    "menclose",
    "mpadded",
    "mphantom",
    "mfenced",
}
_ENVIRONMENTS = {
    "matrix",
    "pmatrix",
    "bmatrix",
    "Bmatrix",
    "vmatrix",
    "Vmatrix",
    "smallmatrix",
    "array",
    "aligned",
    "align",
    "align*",
    "gathered",
    "gather*",
    "cases",
    "split",
}
_FORBIDDEN = re.compile(
    r"\\(?:input|include|includegraphics|write\d*|read|openin|openout|closein|closeout|catcode|csname|"
    r"def|gdef|edef|xdef|let|newcommand|renewcommand|usepackage|documentclass|href|url|html\w*|"
    r"style|class|require|special|loop|repeat|unicode|verb|hspace|vspace|rule|raisebox)\b"
)


class MathRenderError(ValueError):
    pass


@dataclass(frozen=True)
class MathAsset:
    tex: str
    mathml: str
    svg: str
    width: float  # points
    height: float
    descent: float


def checked_mathml(tex: str, *, display: bool = False) -> str:
    from latex2mathml.converter import convert

    if (
        not tex.strip()
        or len(tex) > 16000
        or _FORBIDDEN.search(tex)
        or "^^" in tex
        or re.search(r"(?<!\\)%", tex)
    ):
        raise MathRenderError("公式为空、过长或包含不支持的控制命令")
    depth = 0
    for char in re.sub(r"\\[{}]", "", tex):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        if depth < 0 or depth > 128:
            raise MathRenderError("公式分组未闭合或嵌套过深")
    if depth:
        raise MathRenderError("公式分组未闭合")
    stack = []
    for action, env in re.findall(r"\\(begin|end)\{([^}]+)\}", tex):
        if env not in _ENVIRONMENTS:
            raise MathRenderError(f"不支持的公式环境：{env}")
        if action == "begin":
            stack.append(env)
        elif not stack or stack.pop() != env:
            raise MathRenderError("公式环境未正确配对")
    if stack:
        raise MathRenderError("公式环境未正确配对")
    # The converter supports align* / gather*, while common model output uses
    # their nested equivalents. Both project to the same unnumbered MathML table.
    normalized = tex
    for old, new in (("aligned", "align*"), ("gathered", "gather*")):
        normalized = normalized.replace(r"\begin{" + old + "}", r"\begin{" + new + "}")
        normalized = normalized.replace(r"\end{" + old + "}", r"\end{" + new + "}")
    try:
        value = convert(normalized, display="block" if display else "inline")
        root = ET.fromstring(value)
    except Exception as exc:
        raise MathRenderError("公式语法无法解析") from exc
    if len(value) > 1_000_000 or sum(1 for _ in root.iter()) > 20000:
        raise MathRenderError("公式结构过大")
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] not in _MATHML_TAGS:
            raise MathRenderError("公式包含非数学结构")
        if node.text and re.search(r"\\[A-Za-z]+", node.text):
            raise MathRenderError("公式包含无法识别的命令")
        if any(key.lower().startswith("on") or key in {"href", "src"} for key in node.attrib):
            raise MathRenderError("公式不支持外部资源或交互属性")
        for name in ("mathcolor", "mathbackground"):
            if name in node.attrib and not re.fullmatch(
                r"#[0-9A-Fa-f]{3,8}|[A-Za-z]{1,20}", node.attrib[name]
            ):
                raise MathRenderError("公式颜色格式不受支持")
    return value


@lru_cache(maxsize=32)
def _cjk_font(characters: str) -> Any:
    import pymupdf
    import ziafont
    from fontTools import subset
    from fontTools.ttLib import TTFont
    from fontTools.ttLib.scaleUpem import scale_upem

    # Use the same embedded CJK fallback already shipped by PyMuPDF. Subset
    # before normalizing units, so we do not retain a full font per expression.
    font = TTFont(io.BytesIO(pymupdf.Font("china-s").buffer))
    selector = subset.Subsetter()
    selector.populate(text=characters)
    selector.subset(font)
    scale_upem(font, 1000)
    key = hashlib.sha256(characters.encode()).hexdigest()[:16]
    with tempfile.TemporaryDirectory(prefix="dr-math-font-") as directory:
        path = Path(directory) / f"cjk-{key}.ttf"
        font.save(path)
        result = ziafont.Font(path)  # FontReader retains bytes, not an open file.
    font.close()
    return result


@lru_cache(maxsize=256)
def math_asset(tex: str, display: bool = False, size: float = 10.5) -> MathAsset:
    import ziafont
    import ziamath
    from ziamath.styles import styledchr
    from ziamath.zmath import loadedfonts

    value = checked_mathml(tex, display=display)
    root = ET.fromstring(value)
    with _LOCK:
        base = loadedfonts["default"]
        missing = "".join(
            sorted(
                {
                    c
                    for node in root.iter()
                    for c in (node.text or "")
                    if not c.isspace() and base.glyphindex(c) == 0
                }
            )
        )
        fallback = _cjk_font(missing) if missing else None
        combined = copy.copy(base)

        def findglyph(char, variant):  # type: ignore[no-untyped-def]
            glyph = base.findglyph(char, variant)
            if glyph.index == 0 and fallback is not None:
                glyph = fallback.glyph(styledchr(char, variant))
                if glyph.index == 0:
                    glyph = fallback.glyph(char)
            if glyph.index == 0 and not char.isspace():
                raise MathRenderError(f"公式字体缺少字符：U+{ord(char):04X}")
            return glyph

        combined.findglyph = findglyph  # type: ignore[method-assign]
        loadedfonts["dr-composite"] = combined
        svg2 = ziafont.config.svg2
        ziafont.config.svg2 = (
            False  # Explicit paths: MuPDF does not render SVG2 symbol/use reliably.
        )
        try:
            equation = ziamath.Math(
                ET.fromstring(value), size=size / 0.75, font="dr-composite", title=tex
            )
            svg = equation.svgxml()
            width, height = float(svg.attrib["width"]) * 0.75, float(svg.attrib["height"]) * 0.75
            descent = max(0.0, -equation.getyofst() * 0.75)
        except MathRenderError:
            raise
        except Exception as exc:
            raise MathRenderError("公式结构无法排版") from exc
        finally:
            loadedfonts.pop("dr-composite", None)
            ziafont.config.svg2 = svg2
    if width > 5000 or height > 5000:
        raise MathRenderError("公式尺寸过大，请拆成多个表达式")
    # SVG identifiers must be local to an expression, including different sizes.
    prefix = "math-" + hashlib.sha256(f"{tex}/{display}/{size}".encode()).hexdigest()[:16] + "-"
    for node in svg.iter():
        if "id" in node.attrib:
            node.set("id", prefix + node.attrib["id"])
        for key, item in list(node.attrib.items()):
            if key.endswith("href") and item.startswith("#"):
                node.set(key, "#" + prefix + item[1:])
    return MathAsset(tex, value, ET.tostring(svg, encoding="unicode"), width, height, descent)


def office_math(tex: str, display: bool = False) -> str:
    import mathml2omml

    try:
        return str(mathml2omml.convert(checked_mathml(tex, display=display)))
    except MathRenderError:
        raise
    except Exception as exc:
        raise MathRenderError("公式无法转换为 Word 数学结构") from exc


def native_tex(tex: str) -> str:
    """Only validated mathematical commands may enter the fixed TeX template."""
    checked_mathml(tex)
    for old, new in (("align", "aligned"), ("align*", "aligned"), ("gather*", "gathered")):
        tex = tex.replace(r"\begin{" + old + "}", r"\begin{" + new + "}")
        tex = tex.replace(r"\end{" + old + "}", r"\end{" + new + "}")
    return tex
