from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, AsyncOpenAI, BadRequestError
from pydantic import BaseModel

from deep_research.call_budget import ModelCallBudget, ModelCallLimitExceeded
from deep_research.llm import LLM, ModelStreamInterrupted
from deep_research.observability import Tracer


class Stream:
    def __init__(self, text="done", usage=None, error=None):
        self.text, self.usage, self.error = text, usage, error

    def __aiter__(self):
        async def chunks():
            yield SimpleNamespace(
                choices=[SimpleNamespace(
                    delta=SimpleNamespace(content=self.text), finish_reason=None,
                )], usage=None,
            )
            if self.error:
                raise self.error
            yield SimpleNamespace(choices=[], usage=self.usage)
        return chunks()


class Completions:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    async def create(self, **request):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        await asyncio.sleep(0)
        return response


async def client(settings, *responses):
    settings.llm_api_key = "local-fixture-key"
    tracer = Tracer()
    tracer.request_id = "request-123"
    tracer.conversation_id = "conversation-123"
    tracer.call_budget = ModelCallBudget(16)
    llm = LLM(settings, tracer)
    await llm.client.close()
    completions = Completions(*responses)
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return llm, tracer, completions


def calls(tracer, status):
    return [
        event.data["model_call"] for event in tracer.events
        if (event.data or {}).get("model_call", {}).get("status") == status
    ]


async def test_protocol_retry_is_two_actual_attempts_and_no_private_payload(settings):
    error = BadRequestError(
        "stream_options invalid; private-prompt private-secret",
        response=httpx.Response(400, request=httpx.Request("POST", "https://example.com")),
        body=None,
    )
    usage = SimpleNamespace(prompt_tokens=7, completion_tokens=3, total_tokens=10)
    llm, tracer, completions = await client(settings, error, Stream(usage=usage))
    assert await llm.for_role("researcher").complete("private-prompt", "private-secret") == "done"
    started = calls(tracer, "started")
    failed, succeeded = calls(tracer, "failed"), calls(tracer, "succeeded")
    assert len(started) == len(completions.requests) == tracer.call_budget.used == 2
    assert len(failed) == len(succeeded) == 1
    assert [item["attempt"] for item in started] == [1, 2]
    assert started[0]["operation_id"] == started[1]["operation_id"]
    assert started[0]["call_id"] != started[1]["call_id"]
    assert succeeded[0]["retry_reason"] == "stream_options_unsupported"
    assert succeeded[0]["usage_state"] == "reported"
    assert succeeded[0]["usage"]["total_tokens"] == 10
    assert all(item["role"] == "researcher" for item in started)
    assert all(item["request_id"] == "request-123" for item in started)
    assert "private-prompt" not in str(tracer.events)
    assert "private-secret" not in str(tracer.events)
    assert failed[0]["error_type"] == "BadRequestError"


async def test_parse_retry_has_one_operation_and_distinct_http_ids(settings):
    class Answer(BaseModel):
        value: int

    llm, tracer, completions = await client(settings, Stream('{}'), Stream('{"value": 4}'))
    assert (await llm.parse("system", "user", Answer)).value == 4
    started = calls(tracer, "started")
    assert len(started) == len(completions.requests) == 2
    # HTTP responses succeeded; parsing triggered the retry.
    assert len(calls(tracer, "succeeded")) == 2
    assert started[1]["retry_reason"] == "schema_retry"
    assert started[0]["operation_id"] == started[1]["operation_id"]
    assert started[1]["schema"] == "Answer"


async def test_stream_interruption_is_recorded_once_without_replaying(settings):
    error = APIConnectionError(request=httpx.Request("POST", "https://example.com"))
    llm, tracer, completions = await client(settings, Stream(error=error))
    with pytest.raises(ModelStreamInterrupted):
        _ = [part async for part in llm.stream("system", "user")]
    assert len(completions.requests) == len(calls(tracer, "started")) == 1
    failed = calls(tracer, "failed")
    assert len(failed) == 1
    assert failed[0]["usage_state"] == "unavailable"
    assert failed[0]["usage"] is None


async def test_budget_rejection_is_not_an_actual_http_attempt(settings):
    llm, tracer, completions = await client(settings, Stream(), Stream())
    tracer.call_budget = ModelCallBudget(1)
    await llm.complete("system", "first")
    with pytest.raises(ModelCallLimitExceeded):
        await llm.complete("system", "second")
    assert len(completions.requests) == len(calls(tracer, "started")) == 1


async def test_parallel_role_bindings_do_not_mutate_shared_model(settings):
    llm, tracer, _ = await client(settings, Stream(), Stream())
    first, second = llm.for_role("planner"), llm.for_role("evidence_verifier")
    await asyncio.gather(first.complete("s", "u"), second.complete("s", "u"))
    started = calls(tracer, "started")
    assert {item["role"] for item in started} == {"planner", "evidence_verifier"}
    assert len({item["operation_id"] for item in started}) == 2
    assert llm._call_role is None
    first._prefix_messages_supported = False
    assert second._prefix_messages_supported is False


async def test_cancellation_records_unknown_usage_without_success(settings):
    llm, tracer, _ = await client(settings, Stream(error=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        _ = [part async for part in llm.stream("s", "u")]
    assert len(calls(tracer, "cancelled")) == 1
    assert not calls(tracer, "succeeded")


async def test_interleaved_streams_do_not_leak_operation_context_to_the_caller(settings):
    from deep_research.model_calls import current_operation, model_operation

    llm, tracer, _ = await client(settings, Stream(), Stream())
    with model_operation("structured", role="outer") as outer:
        first, second = llm.stream("s", "u"), llm.stream("s", "u")
        try:
            await anext(first)
            await anext(second)
            assert current_operation() is outer
        finally:
            await first.aclose()
            await second.aclose()
        assert current_operation() is outer
    assert len(calls(tracer, "started")) == len(calls(tracer, "cancelled")) == 2


async def test_provider_input_usage_calibrates_shared_roles_without_extra_model_calls(settings):
    from deep_research.context_budget import ContextBudget

    requests = []

    def respond(request):
        requests.append(request)
        payload = {
            "id": "fixture", "object": "chat.completion.chunk", "created": 0, "model": "fixture",
            "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 500, "completion_tokens": 1, "total_tokens": 501},
        }
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"},
            content="data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n",
        )

    settings.llm_api_key = "fixture-key"
    tracer = Tracer()
    llm = LLM(settings, tracer)
    await llm.client.close()
    llm.client = AsyncOpenAI(
        api_key="fixture-key", base_url="https://example.com/v1", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    first, second = llm.for_role("planner"), llm.for_role("synthesizer")
    before = ContextBudget.from_model(second).estimated_prompt("system", "user")
    try:
        assert await first.complete("system", "user") == "ok"
        after = ContextBudget.from_model(second).estimated_prompt("system", "user")
        assert after > before
        assert first._context_usage_calibration is second._context_usage_calibration
        assert len(requests) == 1
        assert tracer.total_tokens == 501
    finally:
        await llm.aclose()
