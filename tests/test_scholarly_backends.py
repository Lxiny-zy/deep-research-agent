"""学术检索后端与学术引用渲染。

这一层的性质比「多两个后端」重要得多：它把「出处」从一个 URL 变成 DOI + 作者 +
机构 + 撤稿标记。因此测试重点不在能不能发请求，而在：

* 元数据抽取**不编造**——字段缺失必须落到空/None，而不是编一个默认值；
* ``peer_reviewed`` 保持三态，未知不被压成 False；
* OpenAlex 倒排索引摘要能还原，且还原文本就是逐字证据校验匹配的那份；
* arXiv 的 XML 解析对 DOCTYPE/超大响应**失败关闭**；
* 引用渲染把 URL 放在行尾——这是与前端引用回退解析器之间的格式契约。
"""

from __future__ import annotations

import httpx
import pytest

from deep_research.citation import format_reference
from deep_research.config import Settings
from deep_research.execution import ExecutionContext, RunExecutor
from deep_research.guardrails import EvidenceVerifier
from deep_research.models import Finding, ResearchResult, ScholarlyMetadata, Source
from deep_research.tools.arxiv_search import ArxivFeedError, ArxivSearch
from deep_research.tools.openalex import (
    OpenAlexQuotaExceeded,
    OpenAlexSearch,
    _abstract_from_inverted_index,
)


def _executor() -> RunExecutor:
    return RunExecutor(ExecutionContext(repo=None, catalog=None))  # type: ignore[arg-type]


def _openalex(handler) -> OpenAlexSearch:
    tool = OpenAlexSearch(mailto="who@example.org")
    tool._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return tool


def _arxiv(handler) -> ArxivSearch:
    tool = ArxivSearch()
    tool._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return tool


# ── OpenAlex ────────────────────────────────────────────────────────────────

_WORK = {
    "id": "https://openalex.org/W123",
    "doi": "https://doi.org/10.1364/oe.456",
    "display_name": "Snapshot spectral imaging with a coded aperture",
    "abstract_inverted_index": {"coded": [1], "We": [0], "apertures": [2]},
    "publication_year": 2024,
    "primary_location": {
        "landing_page_url": "https://opg.optica.org/oe/abstract.cfm?uri=oe-1",
        "version": "publishedVersion",
        "source": {"display_name": "Optics Express"},
    },
    "best_oa_location": {"pdf_url": "https://opg.optica.org/oe/1.pdf"},
    "authorships": [
        {
            "author": {"display_name": "A Wagadarikar"},
            "institutions": [{"display_name": "Duke University"}],
        },
        {
            "author": {"display_name": "D Brady"},
            "institutions": [{"display_name": "Duke University"}],
        },
    ],
    "cited_by_count": 812,
    "is_retracted": False,
}


@pytest.mark.asyncio
async def test_openalex_maps_a_work_to_a_scholarly_source() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["search"] == "CASSI 重建"
        assert request.url.params["mailto"] == "who@example.org"
        # select 必须覆盖所有被读取的字段，否则解析出来全是空值。
        assert "abstract_inverted_index" in request.url.params["select"]
        return httpx.Response(200, json={"results": [_WORK]})

    tool = _openalex(handler)
    try:
        sources = await tool.search("CASSI 重建", max_results=5)
    finally:
        await tool.aclose()

    assert len(sources) == 1
    source = sources[0]
    # 引用 URL 优先 DOI：它是永久标识，落地页会随出版方改版而变。
    assert source.url == "https://doi.org/10.1364/oe.456"
    assert source.title == "Snapshot spectral imaging with a coded aperture"
    assert source.content == "We coded apertures"

    meta = source.scholarly
    assert meta is not None
    assert meta.doi == "10.1364/oe.456"
    assert meta.work_id == "https://openalex.org/W123"
    assert meta.authors == ["A Wagadarikar", "D Brady"]
    # 机构去重：两位作者同一机构只算一个发布方，这是后续判独立性的输入。
    assert meta.affiliations == ["Duke University"]
    assert meta.venue == "Optics Express"
    assert meta.year == 2024
    assert meta.peer_reviewed is True
    assert meta.retracted is False
    assert meta.citation_count == 812
    assert meta.oa_pdf_url == "https://opg.optica.org/oe/1.pdf"


