"""Synthetic PDF layout regressions derived from the DAUHST section-boundary audit."""

from __future__ import annotations

import pymupdf
import pytest

from deep_research.tools.oa_pdf_fulltext import parse_oa_pdf, select_pdf_sections


def _line(page, text, y, *, x=50, size=11, bold=False):
    page.insert_text((x, y), text, fontsize=size, fontname="hebo" if bold else "helv")


def _heading(page, number, title, y, *, size=12):
    _line(page, number, y, size=size, bold=True)
    _line(page, title, y, x=80, size=size, bold=True)


def section_pdf():
    with pymupdf.open() as pdf:
        first = pdf.new_page()
        _line(first, "Abstract", 50, size=12, bold=True)
        _line(first, "Z_ABSTRACT_END", 75)
        _heading(first, "1", "Introduction", 115)
        _line(first, "Z_INTRO_END", 140)
        _heading(first, "2", "Proposed Method", 180)
        _line(first, "Z_METHOD_ROOT_END", 205)
        _heading(first, "2.1", "Degradation Model", 245, size=11)
        _line(first, "MODEL_END", 270)
        _heading(first, "2.2", "Unfolding Framework", 310, size=11)
        _line(first, "FRAMEWORK_END", 335)
        second = pdf.new_page()
        _heading(second, "2.3", "Half-Shuffle Transformer", 50, size=11)
        _line(second, "MODULE_END", 75)
        _heading(second, "3", "Experiment", 115)
        _line(second, "Z_EXPERIMENT_START", 140)
        for x, label in [(50, "Method"), (160, "PSNR"), (240, "SSIM")]:
            _line(second, label, 180, x=x, size=7)
        for y, label in [(195, "A"), (210, "B")]:
            for x, cell in [(50, label), (160, "30.25"), (240, "0.912")]:
                _line(second, cell, y, x=x, size=7)
        _line(second, "Z_EXPERIMENT_END", 250)
        _heading(second, "4", "Conclusion", 290)
        _line(second, "Z_CONCLUSION_END", 315)
        _line(second, "References", 355, size=12, bold=True)
        _line(second, "[1] A. Author. REFERENCE_ONLY.", 380)
        return pdf.tobytes()


def test_numbered_custom_titles_and_subsections_do_not_merge_into_the_introduction():
    parsed = parse_oa_pdf(section_pdf())
    titles = [s.title for s in parsed.sections]
    assert titles == [
        "Abstract", "1 Introduction", "2 Proposed Method", "2.1 Degradation Model",
        "2.2 Unfolding Framework", "2.3 Half-Shuffle Transformer", "3 Experiment",
        "4 Conclusion", "References",
    ]
    introduction = next(s for s in parsed.sections if s.canonical == "introduction")
    assert introduction.text == "Z_INTRO_END"
    method = next(s for s in parsed.sections if s.title == "2 Proposed Method")
    assert method.canonical == "method" and method.text == "Z_METHOD_ROOT_END"
    assert all(marker in parsed.text for marker in ["ABSTRACT_END", "MODULE_END", "REFERENCE_ONLY"])


def test_required_method_keeps_all_numbered_children_without_experiment_table_rows():
    parsed = parse_oa_pdf(section_pdf())
    selected = select_pdf_sections(parsed, "", required={"method"}, max_chars=1)
    assert [s.title for s in selected] == [
        "2 Proposed Method", "2.1 Degradation Model", "2.2 Unfolding Framework",
        "2.3 Half-Shuffle Transformer",
    ]
    assert "MODULE_END" in selected[-1].text
    assert all("30.25" not in s.text and "REFERENCE_ONLY" not in s.text for s in selected)


def test_references_are_preserved_separately_from_the_conclusion():
    parsed = parse_oa_pdf(section_pdf())
    conclusion = next(s for s in parsed.sections if s.canonical == "conclusion")
    assert conclusion.text == "Z_CONCLUSION_END"
    references = next(s for s in parsed.sections if s.title == "References")
    assert references.canonical == "references" and "REFERENCE_ONLY" in references.text
    assert references.page_start == references.page_end == 1


@pytest.mark.parametrize("size,bold", [(7, False), (11, False), (11, True)])
def test_table_column_labels_are_not_section_boundaries_even_in_body_size_or_bold(size, bold):
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        _heading(page, "3", "Results", 50)
        _line(page, "Table 1: Comparisons", 85)
        for y, cells in [(110, ["Method", "Accuracy"]), (130, ["A", "0.9"]), (150, ["B", "0.8"])]:
            for x, cell in zip([50, 220], cells, strict=True):
                _line(page, cell, y, x=x, size=size, bold=bold if y == 110 else False)
        _line(page, "TABLE_END", 180)
        _heading(page, "4", "Conclusion", 215)
        _line(page, "END", 240)
        parsed = parse_oa_pdf(pdf.tobytes())
    assert [s.title for s in parsed.sections] == ["3 Results", "4 Conclusion"]
    assert "Method" in parsed.sections[0].text and "0.8" in parsed.sections[0].text


