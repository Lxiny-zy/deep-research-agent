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
import hashlib
import io
import math
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from html import unescape
from typing import Any

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
                from .pdf_fonts import story_font

                data, extension = story_font(path, name)
                archive.add(data, "cjk" + extension)
                css = f"@font-face{{font-family:cjk;src:url(cjk{extension});}}"
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
        text = re.sub(r"[\s\[\]\d.,，。:：;；|*`#>-]+", "", unicodedata.normalize("NFC", prose))
        if len(text) >= 4:
            return text[-6:]
    return ""


def _squash(text: str) -> str:
    return re.sub(r"[\s\[\]\d.,，。:：;；|*`#>-]+", "", unicodedata.normalize("NFC", text))


# 子集化后的 CJK 字体通常不到 2 MB；仍超过这个大小，说明整套字体被原样嵌入
_FULL_FONT_BYTES = 4 * 2**20


def _subset_fonts(document) -> None:  # type: ignore[no-untyped-def]
    """字体子集化：整套 CJK 字体动辄 10–25 MB，子集后只保留用到的字形。

    MuPDF 自带的子集化遇到 Noto CJK 的 CFF 字形会报「Reserved charstring byte」，
    但不抛错、原样保留整套字体，单页 PDF 也有 20 MB；这时改用 fontTools 子集化。
    """
    document.subset_fonts()
    fonts = {font[0] for page in document for font in page.get_fonts(full=True)}
    if any(len(document.extract_font(xref)[3] or b"") > _FULL_FONT_BYTES for xref in fonts):
        document.subset_fonts(fallback=True)


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
            if part.keep_together and fit(part.html, where.y0)[1]:
                story, fits_here, rect = fit(part.html, cursor)
                if not fits_here:
                    next_page()
                    story, fits_here, rect = fit(part.html, cursor)
                if not fits_here:
                    raise PdfRenderError("内容块无法完整放入页面")
                story.draw(device)
                cursor = rect.y1
                continue
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
        _subset_fonts(document)
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
    keep_together: bool = False


def _math_lists(body: str) -> list[tuple[int, int, list[str]]]:
    """Split renderer-owned lists at root items, retaining nested children and numbering."""
    spans: list[tuple[int, int, list[str]]] = []
    depth = 0
    start = 0
    opening = ""
    item_starts: list[int] = []
    for match in re.finditer(r"</?(?:ol|ul)>|<li>.*?</li>", body, re.S):
        token = match[0]
        if token.startswith("<li>"):
            if depth == 1:
                item_starts.append(match.start())
            continue
        if not token.startswith("</"):
            if depth == 0:
                start, opening, item_starts = match.start(), token, []
            depth += 1
            continue
        depth -= 1
        if depth or 'class="math-image"' not in body[start:match.end()]:
            continue
        ends = [*item_starts[1:], match.start()]
        items = [
            (f'<ol start="{index}">' if opening == "<ol>" else opening)
            + body[first:last] + token
            for index, (first, last) in enumerate(zip(item_starts, ends, strict=True), 1)
        ]
        spans.append((start, match.end(), items))
    return spans


