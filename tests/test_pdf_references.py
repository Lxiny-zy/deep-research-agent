import io
import re

import pymupdf
import pytest
from docx import Document

from deep_research.workbench.delivery.docx import render_docx
from deep_research.workbench.delivery.pdf import PdfRenderError, render_pdf, verify_pdf


def test_bibliography_entries_stay_on_one_page_with_complete_content():
    entries = [
        f"[{i}] Entry-{i}. " + "Author. A detailed bilingual study 研究报告. " * 7 + f" Tail-{i}."
        for i in range(1, 32)
    ]
    markdown = "## 分析\n\n正文结论。\n\n## 参考文献\n\n" + "\n\n".join(entries)
    pdf = render_pdf(markdown, title="参考文献分页")
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        assert len(doc) > 1
        pages = [p.get_text() for p in doc]
        for i in range(1, 32):
            start = next(n for n, text in enumerate(pages) if f"Entry-{i}." in text)
            end = next(n for n, text in enumerate(pages) if f"Tail-{i}." in text)
            assert start == end
    word = Document(io.BytesIO(render_docx(markdown, title="参考文献分页")))
    paragraphs = [p for p in word.paragraphs if "Entry-" in p.text]
    assert len(paragraphs) == 31
    assert all(p.paragraph_format.keep_together for p in paragraphs)


def test_reference_longer_than_one_page_can_flow_without_truncation():
    markdown = (
        "## References\n\n[1] Start-reference. "
        + "Long reference content. " * 600
        + " End-reference."
    )
    pdf = render_pdf(markdown, title="Long entry")
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        assert len(doc) > 1
        text = "\n".join(p.get_text(clip=p.rect + (0, 0, 0, -60)) for p in doc)
        assert "Start-reference." in text and "End-reference." in text
        assert len(re.findall(r"Long\s+reference\s+content\.", text)) == 600


def test_equivalent_cjk_header_forms_pass_but_changed_words_are_rejected():
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((60, 85), "物理算子的使用方式", fontname="china-s", fontsize=12)
        data = doc.tobytes()
    verify_pdf(data, "", table_fragments=[(0, "<th>物理算子的使用方式</th>", 60, 100)])
    with pytest.raises(PdfRenderError, match="完整表头"):
        verify_pdf(data, "", table_fragments=[(0, "<th>物里算子的使用方式</th>", 60, 100)])
