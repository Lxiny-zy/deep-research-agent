"""Cited search models: discover URLs, then fetch evidence independently."""

from __future__ import annotations

import asyncio
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from ..models import Source
from ..observability import Tracer
from ..security import provider_http_client, validate_provider_url
from .base import SearchTool


class _PageText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden += 1
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def cited_sources(payload: dict[str, Any], max_results: int) -> list[Source]:
    """Read citation metadata only; never attribute a generated answer to a page."""
    found: dict[str, Source] = {}

    def add(item: Any) -> None:
        if isinstance(item, str):
            item = {"url": item}
        if not isinstance(item, dict):
            return
        item = item.get("url_citation", item)
        if not isinstance(item, dict):
            return
        url = item.get("url") or item.get("link")
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            return
        if url not in found:
            found[url] = Source(url=url, title=str(item.get("title") or url)[:200], content="")

    for citation in payload.get("citations", []) or []:
        add(citation)
    for result in payload.get("search_results", []) or []:
        add(result)
    for output in payload.get("output", []) or []:
        if not isinstance(output, dict):
            continue
        for citation in output.get("citations", []) or []:
            add(citation)
        for content in output.get("content", []) or []:
            if isinstance(content, dict):
                for annotation in content.get("annotations", []) or []:
                    add(annotation)
        action = output.get("action", {})
        if isinstance(action, dict):
            for source in action.get("sources", []) or []:
                add(source)
    for choice in payload.get("choices", []) or []:
        if isinstance(choice, dict) and isinstance(choice.get("message"), dict):
            message = choice["message"]
            for item in (message.get("annotations", []) or []) + (
                message.get("citations", []) or []
            ):
                add(item)
    return list(found.values())[: max(0, min(max_results, 20))]


class ModelSearch(SearchTool):
    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str,
        model: str,
        protocol: str = "responses",
        timeout: float = 60,
        allow_private: bool = False,
        tracer: Tracer | None = None,
    ) -> None:
        validate_provider_url(endpoint, allow_private=allow_private)
        self.endpoint, self.model, self.protocol = endpoint, model, protocol
        self._client = provider_http_client(allow_private=allow_private, timeout=timeout)
        self._client.headers["Authorization"] = f"Bearer {api_key}"
        # A separate public-only client: never forward model credentials to source URLs.
        self._pages = provider_http_client(timeout=min(timeout, 15))
        self._timeout = timeout
        self._tracer = tracer
        self._fetch_slots = asyncio.Semaphore(4)

    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        if max_results <= 0:
            return []
        if self.protocol == "chat_search":
            body = {"model": self.model, "messages": [{"role": "user", "content": query}]}
        else:
            body = {"model": self.model, "input": query, "tools": [{"type": "web_search"}]}
        async with asyncio.timeout(self._timeout):
            response = await self._client.post(self.endpoint, json=body)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("搜索模型响应必须是 JSON 对象")
            self._record_usage(payload)
            sources = cited_sources(payload, max_results)
            if not sources:
                raise ValueError("搜索模型未返回结构化引用 URL；请使用支持联网引用的模型与协议")
            return await self.hydrate(sources)

    def _record_usage(self, payload: dict) -> None:
        if self._tracer is None:
            return
        usage = payload.get("usage")
        usage = usage if isinstance(usage, dict) else {}

        def count(field: str) -> int | None:
            value = usage.get(field)
            return value if type(value) is int and value >= 0 else None

        input_tokens = count("input_tokens")
        output_tokens = count("output_tokens")
        if input_tokens is None:
            input_tokens = count("prompt_tokens")
        if output_tokens is None:
            output_tokens = count("completion_tokens")
        total = count("total_tokens")
        if total is None and input_tokens is not None and output_tokens is not None:
            total = input_tokens + output_tokens
        if total is not None:
            self._tracer.add_tokens(total)
        self._tracer.emit(
            "RESEARCHER",
            "info",
            f"搜索模型用量：{total} Token"
            if total is not None
            else "搜索模型未返回用量，总 Token 不含本次消耗",
            data={
                "category": "search_usage",
                "model": self.model,
                "protocol": self.protocol,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total,
                "usage_known": total is not None,
                "calls": 1,
            },
        )

    async def hydrate(self, sources: list[Source]) -> list[Source]:
        async def one(source: Source) -> Source:
            async with self._fetch_slots:
                try:
                    async with asyncio.timeout(15):
                        content = await self._fetch_page(source.url)
                except (httpx.HTTPError, ValueError, TimeoutError, UnicodeError):
                    content = ""
                # Keep an un-fetchable citation visible, but with no evidence text.
                return source.model_copy(update={"content": content})

        return list(await asyncio.gather(*(one(source) for source in sources)))

    async def _fetch_page(self, url: str) -> str:
        for _ in range(5):
            parsed = urlsplit(url)
            validate_provider_url(urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")))
            async with self._pages.stream("GET", url) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get("location", ""))
                    continue
                response.raise_for_status()
                mime = response.headers.get("content-type", "").lower()
                if not any(t in mime for t in ("text/html", "text/plain", "application/xhtml+xml")):
                    return ""
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 1_000_000:
                        raise ValueError("来源页面超过大小限制")
                text = data.decode(response.encoding or "utf-8", errors="replace")
                if "html" in mime:
                    parser = _PageText()
                    parser.feed(text)
                    text = "".join(parser.parts)
                return text.strip()[:24_000]
        raise ValueError("来源页面重定向次数过多")

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        finally:
            await self._pages.aclose()
