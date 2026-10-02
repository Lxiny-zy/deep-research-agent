"""Bounded, process-local reuse of verified paper extraction for identical inputs."""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from typing import Any

from ..models import Finding, Source


def evidence_cache_key(scope: str, query: str, sources: list[Source], researcher: Any) -> str:
    def model(llm: Any) -> dict[str, Any]:
        settings = getattr(llm, "settings", None)
        credential = str(getattr(settings, "llm_api_key", ""))
        return {
            "model": getattr(llm, "model", type(llm).__name__),
            "endpoint": getattr(settings, "llm_base_url", None),
            "credential": hashlib.sha256(credential.encode()).hexdigest(),
            "temperature": getattr(llm, "default_temperature", None),
            "mode": getattr(llm, "parameter_mode", None),
            "effort": getattr(llm, "reasoning_effort", None),
            "context_window_tokens": getattr(llm, "context_window_tokens", None),
            "max_output_tokens": getattr(settings, "llm_max_output_tokens", None),
        }

    payload = {
        "version": 7,
        "scope": scope,
        "query": query,
        "sources": [source.model_dump(mode="json") for source in sources],
        "prompt": researcher.system,
        "extractor": model(researcher.llm),
        "verifier": model(researcher.verification_llm),
        "source_screening": researcher.settings.intent_source_screening,
        "results_per_search": researcher.settings.results_per_search,
        "input_limit": researcher.settings.llm_max_input_chars,
        "output_limit": researcher.settings.llm_max_output_tokens,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


class PaperEvidenceCache:
    def __init__(self, max_entries: int = 64, ttl_seconds: float = 1800) -> None:
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._items: OrderedDict[str, tuple[float, list[Finding], int]] = OrderedDict()

    def get(self, key: str) -> tuple[list[Finding], int] | None:
        item = self._items.pop(key, None)
        if item is None:
            return None
        if item[0] <= time.monotonic():
            return None
        self._items[key] = item
        return [finding.model_copy(deep=True) for finding in item[1]], item[2]

    def put(self, key: str, findings: list[Finding], raw_count: int) -> None:
        if not findings:
            return
        self._items.pop(key, None)
        self._items[key] = (
            time.monotonic() + self.ttl_seconds,
            [finding.model_copy(deep=True) for finding in findings],
            raw_count,
        )
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)
