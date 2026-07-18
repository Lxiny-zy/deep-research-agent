"""LLM 封装：普通补全 + 结构化补全 + 流式补全。

结构化补全不依赖任何 provider 私有特性（如 OpenAI 的 response_format），
而是「把 JSON Schema 注入 prompt + 稳健抽取 JSON + 校验失败自动重试」，
因此可无缝接入 OpenAI / DeepSeek / Qwen / GLM / Moonshot 等任意兼容端点。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Any, TypeVar

from openai import APIStatusError, AsyncOpenAI
from pydantic import BaseModel

from .config import Settings
from .observability import Tracer

T = TypeVar("T", bound=BaseModel)  # 3.11 兼容写法（不用 3.12 的 def f[T]() 语法）


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
        self.client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=settings.request_timeout,
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
        )
        inst = cls(s, tracer)
        inst.default_temperature = temperature
        inst.parameter_mode = parameter_mode
        inst.reasoning_effort = reasoning_effort
        return inst

    default_temperature: float = 0.3
    parameter_mode: str = "temperature"
    reasoning_effort: str = "medium"

    def _generation_options(self, temperature: float) -> dict[str, Any]:
        if self.parameter_mode == "reasoning":
            return {"reasoning_effort": self.reasoning_effort}
        return {"temperature": temperature}

    async def aclose(self) -> None:
        """关闭底层 httpx 连接池（AsyncOpenAI 不关闭只能靠 GC 兜底，会泄漏 FD）。"""
        await self.client.close()

    async def complete(self, system: str, user: str, *, temperature: float = 0.3) -> str:
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            **self._generation_options(temperature),
        )
        content = resp.choices[0].message.content or ""
        usage = _tokens(resp)
        self.tracer.add_tokens(
            usage or _estimate_tokens(system, user, content), estimated=usage == 0
        )
        return content

    async def parse(
        self, system: str, user: str, schema: type[T], *, temperature: float = 0.2, retries: int = 2
    ) -> T:
        """要求模型只输出符合 schema 的 JSON，再用 Pydantic 校验；失败自动重试。

        retries 是「额外重试次数」（总尝试 = retries + 1）。瞬时网络/限流异常与
        解析失败共用同一重试预算：前者指数退避后重发，后者把错误回灌给模型再试。
        """
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        sys = (
            f"{system}\n\n【输出要求】只输出一个 JSON 对象，禁止解释、禁止 markdown 代码块。"
            f"必须严格符合以下 JSON Schema：\n{schema_json}"
        )
        err: Exception | None = None
        attempts = max(1, retries + 1)
        for attempt in range(attempts):
            try:
                resp = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": sys},
                        {"role": "user", "content": user},
                    ],
                    **self._generation_options(temperature),
                )
            except Exception:  # 429/5xx/超时等瞬时故障：退避后重试，而非直接终结整个 run
                if attempt < attempts - 1:
                    await asyncio.sleep(min(2**attempt, 8))
                    continue
                raise  # 重试预算耗尽：原样抛出网络层异常，便于上层区分
            raw = resp.choices[0].message.content or ""
            usage = _tokens(resp)
            self.tracer.add_tokens(
                usage or _estimate_tokens(sys, user, raw), estimated=usage == 0
            )
            try:
                return schema.model_validate(extract_json(raw))
            except Exception as e:  # JSON 非法或字段缺失 → 把错误回灌再试
                err = e
                user = f"{user}\n\n（上次输出无法解析：{e}；请只输出合法 JSON）"
        raise ValueError(f"结构化输出解析失败：{err}")

    async def stream(
        self, system: str, user: str, *, temperature: float = 0.4
    ) -> AsyncIterator[str]:
        """流式补全：逐块产出文本增量。

        使用 OpenAI 兼容的标准 stream=True，不依赖任何 provider 私有扩展，
        DeepSeek / Qwen / GLM / Moonshot 等端点均可用。生成过程中按字符增量估算
        token 供 UI 实时展示；若端点最终返回 usage，则自动用精确值校准。
        """
        request = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            **self._generation_options(temperature),
            "stream": True,
        }
        try:
            resp = await self.client.chat.completions.create(
                **request, stream_options={"include_usage": True}
            )
        except APIStatusError as exc:
            # 部分兼容端点未实现 stream_options；只对参数不支持类错误降级重试。
            if exc.status_code not in {400, 422}:
                raise
            resp = await self.client.chat.completions.create(**request)

        input_estimate = _estimate_tokens(system, user)
        self.tracer.add_tokens(input_estimate, estimated=True)
        estimated_added = input_estimate
        out_chars = 0
        accounted_output = 0
        exact_usage = 0
        async for chunk in resp:
            exact_usage = _tokens(chunk) or exact_usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                out_chars += len(delta)
                output_estimate = out_chars // 2
                increment = output_estimate - accounted_output
                if increment > 0:
                    self.tracer.add_tokens(increment, estimated=True)
                    estimated_added += increment
                    accounted_output = output_estimate
                yield delta
        tail = (out_chars + 1) // 2 - accounted_output
        if tail > 0:
            self.tracer.add_tokens(tail, estimated=True)
            estimated_added += tail
        if exact_usage > 0:
            self.tracer.reconcile_tokens(estimated_added, exact_usage)


def _tokens(resp: object) -> int:
    usage = getattr(resp, "usage", None)
    return int(getattr(usage, "total_tokens", 0) or 0) if usage else 0


def _estimate_tokens(*parts: str) -> int:
    """兼容中英文的保守观测估算；只用于实时 UI，绝不宣称为账单值。"""
    return max(1, (sum(len(part) for part in parts) + 1) // 2)
