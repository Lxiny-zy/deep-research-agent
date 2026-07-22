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


def _int_env_opt(name: str) -> int | None:
    """可选整型环境变量：未设置返回 None（语义：不限 / 采用内置默认）。"""
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# 部分中转/网关前置 Cloudflare 等 WAF，会按 User-Agent 拦截 openai SDK 默认的
# "OpenAI/Python ..."（Bot Fight Mode）。默认伪装成常见浏览器 UA 以放行，可经
# LLM_USER_AGENT 覆盖（若上游无此限制可设任意值，不影响功能）。
_DEFAULT_LLM_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class Settings:
    # --- LLM（任意 OpenAI 兼容端点）---
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_base_url: str | None = field(default_factory=lambda: os.getenv("LLM_BASE_URL") or None)
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "gpt-4o-mini"))
    llm_user_agent: str = field(
        default_factory=lambda: os.getenv("LLM_USER_AGENT") or _DEFAULT_LLM_UA
    )

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

    # 单次研究累计 token 预算上限（防反思/补洞无限烧）；None＝不限。引擎以 Tracer 累计为准，
    # 耗尽则跳过后续研究/反思但仍综合，产出尽力而为的部分报告而非报错。
    max_tokens: int | None = field(default_factory=lambda: _int_env_opt("MAX_TOKENS"))
    # 自组合（auto 流程）生成的流程执行失败/零产出时，Coordinator 重规划的最大次数。
    max_replans: int = field(default_factory=lambda: _int_env("MAX_REPLANS", 1))

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
        if self.max_tokens is not None and self.max_tokens < 1:
            raise ValueError("max_tokens 必须 >= 1 或为 None（不限）")
        if self.max_replans < 0:
            raise ValueError("max_replans 必须 >= 0")
        if self.request_timeout <= 0:
            raise ValueError("request_timeout 必须 > 0")

    def validate_llm(self) -> None:
        """Validate only the credentials needed to construct the default LLM."""
        if not self.llm_api_key:
            raise RuntimeError("缺少环境变量：LLM_API_KEY（可复制 .env.example 配置）")

    def validate_search(self) -> None:
        """Validate only the credentials needed by the built-in Tavily client."""
        if not self.tavily_api_key:
            raise RuntimeError("缺少环境变量：TAVILY_API_KEY（可复制 .env.example 配置）")

    def validate(self) -> None:
        """Validate both built-in integrations for backwards compatibility."""
        missing = []
        if not self.llm_api_key:
            missing.append("LLM_API_KEY")
        if not self.tavily_api_key:
            missing.append("TAVILY_API_KEY")
        if missing:
            raise RuntimeError(f"缺少环境变量：{', '.join(missing)}（可复制 .env.example 配置）")
