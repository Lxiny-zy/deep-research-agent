"""LLM 封装：普通补全 + 结构化补全 + 流式补全。

结构化补全不依赖任何 provider 私有特性（如 OpenAI 的 response_format），
而是「把 JSON Schema 注入 prompt + 稳健抽取 JSON + 校验失败自动重试」，
因此可无缝接入 OpenAI / DeepSeek / Qwen / GLM / Moonshot 等任意兼容端点。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing
from typing import Any, TypeVar
from uuid import uuid4

from openai import APIConnectionError, APIStatusError, AsyncOpenAI
from pydantic import BaseModel

from .config import Settings
from .observability import Tracer
from .prompting import structured_system_prompt
from .provider_limits import provider_request
from .security import provider_http_client
from .token_budget import TokenBudget, TokenReservation

T = TypeVar("T", bound=BaseModel)  # 3.11 兼容写法（不用 3.12 的 def f[T]() 语法）


class ModelOutputTruncated(RuntimeError):
    def __init__(self, output_limit: int) -> None:
        self.output_limit = output_limit
        super().__init__(
            "模型输出被渠道截断，尚未生成完整结果；这不表示论文缺少依据。"
            "请在模型档案中设置该渠道支持的最大输出容量（包含思考与正文）"
        )


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


class LLM:
    def __init__(self, settings: Settings, tracer: Tracer) -> None:
        self.settings = settings
        self.tracer = tracer
        self.model = settings.llm_model
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

    @property
    def input_capacity_chars(self) -> int:
        # Same approximate character/token ratio as runtime telemetry. This is
        # planning guidance, not the provider's tokenizer or a billing claim.
        if self.context_window_tokens is not None:
            return max(1, self.context_window_tokens - self.settings.llm_max_output_tokens) * 2
        return self.settings.llm_max_input_chars

    def _generation_options(self, temperature: float) -> dict[str, Any]:
        if self.parameter_mode == "reasoning":
            return {"reasoning_effort": self.reasoning_effort}
        configured = self.default_temperature
        return {"temperature": temperature if configured is None else configured}

    async def aclose(self) -> None:
        """关闭底层 httpx 连接池（AsyncOpenAI 不关闭只能靠 GC 兜底，会泄漏 FD）。"""
        await self.client.close()

    async def complete(self, system: str, user: str, *, temperature: float = 0.3) -> str:
        for attempt in range(3):
            try:
                return await self._complete_once(system, user, temperature)
            except Exception as exc:
                if not _retryable(exc) or attempt == 2:
                    raise
                await asyncio.sleep(min(2**attempt, 8))
        raise AssertionError("unreachable")

    def _reserve(self, system: str, user: str) -> TokenReservation:
        if len(system) + len(user) > self.input_capacity_chars:
            raise ValueError("模型输入超过 LLM_MAX_INPUT_CHARS 限制")
        assert self.tracer.budget is not None
        self.tracer.budget.update(self.tracer.total_tokens)
        # UTF-8 bytes plus framing are a conservative admission estimate, not a billing claim.
        return self.tracer.budget.reserve(
            # A reservation estimate is NOT sent as an output cap when the
            # profile uses the provider default. Exact usage replaces estimates.
            len(system.encode()) + len(user.encode()) + 128,
            self.settings.llm_max_output_tokens or 8192,
        )

    def _output_options(self, reservation: TokenReservation) -> dict[str, Any]:
        if not self.settings.llm_max_output_tokens:
            return {}
        key = "max_completion_tokens" if self.parameter_mode == "reasoning" else "max_tokens"
        return {key: reservation.output_tokens}

    async def _complete_once(self, system: str, user: str, temperature: float) -> str:
        # Structured consumers still receive one validated value, while the
        # provider transport streams and usage updates throughout generation.
        parts: list[str] = []
        async with (
            provider_request(
                self.settings.llm_base_url or "https://api.openai.com", self.settings.llm_api_key
            ),
            aclosing(self._stream_once(system, user, temperature=temperature)) as stream,
        ):
            async for delta in stream:
                parts.append(delta)
        return "".join(parts)

    async def parse(
        self, system: str, user: str, schema: type[T], *, temperature: float = 0.2, retries: int = 2
    ) -> T:
        """要求模型只输出符合 schema 的 JSON，再用 Pydantic 校验；失败自动重试。

        retries 是「额外重试次数」（总尝试 = retries + 1）。瞬时网络/限流异常与
        解析失败共用同一重试预算：前者指数退避后重发，后者把错误回灌给模型再试。
        """
        sys = structured_system_prompt(system, schema)
        err: Exception | None = None
        attempts = max(1, retries + 1)
        for attempt in range(attempts):
            try:
                raw = await self._complete_once(sys, user, temperature)
            except Exception as exc:
                if _retryable(exc) and attempt < attempts - 1:
                    await asyncio.sleep(min(2**attempt, 8))
                    continue
                raise  # 重试预算耗尽：原样抛出网络层异常，便于上层区分
            try:
                return schema.model_validate(extract_json(raw))
            except Exception as e:  # JSON 非法或字段缺失 → 把错误回灌再试
                err = e
                user = f"{user}\n\n（上次输出无法解析：{e}；请只输出合法 JSON）"
        raise ValueError(f"结构化输出解析失败：{err}")

    async def stream(
        self, system: str, user: str, *, temperature: float = 0.4
    ) -> AsyncIterator[str]:
        # 建连阶段的瞬时故障（限流、网关超时）重试最多 3 次；一旦已经产出过增量就
        # 不再重试——重发会把半截正文重复拼进交付物，这时把异常交给调用方处理。
        for attempt in range(3):
            emitted = False
            try:
                async with (
                    provider_request(
                        self.settings.llm_base_url or "https://api.openai.com",
                        self.settings.llm_api_key,
                    ),
                    aclosing(self._stream_once(system, user, temperature=temperature)) as stream,
                ):
                    async for delta in stream:
                        emitted = True
                        yield delta
                return
            except Exception as exc:
                if emitted or not _retryable(exc) or attempt == 2:
                    raise
                await asyncio.sleep(min(2**attempt, 8))

    async def _stream_once(
        self, system: str, user: str, *, temperature: float = 0.4
    ) -> AsyncGenerator[str, None]:
        """流式补全：逐块产出文本增量。

        使用 OpenAI 兼容的标准 stream=True，不依赖任何 provider 私有扩展，
        DeepSeek / Qwen / GLM / Moonshot 等端点均可用。生成过程中按字符增量估算
        token 供 UI 实时展示；若端点最终返回 usage，则自动用精确值校准。
        """
        reservation = self._reserve(system, user)
        assert self.tracer.budget is not None
        affinity = self._cache_affinity()
        request = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            **self._generation_options(temperature),
            **self._output_options(reservation),
            "stream": True,
            # Fireworks routes matching prefixes most effectively to one
            # replica. The standard user field also survives many gateways.
            "user": affinity,
            "extra_headers": {"x-session-affinity": affinity},
        }
        resp = None
        usage_report: dict[str, int | None] | None = None
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
        try:
            try:
                resp = await self.client.chat.completions.create(
                    **request, stream_options={"include_usage": True}
                )
            except APIStatusError as exc:
                if exc.status_code not in {400, 422} or "stream_options" not in str(exc):
                    raise
                resp = await self.client.chat.completions.create(**request)
            usage_report = _header_usage(resp)
            input_estimate = _estimate_tokens(system, user)
            self.tracer.add_tokens(input_estimate, estimated=True)
            estimated_added = input_estimate
            out_chars = accounted_output = exact_usage = 0
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
                    out_chars += len(delta or "") + len(reasoning)
                    output_estimate = out_chars // 2
                    increment = output_estimate - accounted_output
                    if increment > 0:
                        self.tracer.add_tokens(increment, estimated=True)
                        estimated_added += increment
                        accounted_output = output_estimate
                    if delta:
                        yield delta
            tail = (out_chars + 1) // 2 - accounted_output
            if tail > 0:
                self.tracer.add_tokens(tail, estimated=True)
                estimated_added += tail
            if exact_usage > 0:
                self.tracer.reconcile_tokens(estimated_added, exact_usage)
            if usage_report is not None:
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
        except BaseException as exc:
            if isinstance(exc, (asyncio.CancelledError, GeneratorExit)) or _uncertain_usage(exc):
                self.tracer.add_tokens(max(0, reservation.total - estimated_added), estimated=True)
            raise
        finally:
            flush_reasoning()
            self.tracer.budget.release(reservation)
            if resp is not None and hasattr(resp, "close"):
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
    return max(1, (sum(len(part) for part in parts) + 1) // 2)
