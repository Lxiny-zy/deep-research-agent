import json

import httpx
import pytest

from deep_research.observability import Tracer
from deep_research.security import ProviderURLPolicyError
from deep_research.tools.model_search import ModelSearch, cited_sources


@pytest.mark.parametrize(
    "usage, expected",
    [
        ({"input_tokens": 8, "output_tokens": 3}, 11),
        ({"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11}, 11),
        ({"total_tokens": 0}, 0),
        (None, None),
        ({"total_tokens": -2}, None),
    ],
)
async def test_search_usage_is_charged_even_when_citations_are_invalid(usage, expected):
    tracer = Tracer()
    tracer.add_tokens(5)
    tool = ModelSearch("key", endpoint="https://example.com/responses", model="s", tracer=tracer)
    await tool._client.aclose()
    tool._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"usage": usage}))
    )
    try:
        with pytest.raises(ValueError, match="结构化引用"):
            await tool.search("q")
        assert tracer.total_tokens == 5 + (expected or 0)
        assert tracer.events[0].data["usage_known"] is (expected is not None)
        assert tracer.events[0].data["total_tokens"] == expected
    finally:
        await tool.aclose()


@pytest.mark.parametrize("protocol", ["responses", "chat_search"])
async def test_search_model_fetches_evidence_without_forwarding_secrets(protocol):
    seen = []

    def model(request):
        body = json.loads(request.content)
        assert body["model"] == "search-model"
        assert request.headers["authorization"] == "Bearer provider-secret"
        if protocol == "responses":
            assert body["tools"] == [{"type": "web_search"}]
        else:
            assert body["messages"][0]["content"] == "research"
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "content": [
                            {
                                "text": "Fabricated model answer",
                                "annotations": [
                                    {"url": "https://example.com/article", "title": "Original"}
                                ],
                            }
                        ]
                    }
                ]
            },
        )

    def page(request):
        assert "authorization" not in request.headers
        seen.append(str(request.url))
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<p>Measured value: 42.</p><script>not evidence</script>",
        )

    tool = ModelSearch(
        "provider-secret",
        endpoint="https://model.example/responses",
        model="search-model",
        protocol=protocol,
    )
    await tool._client.aclose()
    await tool._pages.aclose()
    tool._client = httpx.AsyncClient(
        transport=httpx.MockTransport(model), headers={"Authorization": "Bearer provider-secret"}
    )
    tool._pages = httpx.AsyncClient(transport=httpx.MockTransport(page))
    try:
        sources = await tool.search("research")
        assert sources[0].content == "Measured value: 42."
        assert "Fabricated" not in sources[0].content
        assert seen == ["https://example.com/article"]
    finally:
        await tool.aclose()


def test_citation_formats_never_use_generated_text():
    payload = {
        "citations": ["https://example.com/a"],
        "search_results": [{"url": "https://example.com/b"}],
        "choices": [
            {
                "message": {
                    "content": "fake",
                    "annotations": [{"url_citation": {"url": "https://example.com/c"}}],
                }
            }
        ],
    }
    sources = cited_sources(payload, 5)
    assert len(sources) == 3
    assert all(source.content == "" for source in sources)
    assert cited_sources(payload, 0) == []


async def test_fetch_redirect_to_private_host_is_rejected():
    tool = ModelSearch("key", endpoint="https://model.example/responses", model="model")
    await tool._pages.aclose()
    calls = []

    def redirect(request):
        calls.append(request.url)
        return httpx.Response(302, headers={"location": "https://127.0.0.1/private"})

    tool._pages = httpx.AsyncClient(transport=httpx.MockTransport(redirect))
    try:
        with pytest.raises(ProviderURLPolicyError):
            await tool._fetch_page("https://example.com")
        assert len(calls) == 1
        sources = await tool.hydrate(cited_sources({"citations": ["https://example.com"]}, 1))
        assert sources[0].content == ""
    finally:
        await tool.aclose()


async def test_missing_citations_fail_instead_of_inventing_sources():
    tool = ModelSearch("key", endpoint="https://model.example/responses", model="model")
    await tool._client.aclose()
    tool._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"choices": [{"message": {"content": "I searched the web"}}]}
            )
        )
    )
    try:
        with pytest.raises(ValueError, match="结构化引用"):
            await tool.search("q")
    finally:
        await tool.aclose()


async def test_source_body_limit_and_binary_content_are_not_evidence():
    tool = ModelSearch("key", endpoint="https://model.example/responses", model="model")
    await tool._pages.aclose()

    def page(request):
        if request.url.path == "/binary":
            return httpx.Response(
                200, headers={"content-type": "application/pdf"}, content=b"binary"
            )
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"x" * 1_000_001)

    tool._pages = httpx.AsyncClient(transport=httpx.MockTransport(page))
    try:
        sources = await tool.hydrate(
            cited_sources(
                {"citations": ["https://example.com/large", "https://example.com/binary"]}, 2
            )
        )
        assert len(sources) == 2
        assert all(source.content == "" for source in sources)
    finally:
        await tool.aclose()
