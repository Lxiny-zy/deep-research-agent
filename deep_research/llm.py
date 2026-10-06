"""LLM 封装：普通补全 + 结构化补全 + 流式补全。

结构化补全不依赖任何 provider 私有特性（如 OpenAI 的 response_format），
而是「把 JSON Schema 注入 prompt + 稳健抽取 JSON + 校验失败自动重试」，
因此可无缝接入 OpenAI / DeepSeek / Qwen / GLM / Moonshot 等任意兼容端点。
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing, suppress
from typing import Any, TypedDict, TypeVar
from uuid import uuid4

from openai import APIConnectionError, APIStatusError, AsyncOpenAI
from pydantic import BaseModel

from .config import Settings
from .context_budget import ContextBudget, TokenEstimator, estimate_tokens, observe_context_usage
from .model_calls import ModelCall, ModelOperation, current_operation, model_operation
from .observability import Tracer
from .prompting import prompt_messages, structured_system_prompt
from .provider_limits import provider_request
from .security import provider_http_client
from .token_budget import TokenBudget, TokenReservation

T = TypeVar("T", bound=BaseModel)  # 3.11 兼容写法（不用 3.12 的 def f[T]() 语法）


class VerificationGenerationOptions(TypedDict, total=False):
    reasoning_effort: str


def verification_generation_options(llm: Any) -> VerificationGenerationOptions:
    """Use low reasoning for routine checks only when the profile supports it."""
    from .generation_policy import generation_options

    return generation_options(llm, "verification")


class ModelOutputTruncated(RuntimeError):
    def __init__(self, output_limit: int) -> None:
        self.output_limit = output_limit
        super().__init__(
            "模型输出被渠道截断，尚未生成完整结果；这不表示论文缺少依据。"
            "请在模型档案中设置该渠道支持的最大输出容量（包含思考与正文）"
        )


class ModelStreamInterrupted(RuntimeError):
    def __init__(self) -> None:
        super().__init__("模型已返回部分思考或正文，但连接中断；未自动重发同一请求。")


def extract_json(text: str) -> dict:
    """从模型输出稳健抽取 JSON：兼容 ```json 代码块与前后多余文本。"""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


class InputCapacityError(ValueError):
    """An explicitly configured model context cannot fit the request."""


class LLM:
    def __init__(self, settings: Settings, tracer: Tracer) -> None:
        self.settings = settings
        self.tracer = tracer
        self.model = settings.llm_model
        self._transport_capabilities = {"prefix_messages": True}
        self._context_usage_calibration: dict[str, float] = {}
        if self.tracer.budget is None:
            self.tracer.budget = TokenBudget()
        self.client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=settings.request_timeout,
            max_retries=0,
            http_client=provider_http_client(
                allow_private=settings.allow_private_provider_urls,
                timeout=settings.request_timeout,
            ),
            # 覆盖 SDK 默认 UA：上游网关（Cloudflare 等）可能拦 "OpenAI/Python ..."
            default_headers={"User-Agent": settings.llm_user_agent},
        )

    @classmethod
    def from_params(
        cls,
        tracer: Tracer,
        *,
        api_key: str,
        base_url: str | None,
        model: str,
        timeout: float,
        user_agent: str,
        temperature: float = 0.3,
        parameter_mode: str = "temperature",
        reasoning_effort: str = "medium",
        context_window_tokens: int | None = None,
        max_output_tokens: int | None = None,
        allow_private_provider_urls: bool = False,
    ) -> LLM:
        """按模型档案的显式参数构造（角色绑不同模型档案时用）。

        复用 __init__ 的 client 配置，但不经全局 Settings——每个角色可有独立
        base_url/key/model/temperature。temperature 作为该档案的默认采样温度。
        """
        from dataclasses import replace

        s = replace(
            Settings(),  # 以环境变量默认打底，再覆盖档案关键字段
            llm_api_key=api_key,
            llm_base_url=base_url or None,
            llm_model=model,
            llm_user_agent=user_agent,
            request_timeout=timeout,
            llm_max_output_tokens=max_output_tokens or 0,
            allow_private_provider_urls=allow_private_provider_urls,
        )
        inst = cls(s, tracer)
        inst.default_temperature = temperature
        inst.parameter_mode = parameter_mode
        inst.reasoning_effort = reasoning_effort
        inst.context_window_tokens = context_window_tokens
        return inst

    # ``None`` keeps each agent operation's built-in sampling hint. Catalog
    # profiles set an explicit value which must override those hints.
    default_temperature: float | None = None
    parameter_mode: str = "temperature"
    reasoning_effort: str = "medium"
    context_window_tokens: int | None = None
    _call_role: str | None = None

    def for_role(self, role: str) -> LLM:
        """Bind attribution without mutating a model shared by concurrent roles."""
        bound = copy.copy(self)
        bound._call_role = role
        return bound

    @property
    def _prefix_messages_supported(self) -> bool:
        return self._transport_capabilities["prefix_messages"]

    @_prefix_messages_supported.setter
    def _prefix_messages_supported(self, supported: bool) -> None:
        self._transport_capabilities["prefix_messages"] = supported

    @property
    def input_capacity_chars(self) -> int:
        return ContextBudget.for_limits(
            self.context_window_tokens, self.settings.llm_max_output_tokens,
            self.settings.llm_max_input_chars,
        ).input_capacity_chars

    @property
    def enforced_input_capacity_chars(self) -> int | None:
        # An unspecified model context follows the provider. The fallback
        # character target guides batching; it is not a known model limit.
        return self.input_capacity_chars if self.context_window_tokens is not None else None

    def _generation_options(
        self, temperature: float, *, reasoning_effort: str | None = None
    ) -> dict[str, Any]:
        if self.parameter_mode == "reasoning":
            return {"reasoning_effort": reasoning_effort or self.reasoning_effort}
        configured = self.default_temperature
        return {"temperature": temperature if configured is None else configured}

    async def aclose(self) -> None:
        """关闭底层 httpx 连接池（AsyncOpenAI 不关闭只能靠 GC 兜底，会泄漏 FD）。"""
        await self.client.close()

    async def complete(self, system: str, user: str, *, temperature: float = 0.3) -> str:
        with model_operation("complete", role=self._call_role) as operation:
            for attempt in range(3):
                try:
                    return await self._complete_once(system, user, temperature)
                except Exception as exc:
                    if not _retryable(exc) or attempt == 2:
                        raise
                    operation.retry_reason = "transport_retry"
                    await asyncio.sleep(min(2**attempt, 8))
        raise AssertionError("unreachable")

    def _reserve(self, system: str, user: str) -> TokenReservation:
        budget = ContextBudget.from_model(self, self.settings.llm_max_input_chars)
        estimated = budget.estimated_prompt(system, user)
        if budget.enforced and not budget.fits(system, user):
            raise InputCapacityError(
                f"模型输入超过已配置的上下文容量（保守估算 {estimated} tokens，"
                f"可用输入 {budget.input_tokens} tokens；非渠道精确分词）"
            )
        assert self.tracer.budget is not None
        self.tracer.budget.update(self.tracer.total_tokens)
        # UTF-8 bytes plus framing are a conservative admission estimate, not a billing claim.
        return self.tracer.budget.reserve(
            # A reservation estimate is NOT sent as an output cap when the
            # profile uses the provider default. Exact usage replaces estimates.
            estimated,
            budget.output_tokens,
        )

    def _output_options(self, reservation: TokenReservation) -> dict[str, Any]:
        if not self.settings.llm_max_output_tokens:
            return {}
        key = "max_completion_tokens" if self.parameter_mode == "reasoning" else "max_tokens"
        return {key: reservation.output_tokens}

    async def _complete_once(
        self, system: str, user: str, temperature: float, *, reasoning_effort: str | None = None
    ) -> str:
        # Structured consumers still receive one validated value, while the
        # provider transport streams and usage updates throughout generation.
        parts: list[str] = []
        options: VerificationGenerationOptions = (
            {"reasoning_effort": reasoning_effort} if reasoning_effort is not None else {}
        )
        async with (
            provider_request(
                self.settings.llm_base_url or "https://api.openai.com", self.settings.llm_api_key
            ),
            aclosing(self._stream_once(system, user, temperature=temperature, **options)) as stream,
        ):
            async for delta in stream:
                parts.append(delta)
        return "".join(parts)

    async def parse(
        self, system: str, user: str, schema: type[T], *,
        temperature: float = 0.2, retries: int = 2,
        reasoning_effort: str | None = None,
    ) -> T:
        """要求模型只输出符合 schema 的 JSON，再用 Pydantic 校验；失败自动重试。

        retries 是「额外重试次数」（总尝试 = retries + 1）。瞬时网络/限流异常与
        解析失败共用同一重试预算：前者指数退避后重发，后者把错误回灌给模型再试。
        """
        sys = structured_system_prompt(system, schema)
        err: Exception | None = None
        attempts = max(1, retries + 1)
        options: VerificationGenerationOptions = (
            {"reasoning_effort": reasoning_effort} if reasoning_effort is not None else {}
        )
        with model_operation(
            "structured", role=self._call_role, schema=schema.__name__,
        ) as operation:
            for attempt in range(attempts):
                try:
                    raw = await self._complete_once(sys, user, temperature, **options)
                except Exception as exc:
                    if _retryable(exc) and attempt < attempts - 1:
                        operation.retry_reason = "transport_retry"
                        await asyncio.sleep(min(2**attempt, 8))
                        continue
                    raise  # 重试预算耗尽：原样抛出网络层异常，便于上层区分
                try:
                    return schema.model_validate(extract_json(raw))
                except Exception as e:  # JSON 非法或字段缺失 → 把错误回灌再试
                    err = e
                    operation.retry_reason = "schema_retry"
                    user = user + f"\n\n（上次输出无法解析：{e}；请只输出合法 JSON）"
        raise ValueError(f"结构化输出解析失败：{err}")

    async def stream(
        self, system: str, user: str, *, temperature: float = 0.4
    ) -> AsyncIterator[str]:
        # 建连阶段的瞬时故障（限流、网关超时）重试最多 3 次；一旦已经产出过增量就
        # 不再重试——重发会把半截正文重复拼进交付物，这时把异常交给调用方处理。
        # Do not hold a ContextVar token across yields to a caller. Multiple
        # streams may be consumed or closed in a different order in one task.
        operation = ModelOperation(kind="stream", role=self._call_role)
        for attempt in range(3):
            emitted = False
            try:
                async with (
                    provider_request(
                        self.settings.llm_base_url or "https://api.openai.com",
                        self.settings.llm_api_key,
                    ),
                    aclosing(self._stream_once(
                        system, user, temperature=temperature, operation=operation,
                    )) as stream,
                ):
                    async for delta in stream:
                        emitted = True
                        yield delta
                return
            except Exception as exc:
                if emitted or not _retryable(exc) or attempt == 2:
                    raise
                operation.retry_reason = "transport_retry"
                await asyncio.sleep(min(2**attempt, 8))

    async def _stream_once(
        self, system: str, user: str, *, temperature: float = 0.4,
        reasoning_effort: str | None = None,
        operation: ModelOperation | None = None,
    ) -> AsyncGenerator[str, None]:
        """流式补全：逐块产出文本增量。

        使用 OpenAI 兼容的标准 stream=True，不依赖任何 provider 私有扩展，
        DeepSeek / Qwen / GLM / Moonshot 等端点均可用。生成过程中按字符增量估算
        token 供 UI 实时展示；若端点最终返回 usage，则自动用精确值校准。
        """
        reservation = self._reserve(system, user)
        operation = operation or current_operation()
        assert self.tracer.budget is not None
        affinity = self._cache_affinity()
        request = {
            "model": self.model,
            "messages": prompt_messages(system, user, split=self._prefix_messages_supported),
            **self._generation_options(temperature, reasoning_effort=reasoning_effort),
            **self._output_options(reservation),
            "stream": True,
            # Fireworks routes matching prefixes most effectively to one
            # replica. The standard user field also survives many gateways.
            "user": affinity,
            "extra_headers": {"x-session-affinity": affinity},
        }
        resp = None
        usage_report: dict[str, int | None] | None = None
        call: ModelCall | None = None
        call_id = uuid4().hex
        reasoning_parts: list[str] = []
        last_reasoning_emit = 0.0
        finish_reason: str | None = None

        def flush_reasoning() -> None:
            nonlocal last_reasoning_emit
            if reasoning_parts:
                self.tracer.emit(
                    "LLM",
                    "info",
                    "模型返回的思考内容",
                    data={
                        "reasoning_delta": "".join(reasoning_parts),
                        "call_id": call_id,
                        "model": self.model,
                    },
                )
                reasoning_parts.clear()
                last_reasoning_emit = time.monotonic()

        estimated_added = 0
        output_started = False
        try:
            include_usage = True
            protocol_retry: str | None = None
            while True:
                try:
                    stream_options: dict[str, Any] = (
                        {"stream_options": {"include_usage": True}} if include_usage else {}
                    )
                    call_budget = getattr(self.tracer, "call_budget", None)
                    if call_budget is not None:
                        call_budget.reserve()
                    call = ModelCall(
                        self.tracer, self.model, operation=operation, retry_reason=protocol_retry,
                    )
                    call_id = call.id
                    resp = await self.client.chat.completions.create(
                        **{**request, **stream_options},
                    )
                    break
                except APIStatusError as exc:
                    if call is not None:
                        call.finish(error=exc)
                    message = str(exc).lower()
                    if exc.status_code not in {400, 422}:
                        raise
                    if include_usage and "stream_options" in message:
                        include_usage = False
                        protocol_retry = "stream_options_unsupported"
                    elif (
                        len(request["messages"]) > 2
                        and "user" in message
                        and any(
                            word in message for word in ("alternat", "consecutive", "multiple user")
                        )
                    ):
                        # Some chat templates require alternating roles. Fall
                        # back only on an explicit pre-generation schema error.
                        self._prefix_messages_supported = False
                        request["messages"] = prompt_messages(system, user, split=False)
                        protocol_retry = "message_roles_unsupported"
                    else:
                        raise
            usage_report = _header_usage(resp)
            input_estimate = reservation.input_tokens
            self.tracer.add_tokens(input_estimate, estimated=True)
            estimated_added = input_estimate
            accounted_output = exact_usage = 0
            output_estimator = TokenEstimator()
            async for chunk in resp:
                exact_usage = _tokens(chunk) or exact_usage
                reported = _usage_details(chunk)
                if reported is not None:
                    usage_report = {
                        **(usage_report or {}),
                        **{key: value for key, value in reported.items() if value is not None},
                    }
                if not chunk.choices:
                    continue
                finish_reason = getattr(chunk.choices[0], "finish_reason", None) or finish_reason
                fragment = chunk.choices[0].delta
                delta = getattr(fragment, "content", None)
                # Only explicit, displayable provider fields; never infer
                # reasoning from answer text or decode opaque reasoning data.
                reasoning = getattr(fragment, "reasoning_summary", None) or getattr(
                    fragment, "reasoning_content", None
                )
                reasoning = reasoning if isinstance(reasoning, str) else ""
                if reasoning:
                    reasoning_parts.append(reasoning)
                    if time.monotonic() - last_reasoning_emit >= 0.15:
                        flush_reasoning()
                if delta or reasoning:
                    output_started = True
                    output_estimator.feed(reasoning)
                    output_estimate = output_estimator.feed(delta or "")
                    increment = output_estimate - accounted_output
                    if increment > 0:
                        self.tracer.add_tokens(increment, estimated=True)
                        estimated_added += increment
                        accounted_output = output_estimate
                    if delta:
                        yield delta
            if exact_usage > 0:
                self.tracer.reconcile_tokens(estimated_added, exact_usage)
            if usage_report is not None:
                observe_context_usage(self, system, user, usage_report.get("input_tokens"))
                inputs = usage_report.get("input_tokens")
                cached = usage_report.get("cached_input_tokens")
                if inputs is not None and cached is not None and cached > inputs:
                    usage_report["cached_input_tokens"] = None
                self.tracer.emit(
                    "LLM",
                    "info",
                    "模型用量已返回",
                    data={
                        "llm_usage": {
                            "model": self.model,
                            "call_id": call_id,
                            "finish_reason": finish_reason,
                            **({"reasoning_effort": request["reasoning_effort"]}
                               if "reasoning_effort" in request else {}),
                            "cache_affinity": affinity,
                            **usage_report,
                        }
                    },
                )
            if finish_reason == "length" or (
                finish_reason is None
                and self.settings.llm_max_output_tokens > 0
                and usage_report is not None
                and (usage_report.get("output_tokens") or 0) >= reservation.output_tokens
            ):
                raise ModelOutputTruncated(self.settings.llm_max_output_tokens)
            if call is not None:
                call.finish(usage=usage_report, finish_reason=finish_reason)
        except BaseException as exc:
            if call is not None:
                call.finish(error=exc, usage=usage_report, finish_reason=finish_reason)
            if isinstance(exc, (asyncio.CancelledError, GeneratorExit)) or _uncertain_usage(exc):
                self.tracer.add_tokens(max(0, reservation.total - estimated_added), estimated=True)
            if output_started and _retryable(exc):
                raise ModelStreamInterrupted() from exc
            raise
        finally:
            flush_reasoning()
            self.tracer.budget.release(reservation)
            if resp is not None and hasattr(resp, "close"):
                # A cleanup error after a completed response must not initiate
                # another billed generation or replace the successful answer.
                with suppress(Exception):
                    await resp.close()

    def _cache_affinity(self) -> str:
        identity = json.dumps(
            [
                self.settings.llm_base_url,
                self.settings.llm_api_key,
                self.model,
                self.tracer.cache_scope,
            ],
            ensure_ascii=False,
        )
        return "dr-" + hashlib.sha256(identity.encode()).hexdigest()[:48]


def _retryable(error: BaseException) -> bool:
    return isinstance(error, (APIConnectionError, TimeoutError)) or (
        isinstance(error, APIStatusError)
        and (error.status_code in {408, 409, 429} or error.status_code >= 500)
    )


def _uncertain_usage(error: BaseException) -> bool:
    return isinstance(error, (APIConnectionError, TimeoutError)) or (
        isinstance(error, APIStatusError) and error.status_code >= 500
    )


def _tokens(resp: object) -> int:
    usage = getattr(resp, "usage", None)
    return int(getattr(usage, "total_tokens", 0) or 0) if usage else 0


def _usage_details(resp: object) -> dict[str, int | None] | None:
    """Keep provider-reported cache usage distinct from missing statistics."""
    usage = getattr(resp, "usage", None)
    if usage is None:
        return None

    def value(obj: object, name: str) -> object:
        return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)

    def count(raw: object) -> int | None:
        return raw if type(raw) is int and raw >= 0 else None

    inputs = count(value(usage, "prompt_tokens"))
    outputs = count(value(usage, "completion_tokens"))
    cached = count(value(value(usage, "prompt_tokens_details"), "cached_tokens"))
    if cached is None:
        cached = count(value(usage, "prompt_cache_hit_tokens"))
    if inputs is not None and cached is not None and cached > inputs:
        cached = None
    return {
        "total_tokens": count(value(usage, "total_tokens")),
        "input_tokens": inputs,
        "output_tokens": outputs,
        "cached_input_tokens": cached,
        "reasoning_tokens": count(
            value(value(usage, "completion_tokens_details"), "reasoning_tokens")
        ),
    }


def _header_usage(stream: object) -> dict[str, int | None] | None:
    """Fireworks dedicated deployments can report cache hits in HTTP headers."""
    headers = getattr(getattr(stream, "response", None), "headers", None)
    if headers is None:
        return None

    def count(name: str) -> int | None:
        raw = headers.get(name)
        return int(raw) if isinstance(raw, str) and raw.isascii() and raw.isdigit() else None

    inputs = count("fireworks-prompt-tokens")
    cached = count("fireworks-cached-prompt-tokens")
    if inputs is None and cached is None:
        return None
    if inputs is not None and cached is not None and cached > inputs:
        cached = None
    return {
        "input_tokens": inputs,
        "cached_input_tokens": cached,
        "output_tokens": None,
        "reasoning_tokens": None,
    }


def _estimate_tokens(*parts: str) -> int:
    """兼容中英文的保守观测估算；只用于实时 UI，绝不宣称为账单值。"""
    return max(1, estimate_tokens(*parts))
