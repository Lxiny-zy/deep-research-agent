"""The QA call ceiling applies to actual requests, across roles and retries."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError
from pydantic import BaseModel

from deep_research.call_budget import ModelCallBudget, ModelCallLimitExceeded
from deep_research.llm import LLM
from deep_research.observability import Tracer
from tests.test_llm_usage import FakeStream


class Reply(BaseModel):
    value: int


async def test_parallel_roles_share_one_request_ceiling(settings):
    settings.llm_api_key = "test-key"
    tracer = Tracer()
    tracer.call_budget = ModelCallBudget(2)
    models = [LLM(settings, tracer) for _ in range(3)]
    for model in models:
        await model.client.close()
    requests = []

    async def create(**kwargs):
        requests.append(kwargs)
        response = FakeStream()
        response.chunks = [SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(content='{"value": 1}'), finish_reason="stop",
            )],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10, total_tokens=30),
        )]
        return response

    for model in models:
        model.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        )
    results = await asyncio.gather(
        *(model.parse("system", "question", Reply) for model in models),
        return_exceptions=True,
    )
    assert sum(isinstance(result, Reply) for result in results) == 2
    assert sum(isinstance(result, ModelCallLimitExceeded) for result in results) == 1
    assert len(requests) == 2 and tracer.call_budget.used == 2
    with pytest.raises(ModelCallLimitExceeded):
        await models[0].parse("system", "another question", Reply)
    assert len(requests) == 2


async def test_retry_cannot_bypass_the_request_ceiling(settings):
    settings.llm_api_key = "test-key"
    tracer = Tracer()
    tracer.call_budget = ModelCallBudget(2)
    model = LLM(settings, tracer)
    await model.client.close()
    requests = []

    async def create(**kwargs):
        requests.append(kwargs)
        raise APIConnectionError(request=httpx.Request("POST", "https://model.test/completions"))

    model.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    with pytest.raises(ModelCallLimitExceeded):
        await model.parse("system", "question", Reply, retries=5)
    assert len(requests) == 2 and tracer.call_budget.used == 2
