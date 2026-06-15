"""全局配置：从环境变量读取，集中管理研究行为参数。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass
class Settings:
    # --- LLM（任意 OpenAI 兼容端点）---
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_base_url: str | None = field(default_factory=lambda: os.getenv("LLM_BASE_URL") or None)
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "gpt-4o-mini"))

    # --- 检索 ---
    tavily_api_key: str = field(default_factory=lambda: os.getenv("TAVILY_API_KEY", ""))

    # --- API 认证（设置后所有 /api 端点要求 X-API-Key 头或 ?api_key= 参数；空则不启用）---
    api_key: str = field(default_factory=lambda: os.getenv("API_KEY", ""))

    # --- 持久化（默认本地 SQLite；生产经 DATABASE_URL 注入 PostgreSQL）---
    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./deep_research.db")
    )

    # --- 研究行为（可经环境变量覆盖；API 亦支持 per-run 覆盖）---
    max_sub_questions: int = field(default_factory=lambda: _int_env("MAX_SUB_QUESTIONS", 5))
    max_rounds: int = field(default_factory=lambda: _int_env("MAX_ROUNDS", 2))
    max_concurrency: int = field(default_factory=lambda: _int_env("MAX_CONCURRENCY", 4))
    results_per_search: int = field(default_factory=lambda: _int_env("RESULTS_PER_SEARCH", 5))

    # --- 网络 ---
    request_timeout: float = field(default_factory=lambda: _float_env("REQUEST_TIMEOUT", 60.0))

    def __post_init__(self) -> None:
        """范围校验：非法配置尽早失败（含 per-run 覆盖经 dataclasses.replace 时）。"""
        if self.max_sub_questions < 1:
            raise ValueError("max_sub_questions 必须 >= 1")
        if self.max_rounds < 0:
            raise ValueError("max_rounds 必须 >= 0")
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency 必须 >= 1")
        if self.results_per_search < 1:
            raise ValueError("results_per_search 必须 >= 1")
        if self.request_timeout <= 0:
            raise ValueError("request_timeout 必须 > 0")

    def validate(self) -> None:
        """构建真实 LLM / 检索后端前调用；测试注入假依赖时可跳过。"""
        pairs = (("LLM_API_KEY", self.llm_api_key), ("TAVILY_API_KEY", self.tavily_api_key))
        missing = [name for name, value in pairs if not value]
        if missing:
            raise RuntimeError(f"缺少环境变量：{', '.join(missing)}（可复制 .env.example 配置）")