def _layout_parts(
    body: str, page_height: float, page_width: float, heading_height: Callable[[str], float]
) -> list[LayoutPart]:
    """Split renderer-owned tables/figures; external HTML has already been escaped."""
    from PIL import Image

    parts: list[LayoutPart] = []
    lists = _math_lists(body)
    if lists:
        offset = 0
        for start, end, items in lists:
            prefix, lead = take_trailing_heading(body[offset:start])
            parts.extend(_layout_parts(prefix, page_height, page_width, heading_height))
            for index, list_html in enumerate(items):
                html = (lead if index == 0 else "") + list_html
                parts.append(LayoutPart(
                    html, minimum_height=heading_height(html), keep_together=True,
                ))
            offset = end
        parts.extend(_layout_parts(body[offset:], page_height, page_width, heading_height))
        return parts
    pattern = (
        r'(<div class="figure">.*?</div>|<div class="equation">.*?</div>|<table>.*?</table>'
        r'|<p class="reference">.*?</p>'
        r'|<p[^>]*>(?:(?!</p>).)*class="math-image"(?:(?!</p>).)*</p>)'
    )
    for part in re.split(pattern, body, flags=re.S):
        if not part.strip():
            continue
        minimum = 0.0
        figure_width = None
        if part.startswith('<p class="reference">'):
            lead = ""
            if parts and parts[-1].table is None and parts[-1].figure_width is None:
                parts[-1].html, lead = take_trailing_heading(parts[-1].html)
                if not parts[-1].html.strip():
                    parts.pop()
            parts.append(LayoutPart(lead + part, keep_together=True))
            continue
        if (
            part.startswith('<div class="equation">')
            or 'class="math-image"' in part
            and part.startswith("<p")
        ):
            if parts and parts[-1].table is None and parts[-1].figure_width is None:
                parts[-1].html, lead = take_trailing_heading(parts[-1].html)
                if not parts[-1].html.strip():
                    parts.pop()
                part = lead + part
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
    # Story does not reliably honor page-break-after:avoid. Give each heading
    # group its own flow and reserve room for the start of its following prose.
    # Tables, equations and figures already carry their measured heading lead.
    flowed: list[LayoutPart] = []
    heading = r"<h[1-6](?:\s[^>]*)?>.*?</h[1-6]>\s*"
    for item in parts:
        if (
            item.table is not None
            or item.figure_width is not None
            or item.minimum_height
            or item.keep_together
        ):
            flowed.append(item)
            continue
        pending = ""
        for fragment in re.split(r"(?=<h[1-6](?:\s|>))", item.html):
            fragment = pending + fragment
            pending = ""
            if not fragment.strip():
                continue
            heading_match = re.match(rf"(?:\s*{heading})+", fragment, re.S)
            if heading_match and not fragment[heading_match.end() :].strip():
                pending = fragment
                continue
            # Two 10.5 pt body lines at 1.6 line height, plus paragraph spacing.
            minimum = (
                min(page_height, heading_height(heading_match[0]) + 40) if heading_match else 0.0
            )
            flowed.append(LayoutPart(fragment, minimum_height=minimum))
        if pending:
            flowed.append(LayoutPart(pending, minimum_height=heading_height(pending)))
    return flowed


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
                page_text = re.sub(r"\s+", "", unicodedata.normalize("NFC", page.get_text()))
                for cell in re.findall(r"<th[^>]*>(.*?)</th>", header, re.S):
                    if (
                        re.sub(r"\s+", "", unicodedata.normalize("NFC", plain_html(cell)))
                        not in page_text
                    ):
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


_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
# 词框需有两个以上字母、数字或汉字才参与压字判断：公式重音、上下标等单个符号本就叠放
_WORDLIKE = re.compile(r"[\w㐀-鿿]{2}")


def _ink_signature(page: Any, bbox: Any, pymupdf: Any) -> str:
    """字形墨迹签名：放大渲染、二值化并裁到墨迹外框，与字在页面上的位置无关。空白返回空串。"""
    pix = page.get_pixmap(
        clip=pymupdf.Rect(bbox), matrix=pymupdf.Matrix(3, 3), colorspace=pymupdf.csGRAY, alpha=False
    )
    rows = [pix.samples[y * pix.stride : y * pix.stride + pix.width] for y in range(pix.height)]
    inked = [y for y, row in enumerate(rows) if min(row, default=255) < 128]
    if not inked:
        return ""
    band = rows[inked[0] : inked[-1] + 1]
    columns = [x for x in range(pix.width) if any(row[x] < 128 for row in band)]
    bits = "\n".join(
        "".join("1" if row[x] < 128 else "0" for x in range(columns[0], columns[-1] + 1))
        for row in band
    )
    return hashlib.sha1(bits.encode()).hexdigest()


def _stacked_runs(lines: list[dict[str, Any]]) -> list[str]:
    """同一文本块里连续三行以上各只有一个汉字且左缘对齐：列太窄，中文被逐字折行。"""
    runs: list[str] = []
    run: list[str] = []
    left: float | None = None
    for line in lines:
        text = "".join(char["c"] for span in line["spans"] for char in span["chars"]).strip()
        single = len(text) == 1 and bool(_HAN.match(text))
        x0 = float(line["bbox"][0])
        if single and run and left is not None and abs(x0 - left) < 2:
            run.append(text)
        else:
            if len(run) >= 3:
                runs.append("".join(run))
            run = [text] if single else []
        left = x0 if single else None
    if len(run) >= 3:
        runs.append("".join(run))
    return runs


