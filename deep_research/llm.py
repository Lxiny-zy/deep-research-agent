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
from typing import TypeVar

from openai import AsyncOpenAI
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
        )

    async def aclose(self) -> None:
        """关闭底层 httpx 连接池（AsyncOpenAI 不关闭只能靠 GC 兜底，会泄漏 FD）。"""
        await self.client.close()

    async def complete(self, system: str, user: str, *, temperature: float = 0.3) -> str:
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=temperature,
        )
        self.tracer.add_tokens(_tokens(resp))
        return resp.choices[0].message.content or ""

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
                    temperature=temperature,
                )
            except Exception:  # 429/5xx/超时等瞬时故障：退避后重试，而非直接终结整个 run
                if attempt < attempts - 1:
                    await asyncio.sleep(min(2**attempt, 8))
                    continue
                raise  # 重试预算耗尽：原样抛出网络层异常，便于上层区分
            self.tracer.add_tokens(_tokens(resp))
            raw = resp.choices[0].message.content or ""
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
        DeepSeek / Qwen / GLM / Moonshot 等端点均可用。流式响应通常不返回精确
        usage，这里在结束时按字符数粗略估算 token（仅用于观测，非计费依据）。
        """
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=temperature,
            stream=True,
        )
        out_chars = 0
        async for chunk in resp:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                out_chars += len(delta)
                yield delta
        # 流式通常无精确 usage：约 2 字符/token 粗略估算（含输入）
        self.tracer.add_tokens((len(system) + len(user) + out_chars) // 2)


def _tokens(resp: object) -> int:
    usage = getattr(resp, "usage", None)
    return int(getattr(usage, "total_tokens", 0) or 0) if usage else 0
