from __future__ import annotations

import importlib

import httpx
import pytest

from deep_research.models import Source
from deep_research.tools.oa_pdf_fulltext import (
    OaPdfFetcher,
    OaPdfFetchError,
    OaPdfLimits,
    OaPdfParseError,
    PdfDocument,
    PdfSection,
    parse_oa_pdf,
    select_pdf_sections,
)
from deep_research.tools.openalex import OpenAlexSearch


def _pdf_bytes() -> bytes:
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    for lines in (
        ["Abstract", "PSNR 38.36 dB"],
        ["1 Introduction", "Context"],
        ["2 Methods", "Protocol: 31 bands"],
        ["3 Results", "Accuracy 99%", "Table 1: 38.36 dB"],
        ["Conclusion", "The method is useful"],
    ):
        page = document.new_page()
        for line_number, line in enumerate(lines):
            page.insert_text((50, 60 + line_number * 18), line)
    return document.tobytes()


def test_parse_pdf_preserves_numbers_and_classifies_sections() -> None:
    document = parse_oa_pdf(_pdf_bytes())

    assert document.page_count == 5
    assert [section.canonical for section in document.sections] == [
        "abstract",
        "introduction",
        "method",
        "results",
        "conclusion",
    ]
    assert "38.36 dB" in document.text
    assert "Table 1" in document.text


def test_no_heading_pdf_has_an_other_fallback() -> None:
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    page = document.new_page()
    page.insert_text((50, 60), "Unlabelled full text 42.5 dB")

    parsed = parse_oa_pdf(document.tobytes())

    assert len(parsed.sections) == 1
    assert parsed.sections[0].canonical == "other"
    assert "42.5 dB" in parsed.sections[0].text


def test_selector_is_deterministic_and_respects_required_sections() -> None:
    document = PdfDocument(
        text="",
        sections=(
            PdfSection("Introduction", "background", 0, _kind="introduction"),
            PdfSection("Methods", "protocol", 1, _kind="method"),
            PdfSection("Results", "accuracy 99%", 2, _kind="results"),
        ),
    )

    first = select_pdf_sections(document, "accuracy", required=["method"], max_chars=1000)
    second = select_pdf_sections(document, "accuracy", required=["method"], max_chars=1000)

    assert [section.title for section in first] == ["Methods", "Results"]
    assert [section.title for section in first] == [section.title for section in second]


def test_parse_rejects_non_pdf_and_limits() -> None:
    with pytest.raises(OaPdfParseError, match="not a PDF"):
        parse_oa_pdf(b"not a pdf")
    with pytest.raises(OaPdfParseError, match="input size"):
        parse_oa_pdf(b"%PDF-1.7", OaPdfLimits(max_input_bytes=1))


def test_missing_pymupdf_is_a_typed_optional_dependency_error(monkeypatch) -> None:
    original = importlib.import_module

    def missing(name: str, package: str | None = None):
        if name == "fitz":
            raise ImportError("missing fitz")
        return original(name, package)

    monkeypatch.setattr(importlib, "import_module", missing)
    with pytest.raises(OaPdfParseError, match="PyMuPDF is not installed"):
        parse_oa_pdf(b"%PDF-1.7 fake")


@pytest.mark.asyncio
async def test_fetcher_checks_type_size_and_returns_section_sources() -> None:
    raw = _pdf_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://papers.example/one.pdf"
        return httpx.Response(
            200,
            content=raw,
            headers={"content-type": "application/pdf"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = OaPdfFetcher(client=client)
    source = Source(
        title="Paper",
        url="https://doi.org/10.1/paper",
        content="abstract fallback",
        scholarly={"oa_pdf_url": "https://papers.example/one.pdf"},
    )
    try:
        sections = await fetcher.sections(source, "accuracy", max_chars=5000, required=["results"])
    finally:
        await fetcher.aclose()

    assert sections
    assert all("dr_section=pdf-" in section.url for section in sections)
    assert all(section.scholarly is not None for section in sections)
    assert any(section.scholarly and section.scholarly.section == "results" for section in sections)
    assert len({section.url for section in sections}) == len(sections)


@pytest.mark.asyncio
async def test_fetcher_rejects_bad_status_content_type_and_private_url() -> None:
    async def run(response: httpx.Response) -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response))
        fetcher = OaPdfFetcher(client=client)
        try:
            with pytest.raises(OaPdfFetchError):
                await fetcher.fetch("https://papers.example/one.pdf")
        finally:
            await fetcher.aclose()

    await run(httpx.Response(404, content=b"%PDF-1.7"))
    await run(httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"}))

    fetcher = OaPdfFetcher(
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    )
    try:
        with pytest.raises(OaPdfFetchError, match="not public"):
            await fetcher.fetch("http://127.0.0.1/paper.pdf")
    finally:
        await fetcher.aclose()


@pytest.mark.asyncio
async def test_openalex_expands_oa_sources_and_falls_back_on_pdf_failure() -> None:
    work = {
        "id": "https://openalex.org/W1",
        "doi": "https://doi.org/10.1/paper",
        "display_name": "Paper",
        "abstract_inverted_index": {"abstract": [0]},
        "publication_year": 2024,
        "primary_location": {"landing_page_url": "https://papers.example/paper"},
        "best_oa_location": {"pdf_url": "https://papers.example/paper.pdf"},
        "authorships": [],
        "cited_by_count": 1,
        "is_retracted": False,
    }

    class Fetcher:
        def __init__(self, fail: bool = False) -> None:
            self.fail = fail

        async def sections(self, source, query, **kwargs):
            if self.fail:
                raise OaPdfParseError("broken PDF")
            return [
                source.model_copy(
                    update={
                        "url": source.url + "?dr_section=pdf-0",
                        "content": "Results\nAccuracy 99%",
                        "scholarly": source.scholarly.model_copy(update={"section": "results"}),
                    }
                )
            ]

        async def aclose(self) -> None:
            return None

    def openalex_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [work]})

    expanded = OpenAlexSearch(pdf_fetcher=Fetcher())
    expanded._client = httpx.AsyncClient(transport=httpx.MockTransport(openalex_handler))
    try:
        sources = await expanded.search("accuracy")
    finally:
        await expanded.aclose()
    assert sources[0].url.endswith("dr_section=pdf-0")
    assert sources[0].scholarly is not None
    assert sources[0].scholarly.section == "results"

    fallback = OpenAlexSearch(pdf_fetcher=Fetcher(fail=True))
    fallback._client = httpx.AsyncClient(transport=httpx.MockTransport(openalex_handler))
    try:
        sources = await fallback.search("accuracy")
    finally:
        await fallback.aclose()
    assert sources[0].url == "https://doi.org/10.1/paper"
    assert sources[0].scholarly is not None
    assert sources[0].scholarly.section == ""
