from __future__ import annotations

from types import SimpleNamespace

import pytest

from deep_research.llm import LLM
from deep_research.observability import Tracer


class FakeStream:
    def __init__(self) -> None:
        self.chunks = [
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="动态"))], usage=None
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="更新"))], usage=None
            ),
            SimpleNamespace(choices=[], usage=SimpleNamespace(total_tokens=17)),
        ]

    def __aiter__(self):
        async def iterate():
            for chunk in self.chunks:
                yield chunk

        return iterate()


class FakeCompletions:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        return FakeStream()


@pytest.mark.asyncio
async def test_stream_tokens_update_live_then_reconcile_exact_usage(settings):
    tracer = Tracer()
    settings.llm_api_key = "test-key"
    llm = LLM(settings, tracer)
    completions = FakeCompletions()
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    observed: list[int] = []
    deltas: list[str] = []
    async for delta in llm.stream("system", "user"):
        deltas.append(delta)
        observed.append(tracer.total_tokens)

    assert "".join(deltas) == "动态更新"
    assert observed == sorted(observed)
    assert observed[0] > 0
    assert tracer.total_tokens == 17
    assert tracer.tokens_estimated is False
    assert completions.requests[0]["stream_options"] == {"include_usage": True}
