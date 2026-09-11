"""Serper Google Search backend."""

from __future__ import annotations

import httpx

from ..models import Source
from ..provider_limits import provider_request
from .base import SearchTool

_ENDPOINT = "https://google.serper.dev/search"
_MAX_RESULTS = 10


class SerperSearch(SearchTool):
    """Search Google through the Serper JSON API."""

    def __init__(self, api_key: str, *, timeout: float = 30.0) -> None:
        self._api_key = api_key
        if not api_key.strip():
            raise ValueError("SerperSearch requires an API key")
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        )

    @property
    def backend_name(self) -> str:
        return "SerperSearch"

    async def search(self, query: str, *, max_results: int = 5) -> list[Source]:
        if max_results <= 0:
            return []
        requested = min(max_results, _MAX_RESULTS)
        async with provider_request(_ENDPOINT, self._api_key):
            response = await self._client.post(
                _ENDPOINT,
                json={"q": query, "num": requested},
            )
            response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Serper returned a non-object JSON payload")
        results = payload.get("organic", [])
        sources: list[Source] = []
        for item in results if isinstance(results, list) else []:
            if not isinstance(item, dict) or not item.get("link"):
                continue
            sources.append(
                Source(
                    title=str(item.get("title") or "")[:200],
                    url=str(item["link"]),
                    content=str(item.get("snippet") or "")[:2000],
                )
            )
            if len(sources) >= requested:
                break
        return sources

    async def aclose(self) -> None:
        await self._client.aclose()
