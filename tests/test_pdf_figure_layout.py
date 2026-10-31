import io

import pymupdf
import pytest
from PIL import Image

from deep_research.workbench.delivery import pdf as pdf_module
from deep_research.workbench.delivery.pdf import (
    PdfRenderError,
    pdf_layout_advisories,
    render_pdf,
    verify_pdf,
)


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


def report_pdf() -> tuple[str, bytes]:
    markdown = (
        "## 分析\n\n"
        + "DAUHST 在 KAIST 10 个场景上的平均 PSNR 为 38.36 dB，比 MST 高 1.2 dB [1]。" * 8
        + "\n\n重建写作 $\hat{x} = \Phi^\top y$，其中 $x_i$ 为第 $i$ 个波段 [1]。\n\n"
        "| 方法 | PSNR (dB) | 说明 |\n| --- | --- | --- |\n"
        "| MST-L | 35.18 | 掩膜引导的光谱自注意力 |\n| DAUHST | 38.36 | 深度展开，九个阶段 |\n\n"
        "## 局限\n\n上述数值均来自仿真数据 [1]。\n"
    )
    return markdown, render_pdf(markdown, title="重建方法对比")


def test_rendered_pdf_embeds_only_a_font_subset():
    _, pdf = report_pdf()
    document = pymupdf.open(stream=pdf, filetype="pdf")
    fonts = {font[0] for page in document for font in page.get_fonts(full=True)}
    sizes = [len(document.extract_font(xref)[3] or b"") for xref in fonts]
    assert sizes and max(sizes) <= pdf_module._FULL_FONT_BYTES and len(pdf) < 2 * 2**20


def test_full_fonts_left_by_native_subsetting_fall_back_to_fonttools():
    class Program(bytes):
        def __new__(cls, size: int):  # type: ignore[no-untyped-def]
            program = super().__new__(cls, b"x")
            program.size = size
            return program

        def __len__(self) -> int:
            return self.size

    class Page:
        def get_fonts(self, full=False):  # type: ignore[no-untyped-def]
            return [(7, "otf", "Type0", "Noto Serif CJK SC Regular", "F1", "Identity-H", 0)]

    class Document:
        def __init__(self, sizes: list[int]) -> None:
            self.calls: list[dict] = []
            self.sizes = sizes

        def __iter__(self):  # type: ignore[no-untyped-def]
            return iter([Page()])

        def subset_fonts(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
            self.calls.append(kwargs)

        def extract_font(self, xref: int):  # type: ignore[no-untyped-def]
            return ("Noto Serif CJK SC Regular", "otf", "Type0", Program(self.sizes[-1]))

    broken = Document([24 * 2**20])
    pdf_module._subset_fonts(broken)
    assert broken.calls == [{}, {"fallback": True}]
    working = Document([300_000])
    pdf_module._subset_fonts(working)
    assert working.calls == [{}]


def test_layout_advisories_stay_silent_on_a_rendered_report():
    _, pdf = report_pdf()
    assert pdf_layout_advisories(pdf) == []


def test_layout_advisories_find_missing_glyphs_overlaps_and_stacked_characters():
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_font(fontname="nocjk", fontbuffer=pymupdf.Font("helv").buffer)
        page.insert_text((72, 100), "神经算子重建光谱", fontname="nocjk", fontsize=12)
        page.insert_text((72, 180), "Overlap label", fontname="helv", fontsize=12)
        page.insert_text((74, 182), "Second label", fontname="helv", fontsize=12)
        for index, char in enumerate("神经算子"):
            page.insert_text((300, 220 + 14 * index), char, fontname="china-s", fontsize=12)
        advisories = pdf_layout_advisories(document.tobytes())
    assert any("无法识别的字符" in item for item in advisories)
    assert any("文字互相重叠" in item and "Overlap" in item for item in advisories)
    assert any("逐字换行" in item and "神经算子" in item for item in advisories)


@pytest.mark.parametrize(
    ("signature", "expected"), [("box", "渲染成同一形状"), ("", "渲染为空白")]
)
def test_distinct_characters_with_one_shape_are_reported(monkeypatch, signature, expected):
    monkeypatch.setattr(pdf_module, "_ink_signature", lambda *args: signature)
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 100), "神经算子", fontname="china-s", fontsize=12)
        advisories = pdf_layout_advisories(document.tobytes())
    assert any(expected in item for item in advisories)


def test_consistency_gate_reports_layout_advice_without_failing(monkeypatch):
    from deep_research.workbench.gate_classification import classify_gates
    from deep_research.workbench.gates import consistency_gate

    markdown, pdf = report_pdf()
    advice = ["PDF 有 1 处文字互相重叠"]
    monkeypatch.setattr(pdf_module, "pdf_layout_advisories", lambda data: advice)
    gate = consistency_gate(markdown, {"report.pdf": pdf})
    classify_gates([gate])
    assert gate.status == "pass" and gate.blocking_issues == []
    assert gate.advisories == ["（建议）PDF 有 1 处文字互相重叠"]

    def broken(data):
        raise RuntimeError("raster")

    monkeypatch.setattr(pdf_module, "pdf_layout_advisories", broken)
    gate = consistency_gate(markdown, {"report.pdf": pdf})
    assert gate.status == "pass"
    assert gate.issues == ["（建议）PDF 版面建议检查未完成：RuntimeError"]
