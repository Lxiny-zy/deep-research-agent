import io
import tarfile

import httpx
import pytest

from deep_research.guardrails import EvidenceVerifier, report_eligible
from deep_research.models import Finding, Source
from deep_research.tools.arxiv_fulltext import (
    ArxivArchiveError,
    ArxivEprintFetcher,
    ArxivFulltextFetchError,
    ArxivLatexError,
    LatexDocument,
    ParseLimits,
    arxiv_eprint_url,
    parse_arxiv_eprint,
    select_sections,
)
from deep_research.tools.arxiv_search import ArxivSearch


def _tar(files: dict[str, bytes], *, symlink: bool = False, directory: bool = False) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        if symlink:
            info = tarfile.TarInfo("link.tex")
            info.type = tarfile.SYMTYPE
            info.linkname = "main.tex"
            archive.addfile(info)
        if directory:
            archive.addfile(tarfile.TarInfo("figures/"))
    return out.getvalue()


def test_parse_nested_sections_preserves_table_and_comments() -> None:
    raw = _tar(
        {
            "main.tex": (
                b"\\documentclass{article}\n\\begin{document}\n"
                b"% \\section{Ignored}\n\\begin{abstract}A 42\\% result"
                b"\\end{abstract}\n\\section*{Results {2024}}\n"
                b"\\input{parts/method}\n\\section{Conclusion}\nDone\n"
            ),
            "parts/method.tex": (
                b"\\section{Method}\nValues 0.95 and \\begin{tabular}{cc}1 & 2\\end{tabular}\n"
            ),
        }
    )
    document = parse_arxiv_eprint(raw)
    assert isinstance(document, LatexDocument)
    assert document.main_file == "main.tex"
    assert "tabular" in document.text and "0.95" in document.text
    assert [s.title for s in document.sections] == [
        "Abstract",
        "Results {2024}",
        "Method",
        "Conclusion",
    ]


def test_arxiv_url_requested_version_overrides_embedded_version() -> None:
    assert arxiv_eprint_url("arxiv:1234.5678v1", "v2").endswith("/1234.5678v2")


def test_section_parser_accepts_optional_short_titles() -> None:
    document = parse_arxiv_eprint(_tar({"main.tex": b"\\section[Short result]{Results}\n38.36 dB"}))
    assert document.sections[0].title == "Results"
    assert "38.36 dB" in document.sections[0].text


def test_selector_is_query_relevant_and_deterministic() -> None:
    raw = _tar(
        {
            "main.tex": (
                b"\\section{Introduction}\nbackground\n\\section{Results}\n"
                b"accuracy 99%\n\\section{Method}\nprotocol"
            )
        }
    )
    document = parse_arxiv_eprint(raw)
    first = select_sections(document, "accuracy", max_chars=10_000, required=["method"])
    second = select_sections(document, "accuracy", max_chars=10_000, required=["method"])
    assert [s.title for s in first] == ["Results", "Method"]
    assert [s.title for s in first] == [s.title for s in second]


def test_selector_falls_back_to_full_document_when_no_match() -> None:
    document = parse_arxiv_eprint(_tar({"main.tex": b"\\section{Overview}\n123 456"}))
    selected = select_sections(document, "unmatched", max_chars=100)
    assert [section.title for section in selected] == ["Overview"]
    assert "123 456" in selected[0].text


def test_no_section_document_has_an_other_fallback() -> None:
    document = parse_arxiv_eprint(_tar({"main.tex": b"Plain full text with 38.36 dB"}))
    assert len(document.sections) == 1
    assert document.sections[0].canonical == "other"
    assert "38.36 dB" in document.sections[0].text


def test_root_selection_prefers_documentclass_when_main_is_an_include() -> None:
    document = parse_arxiv_eprint(
        _tar(
            {
                "main.tex": b"\\input{body}",
                "body.tex": b"\\section{Body}\nbody",
                "paper.tex": b"\\documentclass{article}\n\\section{Results}\nresult",
            }
        )
    )
    assert document.main_file == "main.tex"


def test_numeric_evidence_from_abstract_is_not_report_eligible() -> None:
    source = Source(
        url="https://arxiv.org/abs/2205.10102v2?dr_section=0",
        content="Abstract\nPSNR 38.36 dB",
        scholarly={"section": "abstract"},
    )
    finding = Finding(
        statement="PSNR is 38.36 dB",
        source_url=source.url,
        evidence_quote="PSNR 38.36 dB",
        quantity={"metric": "PSNR", "value": 38.36, "unit": "dB", "rendered": "38.36"},
    )
    check = EvidenceVerifier().verify(finding, source)
    assert check.accepted and check.finding is not None
    check.finding.verification.semantic_status = "supported"
    assert check.finding.verification.quantity_status == "unsupported"
    assert not report_eligible(check.finding)


