"""Detect visible TeX syntax while preserving real math and deliberate code."""

from __future__ import annotations

import io
import re
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
    r"times|cdot|infty|partial|nabla)\b)"
)
_MONO = re.compile(r"consolas|courier|mono", re.I)


def raw_tex_fragments(text: str) -> list[str]:
    return list(dict.fromkeys(match.group() for match in _TEX.finditer(text)))


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
