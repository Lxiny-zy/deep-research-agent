import io

import pymupdf
import pytest
from PIL import Image

from deep_research.workbench.delivery.pdf import PdfRenderError, render_pdf, verify_pdf


def figure() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (800, 680), "#3983aa").save(buffer, format="PNG")
    return buffer.getvalue()


def test_page_edge_figures_keep_readable_size_instead_of_shrinking_into_remaining_space():
    paragraphs = "\n\n".join(["这里是图表之前的说明文字。" * 15] * 5)
    markdown = paragraphs + "\n\n![第一幅统计图](a.png)\n\n![第二幅统计图](b.png)\n\n最终结论。"
    pdf = render_pdf(markdown, title="图表分页回归", images={"a.png": figure(), "b.png": figure()})
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        images = [(page.number, item) for page in document for item in page.get_image_info()]
        assert len(images) == 2
        assert images[0][0] != images[1][0]
        assert all(pymupdf.Rect(item["bbox"]).width >= 430 for _, item in images)


def test_pdf_quality_check_rejects_accidentally_miniaturized_figures():
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_image(pymupdf.Rect(60, 700, 80, 720), stream=figure())
        data = document.tobytes()
    with pytest.raises(PdfRenderError, match="意外缩小"):
        verify_pdf(data, "", figure_widths=[440])
