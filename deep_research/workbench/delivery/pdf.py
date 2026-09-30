"""HTML → A4 PDF（PyMuPDF Story 排版，内置 CJK 字体回退，无外部二进制依赖）。

选择 PyMuPDF 而不是 LibreOffice / WeasyPrint / TeX：它已经是全文解析的依赖，
能在无头容器里直接排版中文；不需要额外的系统包，也就不存在「转换器并发抢锁」
「退出码 0 但正文截断」这类外部进程故障。

转换完成后仍做两道自检（与验收门同口径）：页数 > 0，且 PDF 抽取文本的末尾
包含正文最后一段的哨兵片段——PDF 被静默截断时这里就会失败，而不是交付一份
缺了后半截的文档。
"""

from __future__ import annotations

import io
import re
from collections.abc import Mapping

from .html import pdf_html


class PdfRenderError(RuntimeError):
    pass


def _fitz():  # type: ignore[no-untyped-def]
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - 旧版本包名
        import fitz as pymupdf  # type: ignore[no-redef]
    return pymupdf


def _archive_fonts(pymupdf):  # type: ignore[no-untyped-def]
    """把系统里可用的 CJK 字体注册进 Story 的字体档案（缺失时用 PyMuPDF 内置 CJK）。"""
    archive = pymupdf.Archive()
    css = ""
    try:
        from matplotlib import font_manager

        wanted = (
            "Noto Serif CJK SC",
            "Source Han Serif SC",
            "SimSun",
            "Noto Sans CJK SC",
            "Source Han Sans SC",
            "Microsoft YaHei",
            "SimHei",
            "WenQuanYi Zen Hei",
        )
        by_name = {
            font.name: font.fname
            for font in font_manager.fontManager.ttflist
            if font.style == "normal" and font.weight in (400, "normal", "regular", "book")
        }
        for name in wanted:
            path = by_name.get(name)
            if path and path.lower().endswith((".ttf", ".otf", ".ttc")):
                with open(path, "rb") as handle:
                    archive.add(handle.read(), "cjk" + path[path.rfind(".") :].lower())
                css = (
                    f"@font-face{{font-family:cjk;src:url(cjk{path[path.rfind('.') :].lower()});}}"
                )
                break
    except Exception:  # 字体探测失败不影响出 PDF：退回 PyMuPDF 内置字体
        css = ""
    return archive, css


def _tail_sentinel(markdown: str) -> str:
    """最后一个正文块的可检索片段（取自与 PDF 相同的块树，避免把图片语法当正文）。"""
    from .markdown import parse_blocks

    for block in reversed(parse_blocks(markdown)):
        if block.kind not in {"paragraph", "list", "quote", "heading", "table"}:
            continue
        text = re.sub(r"[\s\[\]\d.,，。:：;；|*`#>-]+", "", block.plain())
        if len(text) >= 4:
            return text[-6:]
    return ""


def _squash(text: str) -> str:
    return re.sub(r"[\s\[\]\d.,，。:：;；|*`#>-]+", "", text)


def render_pdf(
    markdown: str,
    *,
    title: str,
    meta: str = "",
    images: Mapping[str, bytes] | None = None,
) -> bytes:
    pymupdf = _fitz()
    body, css = pdf_html(markdown, title=title, meta=meta, images=images)
    archive, font_css = _archive_fonts(pymupdf)
    family = "cjk, sans-serif" if font_css else "sans-serif"
    user_css = font_css + css.replace("font-family:sans-serif", f"font-family:{family}")
    story = pymupdf.Story(html=body, user_css=user_css, archive=archive)
    mediabox = pymupdf.paper_rect("a4")
    where = mediabox + (54, 60, -54, -60)
    stream = io.BytesIO()
    writer = pymupdf.DocumentWriter(stream)
    more = True
    pages = 0
    while more:
        device = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
        pages += 1
        if pages > 400:
            writer.close()
            raise PdfRenderError("PDF 页数超过 400 页上限，疑似排版死循环")
    writer.close()
    data = stream.getvalue()
    # 页码：在已排版的 PDF 上逐页写页脚
    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        for number, page in enumerate(document, 1):
            rect = page.rect
            page.insert_text(
                (rect.width / 2 - 12, rect.height - 30),
                f"{number} / {document.page_count}",
                fontsize=8,
                color=(0.45, 0.5, 0.56),
            )
        # 字体子集化：整套 CJK 字体动辄 10 MB，子集后只保留用到的字形（通常 < 100 KB）。
        document.subset_fonts()
        data = document.tobytes(deflate=True, garbage=3)
    finally:
        document.close()
    verify_pdf(data, markdown)
    return data


def pdf_text(data: bytes) -> tuple[int, str]:
    pymupdf = _fitz()
    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        return document.page_count, "".join(page.get_text() for page in document)
    finally:
        document.close()


def verify_pdf(data: bytes, markdown: str) -> None:
    pages, text = pdf_text(data)
    if pages < 1:
        raise PdfRenderError("PDF 没有页面")
    if "�" in text:
        raise PdfRenderError("PDF 含无法显示的字符（字体缺字）")
    sentinel = _tail_sentinel(markdown)
    if sentinel and sentinel not in _squash(text):
        raise PdfRenderError("PDF 末尾缺少正文最后一段，疑似被截断")


__all__ = ["PdfRenderError", "pdf_text", "render_pdf", "verify_pdf"]
