"""资料库 URL 导入的网络边界：HTTPS、账号信息、内网目标、重定向与大小限制。

网络层用 httpx.MockTransport 替换，不产生任何真实请求。
"""

from __future__ import annotations

import httpx
import pytest

from deep_research.library import ingestion
from deep_research.library.ingestion import SourceImportError, prepare_source


def _install(monkeypatch, handler) -> list[str]:  # type: ignore[no-untyped-def]
    seen: list[str] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return handler(request)

    monkeypatch.setattr(
        ingestion,
        "provider_http_client",
        lambda **_: httpx.AsyncClient(transport=httpx.MockTransport(wrapped)),
    )
    return seen


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://example.org/doc", "HTTPS"),
        ("https://user:pw@example.org/doc", "账号信息"),
        ("https://127.0.0.1/doc", "来源 URL"),
        ("https://[::1", "格式无效"),
    ],
)
def test_document_url_policy_rejects_unsafe_targets(url, message):
    with pytest.raises(SourceImportError, match=message):
        ingestion._validate_document_url(url)


def test_document_url_policy_drops_fragment_and_keeps_query():
    assert (
        ingestion._validate_document_url(" https://example.org/p?id=3#section ")
        == "https://example.org/p?id=3"
    )


@pytest.mark.asyncio
async def test_url_import_follows_redirect_and_extracts_html(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(302, headers={"location": "/new"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"<html><head><title>Paper</title></head>"
            b"<body><p>Gain was 17%.</p></body></html>",
        )

    seen = _install(monkeypatch, handler)
    prepared = await prepare_source(title="", kind="url", origin_url="https://example.org/old")
    assert seen == ["https://example.org/old", "https://example.org/new"]
    assert prepared.origin_url == "https://example.org/new"
    assert prepared.title == "Paper"
    assert "Gain was 17%." in prepared.chunks[0]["content"]


@pytest.mark.asyncio
async def test_redirect_to_private_host_is_blocked(monkeypatch):
    _install(
        monkeypatch, lambda _r: httpx.Response(302, headers={"location": "https://10.0.0.5/x"})
    )
    with pytest.raises(SourceImportError, match="来源 URL"):
        await prepare_source(title="", kind="url", origin_url="https://example.org/a")


@pytest.mark.asyncio
async def test_redirect_without_location_and_redirect_loops_fail(monkeypatch):
    _install(monkeypatch, lambda _r: httpx.Response(302))
    with pytest.raises(SourceImportError, match="缺少目标地址"):
        await prepare_source(title="", kind="url", origin_url="https://example.org/a")

    seen = _install(monkeypatch, lambda _r: httpx.Response(302, headers={"location": "/loop"}))
    with pytest.raises(SourceImportError, match="次数过多"):
        await prepare_source(title="", kind="url", origin_url="https://example.org/a")
    assert len(seen) == ingestion.MAX_REDIRECTS + 1


@pytest.mark.asyncio
async def test_oversized_documents_are_rejected(monkeypatch):
    declared = str(ingestion.MAX_SOURCE_BYTES + 1)
    _install(monkeypatch, lambda _r: httpx.Response(200, headers={"content-length": declared}))
    with pytest.raises(SourceImportError, match="16 MB"):
        await prepare_source(title="", kind="url", origin_url="https://example.org/big")

    # 未声明长度时按实际读取的字节数截断
    monkeypatch.setattr(ingestion, "MAX_SOURCE_BYTES", 8)
    _install(monkeypatch, lambda _r: httpx.Response(200, content=b"0123456789abcdef"))
    with pytest.raises(SourceImportError, match="16 MB"):
        await prepare_source(title="", kind="url", origin_url="https://example.org/stream")


@pytest.mark.asyncio
async def test_http_errors_become_import_errors(monkeypatch):
    _install(monkeypatch, lambda _r: httpx.Response(404))
    with pytest.raises(SourceImportError, match="无法读取来源"):
        await prepare_source(title="", kind="url", origin_url="https://example.org/missing")

    def boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    _install(monkeypatch, boom)
    with pytest.raises(SourceImportError, match="ConnectError"):
        await prepare_source(title="", kind="url", origin_url="https://example.org/down")


@pytest.mark.asyncio
async def test_doi_import_resolves_through_doi_org(monkeypatch):
    seen = _install(
        monkeypatch,
        lambda _r: httpx.Response(
            200, headers={"content-type": "text/plain"}, content=b"Abstract."
        ),
    )
    prepared = await prepare_source(title="", kind="doi", origin_url="doi: 10.1364/OE.123456.")
    assert seen == ["https://doi.org/10.1364/OE.123456"]
    assert "Abstract." in prepared.chunks[0]["content"]


def test_text_decoding_falls_back_to_gb18030_and_rejects_binary():
    assert ingestion._decode_text("光谱成像".encode("gb18030")) == "光谱成像"
    with pytest.raises(SourceImportError, match="UTF-8"):
        ingestion._decode_text(b"\x81\x30\xff\xfe\x00\x81")
