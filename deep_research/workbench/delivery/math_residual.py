"""Detect visible TeX syntax while preserving real math and deliberate code."""

from __future__ import annotations

import io
import re
from functools import lru_cache
from html.parser import HTMLParser
from xml.etree import ElementTree as ET
from zipfile import ZipFile

_TEX = re.compile(
    r"(?:[\w)]\s*[_^]\s*\{[^{}\n]+\})"
    r"|(?:[\w)]\s*\^\s*[-+−]?\d+\b)"
    r"|(?:\\[A-Za-z]+\s*(?=\{))"
    r"|(?:\\(?:frac|dfrac|tfrac|sqrt|sum|prod|int|left|right|begin|end|"
    r"mathbf|mathrm|mathbb|mathcal|text|operatorname|alpha|beta|gamma|delta|"
    r"lambda|sigma|theta|mu|tau|Phi|phi|epsilon|varepsilon|eta|rho|varrho|"
    r"xi|psi|omega|zeta|kappa|nu|pi|Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Psi|Omega|"
    r"times|cdot|infty|partial|nabla|sim|approx|le|leq|ge|geq|neq|pm|ldots|cdots|"
    r"propto|rightarrow|in)\b)"
)
# LaTeX 文本命令要改写成 Markdown，不是补数学标记；混为一谈时写作者会把网址包进公式。
_TEXT_COMMANDS = frozenset(
    {"url", "href", "textbf", "textit", "texttt", "emph", "cite", "citep", "citet", "ref",
     "eqref", "label", "footnote"}
)
_MONO = re.compile(r"consolas|courier|mono", re.I)


def raw_tex_fragments(text: str) -> list[str]:
    return list(dict.fromkeys(match.group() for match in _TEX.finditer(text)))


# 一段不含空白与中文的式子；管道符留在外面，表格会把它转义成列分隔。乘号算在式内，
# 1×10^-4 拆成两段时数值会被读成 1 与 10^-4
_RUN = re.compile(r"(?:\\[A-Za-z]+|[A-Za-z0-9α-ωΑ-Ω_^{}+\-−*/=().,×·])+")
# 带上下标的单个记号：z_{k+1}、τ_{k+1}、10^{-4}、\mathbf{x}_k
_ATOM = re.compile(
    r"(?:\\[A-Za-z]+|[A-Za-zα-ωΑ-Ω]|\d+(?:\.\d+)?)(?:\{[^{}]*\})?"
    r"(?:[_^](?:\{[^{}]*\}|[-+−]?\d+|[A-Za-zα-ωΑ-Ω]))+"
)


def mark_bare_math(text: str) -> str:
    """给引用文字中的裸 TeX 补上行内公式标记，字符本身不变。

    发现原句常写 z_{k+1}、10^-4 而不加 $；表格逐字展示这些原句时，交付文件里就是裸 TeX，
    写作者也无从修改。已含 $ 的文字不动，以免与原有公式交错；渲染器不认的片段保持原样。
    带运算符的式子会被当作要逐式核对的公式，这时只标记其中带上下标的记号。
    """
    if "$" in text or not raw_tex_fragments(text):
        return text
    return _RUN.sub(_mark_run, text)


def _mark_run(match: re.Match[str]) -> str:
    from ..formula_review import _qualifies

    run = match[0]
    core = run.rstrip(".,")
    head, tail = "", run[len(core) :]
    while core.endswith(")") and core.count(")") > core.count("("):
        core, tail = core[:-1], ")" + tail
    while core.startswith("(") and core.count("(") > core.count(")"):
        head, core = head + "(", core[1:]
    if core.count("{") != core.count("}") or not raw_tex_fragments(core):
        return run
    if core.count("(") == core.count(")") and not _qualifies(core) and _renders(_braced(core)):
        return f"{head}${_braced(core)}${tail}"
    marked = _ATOM.sub(
        lambda atom: f"${_braced(atom[0])}$" if _renders(_braced(atom[0])) else atom[0], core
    )
    return head + marked + tail