def test_inverted_index_abstract_is_restored_in_word_order() -> None:
    index = {"imaging": [2], "Hyperspectral": [0], "snapshot": [1], "the": [3, 5], "of": [4]}

    assert _abstract_from_inverted_index(index) == "Hyperspectral snapshot imaging the of the"


def test_inverted_index_tolerates_a_missing_or_malformed_payload() -> None:
    assert _abstract_from_inverted_index(None) == ""
    assert _abstract_from_inverted_index({"a": "not-a-list", "b": [0]}) == "b"
    # 布尔量在 Python 里是 int 的子类；位置字段收到 True 不能被当成下标 1。
    assert _abstract_from_inverted_index({"a": [True]}) == ""


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("publishedVersion", True),
        ("acceptedVersion", True),
        ("submittedVersion", False),
        ("", None),
        (None, None),
    ],
)
@pytest.mark.asyncio
async def test_peer_review_status_is_three_valued(version, expected) -> None:
    """缺字段必须是 None（未知），而不是 False——未知与「未经评审」是两回事。"""
    work = {**_WORK, "primary_location": {**_WORK["primary_location"], "version": version}}

    tool = _openalex(lambda request: httpx.Response(200, json={"results": [work]}))
    try:
        sources = await tool.search("q")
    finally:
        await tool.aclose()

    assert sources[0].scholarly is not None
    assert sources[0].scholarly.peer_reviewed is expected


@pytest.mark.asyncio
async def test_openalex_falls_back_to_the_landing_page_without_a_doi() -> None:
    work = {**_WORK, "doi": None}

    tool = _openalex(lambda request: httpx.Response(200, json={"results": [work]}))
    try:
        sources = await tool.search("q")
    finally:
        await tool.aclose()

    assert sources[0].url == "https://opg.optica.org/oe/abstract.cfm?uri=oe-1"
    assert sources[0].scholarly is not None
    assert sources[0].scholarly.doi == ""


@pytest.mark.asyncio
async def test_openalex_drops_records_with_no_resolvable_url() -> None:
    tool = _openalex(
        lambda request: httpx.Response(
            200, json={"results": [{"display_name": "no locator"}, _WORK]}
        )
    )
    try:
        sources = await tool.search("q")
    finally:
        await tool.aclose()

    assert [s.url for s in sources] == ["https://doi.org/10.1364/oe.456"]


@pytest.mark.asyncio
async def test_openalex_respects_max_results() -> None:
    works = [{**_WORK, "doi": f"10.1/{i}"} for i in range(10)]

    tool = _openalex(lambda request: httpx.Response(200, json={"results": works}))
    try:
        sources = await tool.search("q", max_results=3)
    finally:
        await tool.aclose()

    assert len(sources) == 3


@pytest.mark.asyncio
async def test_openalex_non_positive_max_results_does_not_issue_a_request() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"results": []})

    tool = _openalex(handler)
    try:
        assert await tool.search("q", max_results=0) == []
    finally:
        await tool.aclose()
    assert not called


@pytest.mark.asyncio
async def test_openalex_raises_on_http_error() -> None:
    tool = _openalex(lambda request: httpx.Response(500, json={}))
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await tool.search("q")
    finally:
        await tool.aclose()


