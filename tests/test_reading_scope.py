from __future__ import annotations

import asyncio

import pytest

from deep_research.agents.researcher import Researcher
from deep_research.models import ScholarlyMetadata, Source
from deep_research.observability import Tracer
from deep_research.tools.arxiv_fulltext import LatexDocument, LatexSection, select_sections
from deep_research.tools.arxiv_search import ArxivSearch
from deep_research.tools.base import reading_question, reading_question_scope, required_sections
from deep_research.tools.oa_pdf_fulltext import PdfDocument, PdfSection, select_pdf_sections
from deep_research.tools.openalex import OpenAlexSearch
from tests.fakes import FakeLLM, FakeSearch


def test_combined_reading_requirements_do_not_choose_results_instead_of_methods():
    assert required_sections("解释方法、实验条件、局限及 PSNR 结果") == {
        "method",
        "experiment",
        "results",
        "conclusion",
    }


async def test_compact_queries_keep_separate_full_reading_contexts_under_concurrency(settings):
    class Search(FakeSearch):
        def __init__(self):
            self.seen = []

        async def search(self, query, **kwargs):
            await asyncio.sleep(0)
            self.seen.append((query, reading_question(query)))
            return []

    search = Search()
    a = Researcher(FakeLLM(), search, Tracer(), settings)
    b = Researcher(FakeLLM(), search, Tracer(), settings)
    await asyncio.gather(
        a.run("完整方法问题", search_queries=["method query"]),
        b.run("完整实验问题", search_queries=["experiment query"]),
    )
    assert set(search.seen) == {
        ("method query", "完整方法问题"),
        ("experiment query", "完整实验问题"),
    }
    assert reading_question("standalone") == "standalone"


@pytest.mark.parametrize("backend", ["arxiv", "openalex"])
async def test_fulltext_selectors_receive_full_question_and_all_required_categories(backend):
    class Fetcher:
        async def sections(self, source, query, *, max_chars, required):
            assert query == "解释方法、实验条件、局限及 PSNR 结果"
            assert required == {"method", "experiment", "results", "conclusion"}
            return [source]

        async def aclose(self):
            pass

    if backend == "arxiv":
        search = ArxivSearch(fulltext=True, eprint_fetcher=Fetcher())
        source = Source(
            url="https://arxiv.org/abs/2205.10102",
            scholarly=ScholarlyMetadata(work_id="arxiv:2205.10102"),
        )
    else:
        search = OpenAlexSearch(fulltext=True, pdf_fetcher=Fetcher())
        source = Source(
            url="https://example.org/paper",
            scholarly=ScholarlyMetadata(
                work_id="https://openalex.org/W1", oa_pdf_url="https://example.org/paper.pdf"
            ),
        )
    try:
        with reading_question_scope("解释方法、实验条件、局限及 PSNR 结果"):
            assert await search._fulltext_sources(source, "CASSI") == [source]
    finally:
        await search.aclose()


def test_required_latex_parent_keeps_long_child_and_grandchild_sections_complete():
    sections = (
        LatexSection("Abstract", "summary", level=0, command="abstract", index=0),
        LatexSection("Introduction", "optional background", index=1),
        LatexSection("Method", "outline", index=2),
        LatexSection(
            "Network", "long method " * 100 + "METHOD-END", level=2, command="subsection", index=3
        ),
        LatexSection("Module details", "DETAIL-END", level=3, command="subsubsection", index=4),
        LatexSection("Results", "RESULT-END", index=5),
        LatexSection("Other", "unneeded text", index=6),
    )
    doc = LatexDocument("main.tex", "", sections)
    chosen = select_sections(doc, "keyword", max_chars=20, required={"method", "results"})
    assert [s.index for s in chosen] == [2, 3, 4, 5]
    assert chosen[1].text.endswith("METHOD-END") and chosen[2].text == "DETAIL-END"
    assert all(s is sections[s.index] for s in chosen)
    abstract = select_sections(doc, "keyword", max_chars=20, required={"abstract"})
    assert [s.index for s in abstract] == [0]


def test_pdf_required_numbered_children_are_not_dropped_or_truncated():
    sections = (
        PdfSection("Abstract", "summary", 0, _kind="abstract"),
        PdfSection("1 Introduction", "background", 1, _kind="introduction"),
        PdfSection("2 Method", "outline", 2, _kind="method"),
        PdfSection("2.1 Network", "long method " * 100 + "METHOD-END", 3),
        PdfSection("2.1.1 Module", "DETAIL-END", 4),
        PdfSection("3 Results", "RESULT-END", 5, _kind="results"),
        PdfSection("References", "unneeded text", 6),
    )
    doc = PdfDocument(text="", sections=sections)
    chosen = select_pdf_sections(doc, "keyword", max_chars=20, required={"method", "results"})
    assert [s.index for s in chosen] == [2, 3, 4, 5]
    assert chosen[1].text.endswith("METHOD-END")
    assert all(s is sections[s.index] for s in chosen)
    assert [
        s.index for s in select_pdf_sections(doc, "keyword", max_chars=20, required={"abstract"})
    ] == [0]


@pytest.mark.parametrize("pdf", [False, True])
def test_best_section_is_kept_complete_when_larger_than_optional_target(pdf):
    body = "important content " * 100 + "TAIL-KEPT"
    if pdf:
        section = PdfSection("Overview", body, 0)
        selected = select_pdf_sections(
            PdfDocument(text=body, sections=(section,)), "important", max_chars=30
        )
    else:
        section = LatexSection("Overview", body)
        selected = select_sections(
            LatexDocument("main.tex", body, (section,)), "important", max_chars=30
        )
    assert selected == [section] and selected[0].text.endswith("TAIL-KEPT")
