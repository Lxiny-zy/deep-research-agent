"""Compatibility adapter for Grok Responses web search."""

from __future__ import annotations

from typing import Any

from ..models import Source
from .model_search import ModelSearch, cited_sources

_DEFAULT_ENDPOINT = "https://api.x.ai/v1/responses"


class XaiGrokSearch(ModelSearch):
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "grok-4-1-fast-non-reasoning",
        endpoint: str = _DEFAULT_ENDPOINT,
        timeout: float = 60.0,
        allow_private: bool = False,
        max_input_chars: int = 100_000,
        max_output_tokens: int = 4096,
    ) -> None:
        if not api_key.strip():
            raise ValueError("XaiGrokSearch requires an API key")
        super().__init__(
            api_key,
            model=model,
            endpoint=endpoint,
            timeout=timeout,
            allow_private=allow_private,
            max_input_chars=max_input_chars,
            max_output_tokens=max_output_tokens,
        )

    @property
    def backend_name(self) -> str:
        return "XaiGrokSearch"


def _sources_from_response(payload: dict[str, Any], max_results: int) -> list[Source]:
    return cited_sources(payload, max_results)