@pytest.mark.asyncio
async def test_openalex_quota_exhaustion_reports_the_reset_window() -> None:
    """429 与普通 HTTP 错误的处置完全不同：只能等重置或换后端，不值得重试。

    因此它有自己的异常类型，且错误信息自带重置时间——运维不该为了知道
    「什么时候能恢复」去翻响应头。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"error": "Rate limit exceeded", "message": "Insufficient budget."},
            headers={"x-ratelimit-reset": "77614", "x-ratelimit-limit": "1000"},
        )

    tool = _openalex(handler)
    try:
        with pytest.raises(OpenAlexQuotaExceeded) as excinfo:
            await tool.search("q")
    finally:
        await tool.aclose()

    message = str(excinfo.value)
    assert "配额已耗尽" in message
    assert "日配额 1000 次" in message
    assert "21 小时后" in message
    assert "Insufficient budget." in message


@pytest.mark.asyncio
async def test_openalex_quota_message_survives_a_malformed_error_body() -> None:
    """错误处理路径本身不能再抛异常，否则真实原因会被掩盖。"""
    tool = _openalex(lambda request: httpx.Response(429, text="not json"))
    try:
        with pytest.raises(OpenAlexQuotaExceeded, match="配额已耗尽"):
            await tool.search("q")
    finally:
        await tool.aclose()


@pytest.mark.asyncio
async def test_a_quota_exhausted_openalex_does_not_sink_the_run() -> None:
    """配额耗尽必须被多后端隔离：其余后端照常产出，只留一条审计事件。"""
    from deep_research.observability import Tracer
    from deep_research.tools.composite import MultiBackendSearch

    exhausted = _openalex(lambda request: httpx.Response(429, json={"message": "no budget"}))
    healthy = _arxiv(lambda request: httpx.Response(200, text=_FEED))
    tool = MultiBackendSearch([exhausted, healthy], tracer=Tracer())
    try:
        sources = await tool.search("q", max_results=3)
    finally:
        await tool.aclose()

    assert [s.url for s in sources] == ["https://arxiv.org/abs/2205.10102v2"]


# ── arXiv ───────────────────────────────────────────────────────────────────

_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2205.10102v2</id>
    <published>2022-05-20T12:00:00Z</published>
    <updated>2023-01-04T12:00:00Z</updated>
    <title>Mask-guided Spectral-wise Transformer
      for Efficient Reconstruction</title>
    <summary>We propose   a transformer
      for spectral compressive imaging.</summary>
    <author><name>Yuanhao Cai</name></author>
    <author><name>Jing Lin</name></author>
    <arxiv:doi>10.1109/CVPR52688.2022.01698</arxiv:doi>
    <arxiv:journal_ref>CVPR 2022</arxiv:journal_ref>
    <link href="http://arxiv.org/abs/2205.10102v2" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/2205.10102v2" rel="related"
          type="application/pdf"/>
  </entry>
</feed>
"""


@pytest.mark.asyncio
async def test_arxiv_parses_an_atom_entry() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["search_query"] == "all:MST 光谱重建"
        assert request.url.params["sortBy"] == "relevance"
        return httpx.Response(200, text=_FEED)

    tool = _arxiv(handler)
    try:
        sources = await tool.search("MST 光谱重建", max_results=5)
    finally:
        await tool.aclose()

    assert len(sources) == 1
    source = sources[0]
    # http → https：同一条目不能因协议差异被当成两个来源。
    assert source.url == "https://arxiv.org/abs/2205.10102v2"
    # 折叠换行必须发生在进 Source.content 之前：逐字校验匹配的就是这份文本。
    assert source.title == "Mask-guided Spectral-wise Transformer for Efficient Reconstruction"
    assert source.content == "We propose a transformer for spectral compressive imaging."

    meta = source.scholarly
    assert meta is not None
    assert meta.doi == "10.1109/CVPR52688.2022.01698"
    # work_id 剥掉版本号：v1 与 v2 是同一份工作，不能算成两个独立发布方。
    assert meta.work_id == "arxiv:2205.10102"
    assert meta.version == "v2"
    assert meta.authors == ["Yuanhao Cai", "Jing Lin"]
    assert meta.venue == "CVPR 2022"
    # 首次投稿年份，不随 updated 变化。
    assert meta.year == 2022
    # 取回的这份文档就是预印本，与「另有期刊版」是两件事。
    assert meta.peer_reviewed is False
    assert meta.oa_pdf_url == "https://arxiv.org/pdf/2205.10102v2"
    assert meta.affiliations == []


@pytest.mark.asyncio
async def test_arxiv_without_a_journal_ref_reports_arxiv_as_the_venue() -> None:
    feed = _FEED.replace("<arxiv:journal_ref>CVPR 2022</arxiv:journal_ref>", "")

    tool = _arxiv(lambda request: httpx.Response(200, text=feed))
    try:
        sources = await tool.search("q")
    finally:
        await tool.aclose()

    assert sources[0].scholarly is not None
    assert sources[0].scholarly.venue == "arXiv"


@pytest.mark.asyncio
async def test_arxiv_rejects_a_doctype_declaration() -> None:
    """实体声明只能出现在 DTD 里，掐掉 DOCTYPE 就掐掉了实体展开攻击的入口。"""
    hostile = (
        '<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY a "aaaaaaaaaa">]>'
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>&a;</id></entry></feed>'
    )

    tool = _arxiv(lambda request: httpx.Response(200, text=hostile))
    try:
        with pytest.raises(ArxivFeedError, match="DOCTYPE"):
            await tool.search("q")
    finally:
        await tool.aclose()