def test_equations_numbered_prose_and_reference_entries_do_not_become_custom_headings():
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        _heading(page, "2", "Methods", 50)
        for y, text in [(80, "2 x = y + 1"), (100, "2. We used the same samples."),
                        (120, "Results show a difference."), (140, "BODY_END")]:
            _line(page, text, y)
        _line(page, "References", 185, size=12, bold=True)
        _line(page, "1. Methods for inference. A. Author. 2020.", 210)
        _line(page, "2. Results on validation. B. Author. 2021.", 230)
        parsed = parse_oa_pdf(pdf.tobytes())
    assert [s.title for s in parsed.sections] == ["2 Methods", "References"]
    assert "BODY_END" in parsed.sections[0].text
    assert "2021" in parsed.sections[1].text


def test_text_only_heading_fallback_recognizes_numbered_titles_and_chinese_references():
    from deep_research.tools.oa_pdf_fulltext import _sections_from_pages

    sections = _sections_from_pages([
        "1 Introduction\nZINTRO\n2 Proposed Method\nZMETHOD\n2.1 Model Architecture\nCHILD\n"
        "3 结论\n结论文本\n参考文献\n[1] 参考来源"
    ])
    assert [s.title for s in sections] == [
        "1 Introduction", "2 Proposed Method", "2.1 Model Architecture", "3 结论", "参考文献",
    ]
    assert sections[-1].canonical == "references"


def test_page_separator_does_not_extend_a_section_into_the_next_page():
    from deep_research.tools.oa_pdf_fulltext import _sections_from_pages

    sections = _sections_from_pages(["1 Introduction\nintro tail", "2 Methods\nmethod tail"])
    assert sections[0].page_start == sections[0].page_end == 0
    assert sections[1].page_start == sections[1].page_end == 1


def test_required_roman_numbered_section_stops_at_an_unknown_sibling_heading():
    from deep_research.tools.oa_pdf_fulltext import PdfDocument, PdfSection

    sections = (
        PdfSection("II Methods", "root", 0, _kind="method"),
        PdfSection("II.1 Network Design", "child", 1, _kind="other"),
        PdfSection("III Ablation Study", "sibling", 2, _kind="other"),
    )
    selected = select_pdf_sections(PdfDocument(text="", sections=sections), "", 1, {"method"})
    assert [s.index for s in selected] == [0, 1]


def test_parallel_column_headings_are_not_table_headers_without_tabular_rows():
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        _line(page, "Methods", 50, size=12, bold=True)
        _line(page, "Method prose continues here.", 80)
        _line(page, "Results", 50, x=330, size=12, bold=True)
        _line(page, "Result prose continues here.", 80, x=330)
        parsed = parse_oa_pdf(pdf.tobytes())
    assert [s.title for s in parsed.sections] == ["Methods", "Results"]


async def test_fulltext_fetcher_returns_required_method_children_with_stable_source_locations():
    import httpx

    from deep_research.models import ScholarlyMetadata, Source
    from deep_research.tools.oa_pdf_fulltext import OaPdfFetcher

    class Fetcher(OaPdfFetcher):
        async def fetch(self, url):
            return section_pdf()

    source = Source(url="https://doi.org/10.1234/test", scholarly=ScholarlyMetadata(
        oa_pdf_url="https://example.org/test.pdf",
    ))
    async with httpx.AsyncClient() as client:
        fetcher = Fetcher(client=client)
        selected = await fetcher.sections(source, "", max_chars=1, required={"method"})
    assert len(selected) == 5  # Abstract plus the method and its three subsections.
    assert any("MODULE_END" in item.content for item in selected)
    assert all(
        "REFERENCE_ONLY" not in item.content and "30.25" not in item.content for item in selected
    )
    assert len({item.url for item in selected}) == 5


@pytest.mark.parametrize("via_fetcher", [False, True])
async def test_full_reading_keeps_new_custom_root_sections_whole(via_fetcher):
    import httpx

    from deep_research.models import ScholarlyMetadata, Source
    from deep_research.tools.oa_pdf_fulltext import OaPdfFetcher

    with pymupdf.open() as pdf:
        page = pdf.new_page()
        _heading(page, "1", "Methods", 50)
        _line(page, "Method content.", 75)
        _heading(page, "2", "Theoretical Bounds", 110)
        _line(page, "A necessary bound for interpreting the method. CUSTOM_ROOT_END", 135)
        _line(page, "References", 175, size=12, bold=True)
        _line(page, "[1] Reference text.", 200)
        raw = pdf.tobytes()
    if via_fetcher:
        class Fetcher(OaPdfFetcher):
            async def fetch(self, url):
                return raw

        source = Source(url="https://doi.org/10.1234/test", scholarly=ScholarlyMetadata(
            oa_pdf_url="https://example.org/test.pdf",
        ))
        async with httpx.AsyncClient() as client:
            selected = await Fetcher(client=client).sections(source, "", max_chars=1, required=True)
        assert any("CUSTOM_ROOT_END" in item.content for item in selected)
    else:
        selected = select_pdf_sections(parse_oa_pdf(raw), "", max_chars=1, required=True)
        assert [s.title for s in selected] == ["1 Methods", "2 Theoretical Bounds"]
        assert selected[-1].text.endswith("CUSTOM_ROOT_END")
