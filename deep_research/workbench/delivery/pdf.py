"""HTML → A4 PDF（PyMuPDF Story 排版，内置 CJK 字体回退，无外部二进制依赖）。

选择 PyMuPDF 而不是 LibreOffice / WeasyPrint / TeX：它已经是全文解析的依赖，
能在无头容器里直接排版中文；不需要额外的系统包，也就不存在「转换器并发抢锁」
「退出码 0 但正文截断」这类外部进程故障。

转换完成后仍做两道自检（与验收门同口径）：页数 > 0，且 PDF 抽取文本的末尾
包含正文最后一段的哨兵片段——PDF 被静默截断时这里就会失败，而不是交付一份
缺了后半截的文档。
"""

from __future__ import annotations

import base64
import io
import math
import re
from collections.abc import Mapping
from html import unescape

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
    mediabox = pymupdf.paper_rect("a4")
    where = mediabox + (54, 60, -54, -60)
    stream = io.BytesIO()
    writer = pymupdf.DocumentWriter(stream)
    pages = 0
    device = None
    cursor = where.y0
    figure_widths: list[float] = []

    def next_page():  # type: ignore[no-untyped-def]
        nonlocal pages, device, cursor
        if device is not None:
            writer.end_page()
        pages += 1
        if pages > 400:
            writer.close()
            raise PdfRenderError("PDF 页数超过 400 页上限，疑似排版死循环")
        device = writer.begin_page(mediabox)
        cursor = where.y0

    # Story shrinks images to the *remaining* page space instead of moving them
    # intact. Reserve a whole figure and caption before handing it to Story.
    # This also avoids relying on unsupported CSS page-break-inside behavior.
    for part, minimum_height, figure_width in _layout_parts(body, where.height):
        if figure_width is not None:
            figure_widths.append(figure_width)
        if device is None or where.y1 - cursor < max(minimum_height, 36):
            next_page()
        story = pymupdf.Story(html=part, user_css=user_css, archive=archive)
        while True:
            more, filled = story.place(pymupdf.Rect(where.x0, cursor, where.x1, where.y1))
            story.draw(device)
            if not more:
                cursor = pymupdf.Rect(filled).y1
                break
            next_page()
    if device is not None:
        writer.end_page()
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
    verify_pdf(data, markdown, figure_widths=figure_widths)
    return data


def _layout_parts(body: str, page_height: float) -> list[tuple[str, float, float | None]]:
    """Split only the renderer-owned figure wrapper; external HTML is escaped."""
    from PIL import Image

    parts: list[tuple[str, float, float | None]] = []
    for part in re.split(r'(<div class="figure">.*?</div>)', body, flags=re.S):
        if not part.strip():
            continue
        minimum = 0.0
        figure_width = None
        if part.startswith('<div class="figure">'):
            encoded = re.search(r'src="data:image/[^;]+;base64,([^"]+)"', part)
            caption_match = re.search(r'<p class="caption">(.*?)</p>', part, flags=re.S)
            if encoded is None:
                raise PdfRenderError("图像资产无法读取")
            with Image.open(io.BytesIO(base64.b64decode(encoded[1], validate=True))) as picture:
                image_width, image_height = picture.size
            caption = unescape(caption_match[1]) if caption_match else ""
            line_width = sum(9 if ord(char) > 255 else 5 for char in caption)
            caption_height = max(1, math.ceil(line_width / 440)) * 15 + 66
            if caption_height >= page_height - 80:
                raise PdfRenderError("图注过长，无法与图像在同一页清晰排版")
            width = min(440.0, (page_height - caption_height) * image_width / image_height)
            minimum = width * image_height / image_width + caption_height
            figure_width = width
            part = part.replace('width="440"', f'width="{width:.3f}"', 1)
        parts.append((part, minimum, figure_width))
    return parts


def pdf_text(data: bytes) -> tuple[int, str]:
    pymupdf = _fitz()
    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        return document.page_count, "".join(page.get_text() for page in document)
    finally:
        document.close()


def verify_pdf(data: bytes, markdown: str, *, figure_widths: list[float] | None = None) -> None:
    pages, text = pdf_text(data)
    if pages < 1:
        raise PdfRenderError("PDF 没有页面")
    if "�" in text:
        raise PdfRenderError("PDF 含无法显示的字符（字体缺字）")
    sentinel = _tail_sentinel(markdown)
    if sentinel and sentinel not in _squash(text):
        raise PdfRenderError("PDF 末尾缺少正文最后一段，疑似被截断")
    if figure_widths:
        pymupdf = _fitz()
        with pymupdf.open(stream=data, filetype="pdf") as document:
            images = [(page, item) for page in document for item in page.get_image_info()]
            if len(images) != len(figure_widths):
                raise PdfRenderError("PDF 图像数量与排版内容不一致")
            for (page, item), expected in zip(images, figure_widths, strict=True):
                rect = pymupdf.Rect(item["bbox"])
                if rect.width < expected * 0.9:
                    raise PdfRenderError("PDF 图像被意外缩小，无法按预期尺寸阅读")
                if not page.rect.contains(rect):
                    raise PdfRenderError("PDF 图像超出页面范围")


__all__ = ["PdfRenderError", "pdf_text", "render_pdf", "verify_pdf"]