@pytest.mark.asyncio
async def test_arxiv_rejects_an_oversized_response() -> None:
    tool = _arxiv(lambda request: httpx.Response(200, text="<feed/>" + " " * 1_100_000))
    try:
        with pytest.raises(ArxivFeedError, match="上限"):
            await tool.search("q")
    finally:
        await tool.aclose()


@pytest.mark.asyncio
async def test_arxiv_rejects_malformed_xml() -> None:
    tool = _arxiv(lambda request: httpx.Response(200, text="<feed><entry>"))
    try:
        with pytest.raises(ArxivFeedError, match="合法 XML"):
            await tool.search("q")
    finally:
        await tool.aclose()


@pytest.mark.asyncio
async def test_arxiv_skips_entries_without_an_id() -> None:
    feed = (
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<entry><title>no id</title></entry>"
        "<entry><id>http://arxiv.org/abs/1234.5678v1</id><title>ok</title></entry>"
        "</feed>"
    )

    tool = _arxiv(lambda request: httpx.Response(200, text=feed))
    try:
        sources = await tool.search("q")
    finally:
        await tool.aclose()

    assert [s.url for s in sources] == ["https://arxiv.org/abs/1234.5678v1"]


# ── 配置与后端组装 ───────────────────────────────────────────────────────────


def test_scholarly_backends_are_accepted_by_config() -> None:
    settings = Settings(search_backends=("openalex", "arxiv"))

    assert settings.search_backends == ("openalex", "arxiv")


def test_unknown_backend_error_lists_the_scholarly_options() -> None:
    with pytest.raises(ValueError, match="openalex"):
        Settings(search_backends=("sci-hub",))


@pytest.mark.asyncio
async def test_scholarly_backends_need_no_api_key() -> None:
    """学术源没有「缺 key 退化」分支：启用即可用，这是它们相对通用检索的优势。"""
    settings = Settings(search_backends=("openalex", "arxiv"))

    tool = await _executor().build_search_tool(settings)

    assert tool is not None
    assert tool.backend_name == "OpenAlexSearch+ArxivSearch"
    await tool.aclose()


@pytest.mark.asyncio
async def test_web_and_scholarly_backends_combine() -> None:
    settings = Settings(
        search_backends=("tavily", "openalex"),
        tavily_api_key="t-key",
    )

    tool = await _executor().build_search_tool(settings)

    assert tool is not None
    # manifest 里能区分单/双/学术后端组合，才能做对照实验。
    assert tool.backend_name == "TavilySearch+OpenAlexSearch"
    await tool.aclose()


@pytest.mark.asyncio
async def test_default_configuration_is_unchanged_by_the_new_backends() -> None:
    """默认部署行为必须与引入学术源之前完全一致。"""
    assert await _executor().build_search_tool(Settings(tavily_api_key="t-key")) is None


# ── 引用渲染 ─────────────────────────────────────────────────────────────────


def test_reference_renders_authors_title_venue_year_and_url() -> None:
    meta = ScholarlyMetadata(
        doi="10.1364/oe.456",
        authors=["A Wagadarikar", "D Brady"],
        venue="Optics Express",
        year=2024,
        peer_reviewed=True,
    )

    reference = format_reference("https://doi.org/10.1364/oe.456", meta, title="Snapshot imaging")

    assert reference == (
        "A Wagadarikar, D Brady. Snapshot imaging. Optics Express, 2024. "
        "https://doi.org/10.1364/oe.456"
    )
    # 与前端回退解析器的格式契约：URL 在行尾且不带尾随标点。
    assert reference.endswith("https://doi.org/10.1364/oe.456")


def test_reference_truncates_long_author_lists() -> None:
    meta = ScholarlyMetadata(authors=["A", "B", "C", "D", "E"])

    assert format_reference("https://x.test/1", meta, title="T").startswith("A, B, C 等. T.")


def test_reference_keeps_the_doi_when_the_url_points_elsewhere() -> None:
    """arXiv 的 URL 是 abs 页而 DOI 指向期刊版，这时 DOI 是额外信息，必须保留。"""
    meta = ScholarlyMetadata(doi="10.1109/CVPR52688.2022.01698", venue="CVPR 2022", year=2022)

    reference = format_reference("https://arxiv.org/abs/2205.10102v2", meta, title="MST")

    assert "doi:10.1109/CVPR52688.2022.01698" in reference
    assert reference.endswith("https://arxiv.org/abs/2205.10102v2")