def _braced(tex: str) -> str:
    # 纯文本里的 10^-4 指 10 的 -4 次方；TeX 只把紧跟的一个字符作上标
    return re.sub(r"\^(?!\{)([-+−]?\d+)", r"^{\1}", tex)


@lru_cache(maxsize=512)
def _renders(tex: str) -> bool:
    from .math import checked_mathml

    try:
        checked_mathml(tex)
    except Exception:  # noqa: BLE001 - 渲染器不认的片段保持原样，交给公式标记门禁
        return False
    return True


def tex_issue_messages(
    fragments: list[str], *, formula_prefix: str = "为未渲染公式补齐数学标记，保留原表达式及条件"
) -> list[str]:
    """把未渲染片段分成文本命令与公式，各自给出能照做的修改要求。"""
    commands = [item for item in fragments if item.strip().lstrip("\\") in _TEXT_COMMANDS]
    formulas = [item for item in fragments if item not in commands]
    messages = []
    if commands:
        messages.append(
            "LaTeX 文本命令不会渲染，改写为 Markdown（网址直接写出或写成 [文字](网址)，"
            "强调用 **…**，删去引用与标签命令）："
            + "、".join(item.strip() for item in commands[:3])
        )
    if formulas:
        messages.append(formula_prefix + "：" + "、".join(formulas[:3]))
    return messages


class _VisibleHTML(HTMLParser):
    def __init__(self, *, article_only: bool) -> None:
        super().__init__(convert_charrefs=True)
        self.article_only = article_only
        self.stack: list[str] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"p", "div", "li", "tr", "br", "pre", "code"}:
            self.parts.append("\n")
        if tag not in {"br", "hr", "img", "meta", "link", "input", "wbr"}:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.stack:
            last = len(self.stack) - 1 - self.stack[::-1].index(tag)
            del self.stack[last:]
        if tag in {"p", "div", "li", "tr", "pre", "code", "math", "svg"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.article_only and "article" not in self.stack:
            return
        if not {"style", "script", "math", "svg", "pre", "code"}.intersection(self.stack):
            self.parts.append(data)


def visible_format_text(fmt: str, data: bytes) -> str:
    if fmt == "html":
        text = data.decode("utf-8")
        # The source-reading apparatus preserves verbatim original quotations;
        # inspect the authored report, not those independently displayed sources.
        parser = _VisibleHTML(article_only=bool(re.search(r"<article\b", text, re.I)))
        parser.feed(text)
        parser.close()
        return "".join(parser.parts)
    if fmt == "docx":
        with ZipFile(io.BytesIO(data)) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = []
        for paragraph in root.findall(".//w:p", ns):
            parts = []
            for run in paragraph.findall(".//w:r", ns):
                font = run.find("w:rPr/w:rFonts", ns)
                if font is not None and any(_MONO.search(name) for name in font.attrib.values()):
                    parts.append("\n")
                    continue
                # Office Math has m:t elements, which are not raw rendered TeX.
                parts.append("".join(text.text or "" for text in run.findall(".//w:t", ns)))
            paragraphs.append("".join(parts))
        return "\n".join(paragraphs)
    if fmt == "pdf":
        import pymupdf

        with pymupdf.open(stream=data, filetype="pdf") as document:
            return "\n".join(
                "".join(
                    "".join(chr(char[0]) for char in span["chars"])
                    if span["type"] != 3 and not _MONO.search(span["font"])
                    else "\n"
                    for span in page.get_texttrace()
                )
                for page in document
            )
    raise ValueError(f"不支持的公式残留检查格式：{fmt}")


def residual_math_issues(fmt: str, data: bytes) -> list[str]:
    found = raw_tex_fragments(visible_format_text(fmt, data))
    if not found:
        return []
    sample = "、".join(fragment[:60] for fragment in found[:3])
    return [f"{fmt.upper()} 正文存在未渲染的公式语法（{sample}），需修订公式标记或修复渲染"]