def _collisions(words: list[Any], page_number: int, limit: int = 5) -> list[str]:
    """不同文字行的词框相交达较小框面积的三成；同字同位的重复绘制（模拟加粗）不算。"""
    found: list[str] = []
    ordered = sorted(
        (word for word in words if _WORDLIKE.search(str(word[4]))), key=lambda word: word[1]
    )
    for index, first in enumerate(ordered):
        for second in ordered[index + 1 :]:
            if second[1] >= first[3]:
                break
            if (first[5], first[6]) == (second[5], second[6]):
                continue
            width = min(first[2], second[2]) - max(first[0], second[0])
            height = min(first[3], second[3]) - max(first[1], second[1])
            smaller = min(
                (first[2] - first[0]) * (first[3] - first[1]),
                (second[2] - second[0]) * (second[3] - second[1]),
            )
            if width <= 0 or height <= 0 or smaller <= 0 or width * height < 0.3 * smaller:
                continue
            if first[4] == second[4] and width * height > 0.9 * smaller:
                continue
            found.append(f"第 {page_number} 页「{first[4]}」与「{second[4]}」")
            if len(found) >= limit:
                return found
    return found


def pdf_layout_advisories(data: bytes, *, glyph_sample: int = 240) -> list[str]:
    """文字层看起来正常、读者却看不清的问题：缺字形、压字、窄列逐字换行。只作建议。

    替换字符、末段截断、表格越界与图像尺寸由 ``verify_pdf`` 硬判。这里的问题在抽取文本里
    往往码位齐全，要靠渲染或坐标计算：不同汉字渲染成同一形状（缺字方框）或空白、
    两段文字的词框相交、窄列把中文压成一行一个字。
    """
    pymupdf = _fitz()
    unreadable = 0
    blank: set[str] = set()
    shapes: dict[str, set[str]] = {}
    sampled: set[str] = set()
    collisions: list[str] = []
    stacked: list[str] = []
    with pymupdf.open(stream=data, filetype="pdf") as document:
        for number, page in enumerate(document, start=1):
            for block in page.get_text("rawdict")["blocks"]:
                lines = block.get("lines", [])
                stacked += [f"第 {number} 页「{run}」" for run in _stacked_runs(lines)]
                for char in (c for line in lines for span in line["spans"] for c in span["chars"]):
                    value = char["c"]
                    if value and ord(value[0]) < 32 and value not in "\t\n\r":
                        unreadable += 1
                        continue
                    key = unicodedata.normalize("NFKC", value)
                    if not _HAN.match(value) or key in sampled or len(sampled) >= glyph_sample:
                        continue
                    sampled.add(key)
                    signature = _ink_signature(page, char["bbox"], pymupdf)
                    if signature:
                        shapes.setdefault(signature, set()).add(key)
                    else:
                        blank.add(key)
            if len(collisions) < 5:
                collisions += _collisions(page.get_text("words"), number, 5 - len(collisions))
    advisories: list[str] = []
    if unreadable:
        advisories.append(f"PDF 文字层有 {unreadable} 个无法识别的字符，对应字形可能缺失")
    if len(blank) >= 3:
        sample = "".join(sorted(blank)[:5])
        advisories.append(f"PDF 中 {len(blank)} 个汉字渲染为空白（如「{sample}」），疑似字体缺字")
    same = sorted((chars for chars in shapes.values() if len(chars) >= 3), key=len, reverse=True)
    if same:
        sample = "".join(sorted(same[0])[:5])
        advisories.append(
            f"PDF 中 {sum(map(len, same))} 个不同汉字渲染成同一形状（如「{sample}」），疑似缺字方框"
        )
    if collisions:
        advisories.append(f"PDF 有 {len(collisions)} 处文字互相重叠，如{collisions[0]}")
    if stacked:
        advisories.append(f"PDF 有 {len(stacked)} 处窄列中文逐字换行，如{stacked[0]}")
    return advisories


__all__ = ["PdfRenderError", "pdf_layout_advisories", "pdf_text", "render_pdf", "verify_pdf"]