def test_reference_omits_fields_that_were_never_extracted() -> None:
    """缺失就是缺失：不能出现 n.d.、Anonymous 之类让缺失看起来像已知的占位符。"""
    reference = format_reference("https://x.test/1", ScholarlyMetadata(), title="仅有标题")

    assert reference == "仅有标题. https://x.test/1"


def test_reference_flags_retraction_and_preprint_status() -> None:
    retracted = format_reference(
        "https://doi.org/10.1/x", ScholarlyMetadata(doi="10.1/x", retracted=True), title="T"
    )
    preprint = format_reference(
        "https://arxiv.org/abs/1v2", ScholarlyMetadata(peer_reviewed=False, version="v2"), title="T"
    )

    # 撤稿必须出现在引用里：报告是给人看的最终产物，只写进审计事件读者看不到。
    assert "【已撤稿】" in retracted
    assert "【预印本 v2】" in preprint


def test_reference_falls_back_to_the_previous_form_for_web_sources() -> None:
    """非学术来源渲染为空串＝「回退裸 URL」，既有通用调研报告产物逐字节不变。"""
    assert format_reference("https://blog.test/p", None, title="某篇博客") == ""
    assert format_reference("https://blog.test/p", None) == ""


# ── 与证据链的衔接 ───────────────────────────────────────────────────────────


def test_evidence_verifier_stamps_the_rendered_reference() -> None:
    """引用在验证时刻渲染：只有这一刻同时握有 Finding 与 Source。"""
    source = Source(
        title="Snapshot imaging",
        url="https://doi.org/10.1364/oe.456",
        content="We measured a PSNR of 34.26 dB on the KAIST simulation set.",
        scholarly=ScholarlyMetadata(
            doi="10.1364/oe.456", authors=["A Wagadarikar"], venue="Optics Express", year=2024
        ),
    )
    candidate = Finding(
        statement="该方法在 KAIST 仿真集上达到 34.26 dB",
        source_url=source.url,
        evidence_quote="PSNR of 34.26 dB on the KAIST simulation set",
    )

    check = EvidenceVerifier().verify(candidate, source)

    assert check.accepted
    assert check.finding is not None
    assert check.finding.verification.source_reference == (
        "A Wagadarikar. Snapshot imaging. Optics Express, 2024. https://doi.org/10.1364/oe.456"
    )


def test_web_source_findings_keep_an_empty_reference() -> None:
    source = Source(title="博客", url="https://blog.test/p", content="一段可以被逐字引用的正文")
    candidate = Finding(
        statement="某个论断",
        source_url=source.url,
        evidence_quote="可以被逐字引用的正文",
    )

    check = EvidenceVerifier().verify(candidate, source)

    assert check.finding is not None
    assert check.finding.verification.source_reference == ""


def test_report_reference_list_uses_scholarly_citations_but_keeps_url_citations() -> None:
    """``Report.citations`` 必须保持纯 URL：前端按下标跳转、指标按 URL 比对快照。"""
    from deep_research.agents.synthesizer import Synthesizer

    scholarly = Finding(
        statement="A",
        source_url="https://doi.org/10.1/a",
        evidence_quote="q",
    )
    scholarly.verification.source_reference = "作者. 标题. 期刊, 2024. https://doi.org/10.1/a"
    web = Finding(statement="B", source_url="https://blog.test/p", evidence_quote="q")
    results = [ResearchResult(sub_question="sq", findings=[scholarly, web])]

    synth = Synthesizer.__new__(Synthesizer)
    report = Synthesizer._finalize(
        synth,
        "查询",
        "正文",
        {"https://doi.org/10.1/a": 1, "https://blog.test/p": 2},
        Synthesizer._references(results),
    )

    assert report.citations == ["https://doi.org/10.1/a", "https://blog.test/p"]
    assert "[1] 作者. 标题. 期刊, 2024. https://doi.org/10.1/a" in report.markdown
    # 没有学术元数据的来源退回裸 URL 行，与改造前一致。
    assert "[2] https://blog.test/p" in report.markdown
