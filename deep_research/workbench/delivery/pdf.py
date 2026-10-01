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
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from html import unescape

from .html import pdf_html
from .math import MathAsset
from .math_pdf import place_vector_math
from .pdf_tables import (
    PdfTable,
    plain_html,
    table_from_html,
    take_table_prefix,
    take_trailing_heading,
)


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
        inlines = block.inlines
        if block.kind == "list":
            inlines = [inline for item in block.items for inline in item.inlines]
        elif block.kind == "table":
            inlines = [inline for row in block.rows for cell in row for inline in cell]
        # Vector equations carry ActualText in separate PDF streams; their
        # extraction order need not match their visual position in a sentence.
        # Formula completeness is checked independently during vector placement.
        prose = "".join(inline.text for inline in inlines if not inline.math)
        text = re.sub(r"[\s\[\]\d.,，。:：;；|*`#>-]+", "", prose)
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
    math_assets: dict[bytes, MathAsset] = {}
    body, css = pdf_html(markdown, title=title, meta=meta, images=images, math_assets=math_assets)
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
    table_fragments: list[tuple[int, str, float, float]] = []
    table_cells: list[tuple[int, tuple[float, ...]]] = []

    def next_page():  # type: ignore[no-untyped-def]
        nonlocal pages, device, cursor
        if device is not None:
            writer.end_page()
            device = None
        pages += 1
        if pages > 400:
            raise PdfRenderError("PDF 页数超过 400 页上限，疑似排版死循环")
        device = writer.begin_page(mediabox)
        cursor = where.y0

    # Story shrinks images to the *remaining* page space instead of moving them
    # intact. Reserve a whole figure and caption before handing it to Story.
    # This also avoids relying on unsupported CSS page-break-inside behavior.
    def fit(html: str, top: float):  # type: ignore[no-untyped-def]
        story = pymupdf.Story(html=html, user_css=user_css, archive=archive)
        more, filled = story.place(pymupdf.Rect(where.x0, top, where.x1, where.y1))
        rect = pymupdf.Rect(filled)
        sizes_valid = True

        def check_math(position):  # type: ignore[no-untyped-def]
            nonlocal sizes_valid
            if position.id and position.id.startswith("math-"):
                asset = math_assets.get(bytes.fromhex(position.id[5:]))
                placed = pymupdf.Rect(position.rect)
                if (
                    asset
                    and placed.width > 0
                    and (
                        abs(placed.width - math.ceil(asset.width)) > 0.1
                        or abs(placed.height - math.ceil(asset.height)) > 0.1
                    )
                ):
                    sizes_valid = False

        story.element_positions(check_math)
        return story, not more and rect.y1 <= where.y1 + 0.1 and sizes_valid, rect

    def draw_table(table: PdfTable) -> None:
        nonlocal cursor
        if device is None:
            next_page()
        full = table.html(table.rows)
        _, fits_page, _ = fit(full, where.y0)
        if fits_page:
            story, fits_here, rect = fit(full, cursor)
            if not fits_here:
                next_page()
                story, fits_here, rect = fit(full, cursor)
            if not fits_here:
                raise PdfRenderError("表格不能完整放入页面，未裁剪内容")
            draw_cells(story)
            table_fragments.append((pages - 1, table.header, cursor, rect.y1))
            cursor = rect.y1
            return
        if not table.rows:
            raise PdfRenderError("表格表头或表题超过一页可用高度，未裁剪内容")
        start = 0
        while start < len(table.rows):
            low, high, count = 1, len(table.rows) - start, 0
            while low <= high:
                mid = (low + high) // 2
                _, fits_here, _ = fit(
                    table.html(table.rows[start : start + mid], continued=start > 0), cursor
                )
                if fits_here:
                    count, low = mid, mid + 1
                else:
                    high = mid - 1
            if count < min(2, len(table.rows) - start) and cursor > where.y0 + 0.1:
                next_page()
                continue
            if count == 0:
                raise PdfRenderError("表格单行或表题超过一页可用高度，未裁剪内容；请拆分长单元格")
            # Do not leave a lone final row if both fragments can hold more.
            if len(table.rows) - start - count == 1 and count > 2:
                count -= 1
            story, fits_here, rect = fit(
                table.html(table.rows[start : start + count], continued=start > 0), cursor
            )
            if not fits_here:
                raise PdfRenderError("表格分页测量不一致，未交付截断表格")
            draw_cells(story)
            table_fragments.append((pages - 1, table.header, cursor, rect.y1))
            cursor = rect.y1
            start += count
            if start < len(table.rows):
                next_page()

    def draw_cells(story):  # type: ignore[no-untyped-def]
        def record(position):  # type: ignore[no-untyped-def]
            if position.id and position.id.startswith("dr-cell-") and position.open_close == 1:
                table_cells.append((pages - 1, tuple(position.rect)))

        story.element_positions(record)
        story.draw(device)

    def heading_height(html: str) -> float:
        _, fits_page, rect = fit(html, where.y0)
        if not fits_page:
            raise PdfRenderError("图表标题超过一页可用高度")
        return float(rect.height)

    try:
        for part in _layout_parts(body, where.height, where.width, heading_height):
            if part.table is not None:
                draw_table(part.table)
                continue
            if part.figure_width is not None:
                figure_widths.append(part.figure_width)
            if device is None or where.y1 - cursor < max(part.minimum_height, 36):
                next_page()
            story = pymupdf.Story(html=part.html, user_css=user_css, archive=archive)
            while True:
                more, filled = story.place(pymupdf.Rect(where.x0, cursor, where.x1, where.y1))
                story.draw(device)
                if not more:
                    cursor = pymupdf.Rect(filled).y1
                    break
                next_page()
    finally:
        if device is not None:
            writer.end_page()
        writer.close()
    data = stream.getvalue()
    # 页码：在已排版的 PDF 上逐页写页脚
    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        place_vector_math(document, math_assets)
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
    verify_pdf(
        data,
        markdown,
        figure_widths=figure_widths,
        table_fragments=table_fragments,
        table_cells=table_cells,
    )
    return data