@pytest.mark.parametrize("name", ["../evil.tex", "/absolute.tex", "dir/../evil.tex"])
def test_rejects_traversal(name: str) -> None:
    with pytest.raises(ArxivArchiveError):
        parse_arxiv_eprint(_tar({name: b"x"}))


def test_rejects_symlink_and_member_limits() -> None:
    with pytest.raises(ArxivArchiveError):
        parse_arxiv_eprint(_tar({"main.tex": b"x"}, symlink=True))
    with pytest.raises(ArxivArchiveError):
        parse_arxiv_eprint(_tar({"main.tex": b"x"}), ParseLimits(max_members=0))


def test_ignores_safe_directory_members() -> None:
    document = parse_arxiv_eprint(_tar({"main.tex": b"\\section{A}\ntext"}, directory=True))
    assert document.main_file == "main.tex"


def test_rejects_non_tex_archive() -> None:
    with pytest.raises(ArxivLatexError):
        parse_arxiv_eprint(_tar({"README": b"not latex"}))


def test_eprint_url_normalizes_work_and_version() -> None:
    assert arxiv_eprint_url("arxiv:2205.10102", "v2") == (
        "https://export.arxiv.org/e-print/2205.10102v2"
    )
    with pytest.raises(ArxivFulltextFetchError):
        arxiv_eprint_url("arxiv:../escape")
    assert arxiv_eprint_url("arxiv:hep-th/9901001", "v1").endswith("hep-th/9901001v1")


@pytest.mark.asyncio
async def test_fetcher_downloads_bounded_eprint() -> None:
    raw = _tar({"main.tex": b"\\section{Results}\nPSNR 38.36 dB"})

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://export.arxiv.org/e-print/2205.10102v2"
        return httpx.Response(200, content=raw)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = ArxivEprintFetcher(client=client)
    try:
        source = Source(
            title="MST",
            url="https://arxiv.org/abs/2205.10102v2",
            content="abstract fallback",
            scholarly={"work_id": "arxiv:2205.10102", "version": "v2"},
        )
        sections = await fetcher.sections(source, "PSNR", max_chars=1_000)
    finally:
        await fetcher.aclose()

    assert len(sections) == 1
    assert sections[0].url.endswith("?dr_section=0")
    assert sections[0].scholarly is not None
    assert sections[0].scholarly.section == "results"
    assert "38.36 dB" in sections[0].content


@pytest.mark.asyncio
async def test_fetcher_rejects_oversized_content_length_without_reading_body() -> None:
    class UnreadStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise AssertionError("oversized response body must not be read")
            yield b""  # pragma: no cover

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"Content-Length": "9"},
                stream=UnreadStream(),
            )
        )
    )
    try:
        with pytest.raises(ArxivFulltextFetchError, match="exceeds input size limit"):
            await ArxivEprintFetcher(client=client, max_bytes=8).fetch("arxiv:2205.10102")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_fetcher_stops_streaming_as_soon_as_body_exceeds_limit() -> None:
    class ChunkedStream(httpx.AsyncByteStream):
        emitted = 0

        async def __aiter__(self):
            for chunk in (b"1234", b"5678", b"should-not-be-read"):
                self.emitted += 1
                yield chunk

    stream = ChunkedStream()
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    )
    try:
        with pytest.raises(ArxivFulltextFetchError, match="exceeds input size limit"):
            await ArxivEprintFetcher(client=client, max_bytes=6).fetch("arxiv:2205.10102")
    finally:
        await client.aclose()

    assert stream.emitted == 2


@pytest.mark.asyncio
async def test_arxiv_search_expands_sources_with_an_injected_fetcher() -> None:
    feed = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2205.10102v2</id>
    <title>MST</title><summary>abstract fallback</summary>
    <published>2022-01-01T00:00:00Z</published>
    <author><name>Author</name></author>
  </entry>
</feed>"""
    raw = _tar({"main.tex": b"\\section{Results}\nPSNR 38.36 dB"})

    class Fetcher:
        async def sections(self, source, query, *, max_chars=12_000, required=()):
            client = httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request: httpx.Response(200, content=raw))
            )
            return await ArxivEprintFetcher(client=client).sections(
                source, query, max_chars=max_chars, required=required
            )

        async def aclose(self):
            return None

    tool = ArxivSearch(eprint_fetcher=Fetcher())
    tool._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=feed))
    )
    try:
        sources = await tool.search("PSNR", max_results=5)
    finally:
        await tool.aclose()
    assert sources and sources[0].url.endswith("?dr_section=0")
