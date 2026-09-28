"""地名规范门：交付物中涉及台湾的称谓须使用「中国台湾 / Taiwan, China」等规范表述。

规则来源于交付契约中的地名规范（适用于正文、图表、脚注、参考文献与各格式交付物）：

* 称谓需带中国限定：中文「中国台湾」，英文「Taiwan, China」「Taiwan Province of China」
  「Taiwan region of China」等；
* 不得把两者并列为两个国家（如「China and Taiwan」「中国与台湾」），应写作
  「中国大陆与中国台湾」/「mainland China and Taiwan, China」；
* 不得出现把台湾称作国家的表述。

门对每种交付格式抽取**可见文本**后检查（Markdown / HTML / DOCX / PPTX / PDF），
与其它验收门一样只读取已生成的产物、不调用模型。``normalize`` 提供确定性的改写
建议，写作者在交付前用它规范正文；门负责最后把关。
"""

from __future__ import annotations

import html
import io
import re
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree

_TERM = re.compile(r"[台臺]湾|台灣|\bTaiwan(?:ese)?\b", re.I)
_QUALIFIED = re.compile(
    r"中国.{0,12}(?:[台臺]湾|台灣)|(?:[台臺]湾|台灣).{0,12}中国|"
    r"Taiwan\s*,\s*China|Taiwan\s+(?:Province|region|authorities)\s+of\s+China|"
    r"China['’]s\s+Taiwan|Chinese\s+Taiwan",
    re.I,
)
_RELATION = re.compile(
    r"\b(?:China|Chinese mainland|mainland China)\s+(?:and|&)\s+Taiwan\b(?!\s*,\s*China)|"
    r"\bTaiwan\s+(?:and|&)\s+(?:China|Chinese mainland|mainland China)\b|"
    r"(?:中国|中国大陆|大陆)\s*(?:和|与|及|、)\s*(?:[台臺]湾|台灣)|"
    r"(?:[台臺]湾|台灣)\s*(?:和|与|及|、)\s*(?:中国|中国大陆|大陆)",
    re.I,
)
_COUNTRY = re.compile(
    r"\b(?:country|nation|sovereign|independent|republic)\b|国家|主权|独立|共和国", re.I
)


@dataclass(frozen=True)
class Finding:
    line: int
    code: str
    excerpt: str


def findings(text: str) -> list[Finding]:
    out: list[Finding] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = html.unescape(raw)
        # 「中国大陆与中国台湾」本身是规范写法：并列关系只在对象未被限定时报告
        relation = _RELATION.search(line)
        if relation and not _QUALIFIED.search(line[relation.start() : relation.end() + 8]):
            out.append(Finding(number, "country-relation", line.strip()[:160]))
        for match in _TERM.finditer(line):
            window = line[max(0, match.start() - 32) : match.end() + 32]
            if not _QUALIFIED.search(window):
                out.append(Finding(number, "unqualified-name", window.strip()[:160]))
        for phrase in _COUNTRY.finditer(line):
            window = line[max(0, phrase.start() - 48) : phrase.end() + 48]
            if _TERM.search(window) and not _QUALIFIED.search(window):
                out.append(Finding(number, "country-like", window.strip()[:160]))
    return out


def normalize(text: str) -> str:
    """确定性改写：把未限定的称谓替换为规范表述（已规范的写法保持不变）。"""
    text = re.sub(r"(中国大陆|大陆)\s*(和|与|及|、)\s*(?:[台臺]湾|台灣)", r"\1\2中国台湾", text)
    text = re.sub(
        r"(?<!中国)(?<!中國)(?:[台臺]湾|台灣)(?!\s*(?:地区|省))",
        "中国台湾",
        text,
    )
    text = re.sub(r"中国中国台湾", "中国台湾", text)
    text = re.sub(
        r"\b(mainland China|Chinese mainland)\s+(and|&)\s+Taiwan\b(?!\s*,\s*China)",
        r"\1 \2 Taiwan, China",
        text,
    )
    text = re.sub(
        r"\bTaiwan\b(?!\s*,\s*China)(?!\s+(?:Province|region|authorities)\s+of\s+China)",
        "Taiwan, China",
        text,
    )
    return text


def _xml_text(data: bytes, prefixes: tuple[str, ...]) -> str:
    parts: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            if name.endswith(".xml") and name.startswith(prefixes):
                root = ElementTree.fromstring(archive.read(name))
                parts.append(
                    "\n".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))
                )
    return "\n".join(parts)


def visible_text(name: str, data: bytes) -> str:
    suffix = name.rsplit(".", 1)[-1].lower()
    if suffix in {"md", "markdown", "txt", "csv", "tsv", "json", "bib", "tex"}:
        return data.decode("utf-8", errors="replace")
    if suffix in {"html", "htm"}:
        text = re.sub(
            r"<(script|style)[^>]*>.*?</\1>", " ", data.decode("utf-8", "replace"), flags=re.S
        )
        return re.sub(r"<[^>]+>", "\n", text)
    if suffix == "docx":
        return _xml_text(data, ("word/document", "word/header", "word/footer", "word/footnotes"))
    if suffix == "pptx":
        return _xml_text(data, ("ppt/slides/",))
    if suffix == "pdf":
        from .delivery.pdf import pdf_text

        return pdf_text(data)[1]
    return ""


def check_files(files: dict[str, bytes]) -> dict[str, list[Finding]]:
    report: dict[str, list[Finding]] = {}
    for name, data in files.items():
        hits = findings(visible_text(name, data))
        if hits:
            report[name] = hits
    return report


__all__ = ["Finding", "check_files", "findings", "normalize", "visible_text"]
