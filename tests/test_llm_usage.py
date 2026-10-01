from __future__ import annotations

from types import SimpleNamespace

import pytest

from deep_research.llm import LLM, _usage_details
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


@pytest.mark.parametrize("cache", [None, 0, 8, 999])
def test_cache_usage_distinguishes_missing_zero_and_invalid(cache):
    usage = SimpleNamespace(
        prompt_tokens=10,
        completion_tokens=3,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cache),
    )
    report = _usage_details(SimpleNamespace(usage=usage))
    assert report is not None
    assert report["cached_input_tokens"] == (cache if cache in (0, 8) else None)
    assert report["input_tokens"] == 10


async def test_provider_reasoning_is_separate_from_answer_and_cache_usage_is_recorded(settings):
    tracer = Tracer()
    settings.llm_api_key = "test-key"
    llm = LLM(settings, tracer)
    response = FakeStream()
    response.chunks = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content=None, reasoning_content="接口返回的展示内容")
                )
            ],
            usage=None,
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="最终答案"))], usage=None
        ),
        SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(
                total_tokens=17, prompt_tokens=12, completion_tokens=5, prompt_cache_hit_tokens=8
            ),
        ),
    ]

    async def create(**kwargs):
        return response

    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    answer = "".join([part async for part in llm.stream("system", "question")])
    assert answer == "最终答案"
    reasoning = [
        event.data["reasoning_delta"]
        for event in tracer.events
        if event.data and "reasoning_delta" in event.data
    ]
    assert "".join(reasoning) == "接口返回的展示内容"
    usage = [
        event.data["llm_usage"]
        for event in tracer.events
        if event.data and "llm_usage" in event.data
    ]
    assert usage[0]["cached_input_tokens"] == 8
    assert tracer.total_tokens == 17
