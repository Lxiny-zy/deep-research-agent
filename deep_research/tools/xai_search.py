"""xAI/Grok web-search backend using the Responses API."""

from __future__ import annotations

from typing import Any

import httpx

from ..models import Source
from .base import SearchTool

_DEFAULT_ENDPOINT = "https://api.x.ai/v1/responses"
_MAX_RESULTS = 20


class XaiGrokSearch(SearchTool):
    """Use Grok's hosted web search and expose cited pages as Sources."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "grok-4-1-fast-non-reasoning",
        endpoint: str = _DEFAULT_ENDPOINT,
        timeout: float = 60.0,
    ) -> None:
        if not api_key.strip():
            raise ValueError("XaiGrokSearch requires an API key")
        self._model = model
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        self._endpoint = endpoint

    @property
    def backend_name(self) -> str:
        return "XaiGrokSearch"

    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        if max_results <= 0:
            return []
        response = await self._client.post(
            self._endpoint,
            json={
                "model": self._model,
                "input": query,
                "tools": [{"type": "web_search"}],
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("xAI returned a non-object JSON payload")
        return _sources_from_response(payload, max_results)

    async def aclose(self) -> None:
        await self._client.aclose()


def _sources_from_response(payload: dict[str, Any], max_results: int) -> list[Source]:
    """Accept current Responses annotations and the older citations shape."""
    sources: list[Source] = []
    seen: set[str] = set()

    def add(url: object, title: object = "", content: object = "") -> None:
        if not isinstance(url, str) or not url or url in seen:
            return
        seen.add(url)
        sources.append(
            Source(
                title=str(title or "")[:200],
                url=url,
                content=str(content or "")[:2000],
            )
        )

    for citation in payload.get("citations", []):
        if isinstance(citation, dict):
            add(citation.get("url") or citation.get("link"), citation.get("title"))

    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            text = content.get("text") or content.get("value") or ""
            for annotation in content.get("annotations", []):
                if isinstance(annotation, dict):
                    add(annotation.get("url"), annotation.get("title"), text)
        for citation in item.get("citations", []):
            if isinstance(citation, dict):
                add(citation.get("url") or citation.get("link"), citation.get("title"))
    return sources[: min(max_results, _MAX_RESULTS)]