@dataclass
class LayoutPart:
    html: str
    minimum_height: float = 0.0
    figure_width: float | None = None
    table: PdfTable | None = None


def _layout_parts(
    body: str, page_height: float, page_width: float, heading_height: Callable[[str], float]
) -> list[LayoutPart]:
    """Split renderer-owned tables/figures; external HTML has already been escaped."""
    from PIL import Image

    parts: list[LayoutPart] = []
    pattern = (
        r'(<div class="figure">.*?</div>|<div class="equation">.*?</div>|<table>.*?</table>'
        r'|<p[^>]*>(?:(?!</p>).)*class="math-image"(?:(?!</p>).)*</p>)'
    )
    for part in re.split(pattern, body, flags=re.S):
        if not part.strip():
            continue
        minimum = 0.0
        figure_width = None
        if (
            part.startswith('<div class="equation">')
            or 'class="math-image"' in part
            and part.startswith("<p")
        ):
            parts.append(LayoutPart(part, minimum_height=heading_height(part)))
            continue
        if part.startswith("<table>"):
            table = table_from_html(part, page_width)
            if parts and parts[-1].table is None and parts[-1].figure_width is None:
                parts[-1].html, table.caption, table.lead = take_table_prefix(parts[-1].html)
                if not parts[-1].html.strip():
                    parts.pop()
            parts.append(LayoutPart("", table=table))
            continue
        if part.startswith('<div class="figure">'):
            lead = ""
            if parts and parts[-1].table is None and parts[-1].figure_width is None:
                parts[-1].html, lead = take_trailing_heading(parts[-1].html)
                if not parts[-1].html.strip():
                    parts.pop()
            lead_height = heading_height(lead) if lead else 0.0
            encoded = re.search(r'src="data:image/[^;]+;base64,([^"]+)"', part)
            caption_match = re.search(r'<p class="caption">(.*?)</p>', part, flags=re.S)
            if encoded is None:
                raise PdfRenderError("图像资产无法读取")
            with Image.open(io.BytesIO(base64.b64decode(encoded[1], validate=True))) as picture:
                image_width, image_height = picture.size
            caption = unescape(caption_match[1]) if caption_match else ""
            line_width = sum(9 if ord(char) > 255 else 5 for char in caption)
            caption_height = max(1, math.ceil(line_width / 440)) * 15 + 66
            if caption_height + lead_height >= page_height - 80:
                raise PdfRenderError("图注过长，无法与图像在同一页清晰排版")
            width = min(
                440.0, (page_height - caption_height - lead_height) * image_width / image_height
            )
            minimum = width * image_height / image_width + caption_height + lead_height
            figure_width = width
            part = part.replace('width="440"', f'width="{width:.3f}"', 1)
            part = lead + part
        parts.append(LayoutPart(part, minimum, figure_width))
    return parts


def pdf_text(data: bytes) -> tuple[int, str]:
    pymupdf = _fitz()
    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        return document.page_count, "".join(page.get_text() for page in document)
    finally:
        document.close()


def verify_pdf(
    data: bytes,
    markdown: str,
    *,
    figure_widths: list[float] | None = None,
    table_fragments: list[tuple[int, str, float, float]] | None = None,
    table_cells: list[tuple[int, tuple[float, ...]]] | None = None,
) -> None:
    pages, text = pdf_text(data)
    if pages < 1:
        raise PdfRenderError("PDF 没有页面")
    if "�" in text:
        raise PdfRenderError("PDF 含无法显示的字符（字体缺字）")
    sentinel = _tail_sentinel(markdown)
    if sentinel and sentinel not in _squash(text):
        raise PdfRenderError("PDF 末尾缺少正文最后一段，疑似被截断")
    if table_fragments:
        with _fitz().open(stream=data, filetype="pdf") as document:
            for page_number, header, top, bottom in table_fragments:
                page = document[page_number]
                page_text = re.sub(r"\s+", "", page.get_text())
                for cell in re.findall(r"<th[^>]*>(.*?)</th>", header, re.S):
                    if re.sub(r"\s+", "", plain_html(cell)) not in page_text:
                        raise PdfRenderError("PDF 表格续页缺少完整表头")
                for word in page.get_text("words"):
                    if top <= (word[1] + word[3]) / 2 <= bottom and (
                        word[0] < 52 or word[2] > page.rect.width - 52
                    ):
                        raise PdfRenderError("PDF 表格内容超出版心，未通过可读性检查")
    if table_cells:
        with _fitz().open(stream=data, filetype="pdf") as document:
            words = {i: page.get_text("words") for i, page in enumerate(document)}
            for page_number, box in table_cells:
                x0, y0, x1, y1 = box
                for word in words[page_number]:
                    if y0 <= (word[1] + word[3]) / 2 <= y1 and x0 - 0.5 <= word[0] < x1:
                        if word[2] > x1 + 1:
                            raise PdfRenderError("PDF 表格单元格文字越界，可能覆盖相邻列")
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
