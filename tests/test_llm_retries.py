"""LLM 聚合流式调用：瞬时错误重试、结构化解析回灌、token 记账与输入上限。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from deep_research import llm as llm_module
from deep_research.llm import LLM
from deep_research.observability import Tracer


class _Answer(BaseModel):
    value: int


def _response(content: str, total_tokens: int | None) -> Any:
    usage = SimpleNamespace(total_tokens=total_tokens) if total_tokens is not None else None

    async def chunks():  # type: ignore[no-untyped-def]
        for part in (content[:2], content[2:]):
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=part))], usage=None
            )
        yield SimpleNamespace(choices=[], usage=usage)

    return chunks()


class _ScriptedCompletions:
    """按脚本依次返回响应或抛出异常。"""

    def __init__(self, *steps: Any) -> None:
        self.steps = list(steps)
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


@pytest.fixture
def make_llm(settings, monkeypatch):  # type: ignore[no-untyped-def]
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(llm_module.asyncio, "sleep", fake_sleep)

    def build(*steps: Any) -> tuple[LLM, _ScriptedCompletions, Tracer, list[float]]:
        settings.llm_api_key = "test-key"
        tracer = Tracer()
        llm = LLM(settings, tracer)
        completions = _ScriptedCompletions(*steps)
        llm.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        return llm, completions, tracer, sleeps

    return build


@pytest.mark.asyncio
async def test_complete_retries_transient_errors_with_backoff(make_llm):
    llm, completions, tracer, sleeps = make_llm(TimeoutError(), TimeoutError(), _response("ok", 9))
    assert await llm.complete("s", "u") == "ok"
    assert len(completions.requests) == 3
    assert all(request["stream"] is True for request in completions.requests)
    assert sleeps == [1, 2]
    # 两次超时的用量不确定，按预留记为估算；成功那次按上游精确值
    assert tracer.total_tokens > 9 and tracer.tokens_estimated is True


@pytest.mark.asyncio
async def test_complete_gives_up_after_three_attempts(make_llm):
    llm, completions, _, _ = make_llm(TimeoutError(), TimeoutError(), TimeoutError())
    with pytest.raises(TimeoutError):
        await llm.complete("s", "u")
    assert len(completions.requests) == 3


@pytest.mark.asyncio
async def test_complete_does_not_retry_permanent_errors(make_llm):
    llm, completions, tracer, sleeps = make_llm(ValueError("bad request"))
    with pytest.raises(ValueError):
        await llm.complete("s", "u")
    assert len(completions.requests) == 1 and sleeps == []
    # 确定失败（非网络不确定）的调用不计 token
    assert tracer.total_tokens == 0


@pytest.mark.asyncio
async def test_missing_usage_falls_back_to_estimate(make_llm):
    llm, _, tracer, _ = make_llm(_response("回答内容", None))
    await llm.complete("系统", "问题")
    assert tracer.total_tokens > 0 and tracer.tokens_estimated is True


@pytest.mark.asyncio
async def test_input_over_limit_is_rejected_before_calling_provider(make_llm, settings):
    llm, completions, _, _ = make_llm()
    settings.llm_max_input_chars = 10
    with pytest.raises(ValueError, match="LLM_MAX_INPUT_CHARS"):
        await llm.complete("system", "user text")
    assert completions.requests == []


@pytest.mark.asyncio
async def test_parse_feeds_back_invalid_json_then_succeeds(make_llm):
    llm, completions, _, _ = make_llm(
        _response("not json", 5), _response('```json\n{"value": 7}\n```', 5)
    )
    answer = await llm.parse("s", "u", _Answer)
    assert answer.value == 7
    assert all(request["stream"] is True for request in completions.requests)
    retry_user = completions.requests[1]["messages"][1]["content"]
    assert "上次输出无法解析" in retry_user


@pytest.mark.asyncio
async def test_parse_retries_transient_error_and_reports_exhausted_parse(make_llm):
    llm, completions, _, sleeps = make_llm(
        TimeoutError(), _response('{"value": "x"}', 5), _response("{}", 5)
    )
    with pytest.raises(ValueError, match="结构化输出解析失败"):
        await llm.parse("s", "u", _Answer, retries=2)
    assert len(completions.requests) == 3 and sleeps == [1]


@pytest.mark.asyncio
async def test_parse_reraises_network_error_when_budget_exhausted(make_llm):
    llm, _, _, _ = make_llm(TimeoutError())
    with pytest.raises(TimeoutError):
        await llm.parse("s", "u", _Answer, retries=0)
