from __future__ import annotations

import re

import pymupdf
import pytest

from deep_research.workbench.delivery.pdf import PdfRenderError, render_pdf


def test_short_table_and_caption_move_together_instead_of_leaving_a_single_row():
    intro = "\n\n".join(["这是用于占位的前置段落，以便表格接近页面底部。"] * 18)
    table = "\n\n## 配对结果\n\n表 1 配对比较\n\n| Identifier | Value |\n|---|---|\n"
    table += "\n".join(f"| ROW{i:03} | {i}.25 |" for i in range(10))
    pdf = render_pdf(intro + table, title="表格分页验收")
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        pages = [page.get_text() for page in document]
    table_pages = [text for text in pages if "ROW000" in text or "ROW009" in text]
    assert len(table_pages) == 1
    assert "表 1 配对比较" in table_pages[0] and "配对结果" in table_pages[0]
    assert all(f"ROW{i:03}" in table_pages[0] for i in range(10))


def test_long_table_repeats_headers_preserves_every_row_and_keeps_column_positions():
    body = "表 2 Long measurements\n\n| Identifier | Mean | Description |\n|---|---|---|\n"
    body += "\n".join(
        f"| ROW{i:03} | {i}.25 | several words describing this measurement and its conditions |"
        for i in range(110)
    )
    pdf = render_pdf(body, title="Long table")
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        texts = [page.get_text() for page in document]
        assert len(texts) >= 3
        assert all(
            "Identifier" in text and "Mean" in text and "Description" in text for text in texts
        )
        assert all("续" in text for text in texts[1:])
        assert re.findall(r"ROW\d{3}", "\n".join(texts)) == [f"ROW{i:03}" for i in range(110)]
        x_positions = [
            next(w[0] for w in page.get_text("words") if w[4] == "Mean") for page in document
        ]
        assert max(x_positions) - min(x_positions) < 1
        for page in document:
            assert all(w[0] >= 52 and w[2] <= page.rect.width - 52 for w in page.get_text("words"))


def test_unrenderable_row_is_reported_instead_of_clipped_or_looped():
    body = "| Identifier | Description |\n|---|---|\n| BIG | " + "很长的单元格内容。" * 1000 + " |"
    with pytest.raises(PdfRenderError, match="单行"):
        render_pdf(body, title="长单元格")


def test_long_identifiers_do_not_overlap_adjacent_numeric_cells(monkeypatch):
    from deep_research.workbench.delivery import pdf as module

    body = (
        "| Variable | n | Mean | Std | Median | Min | Max |\n|---|---|---|---|---|---|---|\n"
        "| method_a_psnr_db | 12 | 31.3 | 0.7211 | 31.3 | 30.2 | 32.4 |\n"
        "| method_b_psnr_db | 12 | 32.4 | 0.716 | 32.35 | 31.3 | 33.4 |"
    )
    data = render_pdf(body, title="Column widths")
    with pymupdf.open(stream=data, filetype="pdf") as document:
        words = document[0].get_text("words")
        for label in ("method_a_psnr_db", "method_b_psnr_db"):
            identifier = next(word for word in words if word[4] == label)
            value = next(
                word for word in words if word[4] == "12" and abs(word[1] - identifier[1]) < 2
            )
            assert identifier[2] + 2 < value[0]

    original = module.table_from_html

    def broken_widths(html, width):
        table = original(html, width)
        table.header = re.sub(r'width:[^";]+', "width:1%", table.header)
        table.rows = [re.sub(r'width:[^";]+', "width:1%", row) for row in table.rows]
        return table

    monkeypatch.setattr(module, "table_from_html", broken_widths)
    with pytest.raises(PdfRenderError, match="单元格"):
        render_pdf(body, title="Injected overlapping columns")


def test_figure_heading_moves_with_its_image():
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (600, 500), "#4477aa").save(buffer, format="PNG")
    intro = "\n\n".join(["前置内容用于验证插图标题不能孤立在页末。"] * 18)
    body = intro + "\n\n## 实验图表\n\n![实验图示](plot.png)"
    data = render_pdf(body, title="图标题与插图", images={"plot.png": buffer.getvalue()})
    with pymupdf.open(stream=data, filetype="pdf") as document:
        image_page = next(page for page in document if page.get_images())
        assert "实验图表" in image_page.get_text()
        assert all("实验图表" not in page.get_text() for page in document if not page.get_images())
