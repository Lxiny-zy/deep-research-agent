"""全局配置：从环境变量读取，集中管理研究行为参数。"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _int_env_opt(name: str) -> int | None:
    """可选整型环境变量：未设置返回 None（语义：不限 / 采用内置默认）。"""
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer or unset, got {raw!r}") from exc


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be one of 1/0, true/false, yes/no, or on/off; got {raw!r}")


def _csv_env(name: str) -> tuple[str, ...]:
    return tuple(item.strip().lower() for item in os.getenv(name, "").split(",") if item.strip())


def _str_env(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip()


# 部分中转/网关前置 Cloudflare 等 WAF，会按 User-Agent 拦截 openai SDK 默认的
# "OpenAI/Python ..."（Bot Fight Mode）。默认伪装成常见浏览器 UA 以放行，可经
# LLM_USER_AGENT 覆盖（若上游无此限制可设任意值，不影响功能）。
_DEFAULT_LLM_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# 已实现的检索后端。``tavily`` / ``brave`` 是通用网页索引，``openalex`` / ``arxiv``
# 是学术源。分开列在这里而不是散落在 execution 里，是为了让非法配置在
# Settings 构造时就失败，而不是等到某次研究真的去检索时才报错。
_KNOWN_SEARCH_BACKENDS = frozenset({"tavily", "brave", "openalex", "arxiv"})


@dataclass
class Settings:
    # --- deployment posture ---
    app_env: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    allow_private_provider_urls: bool = field(
        default_factory=lambda: _bool_env("ALLOW_PRIVATE_PROVIDER_URLS", False)
    )
    provider_host_allowlist: tuple[str, ...] = field(
        default_factory=lambda: _csv_env("PROVIDER_HOST_ALLOWLIST")
    )

    # --- LLM（任意 OpenAI 兼容端点）---
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_base_url: str | None = field(default_factory=lambda: os.getenv("LLM_BASE_URL") or None)
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "gpt-4o-mini"))
    llm_user_agent: str = field(
        default_factory=lambda: os.getenv("LLM_USER_AGENT") or _DEFAULT_LLM_UA
    )

    # --- 检索 ---
    tavily_api_key: str = field(default_factory=lambda: os.getenv("TAVILY_API_KEY", ""))
    brave_api_key: str = field(default_factory=lambda: os.getenv("BRAVE_API_KEY", ""))
    # OpenAlex / Crossref 的礼貌池联系邮箱。不是密钥，缺失也能用（走匿名池、
    # 配额更紧），因此不进 validate_search 的必填校验。
    openalex_mailto: str = field(default_factory=lambda: os.getenv("OPENALEX_MAILTO", ""))
    # 启用的检索后端（逗号分隔，默认仅 tavily）。多后端时并发查询后归一化去重，
    # 目的是提高「独立发布方」的召回上限——交叉印证门禁的判定质量受限于此。
    # openalex / arxiv 是学术源：它们额外提供 DOI、机构与撤稿标记，
    # 通用网页检索拿不到这些字段，而科学场景的证据链依赖它们。
    search_backends: tuple[str, ...] = field(
        default_factory=lambda: _csv_env("DR_SEARCH_BACKENDS") or ("tavily",)
    )

    # --- API 认证（支持 Authorization: Bearer 与 X-API-Key；不接受 URL 查询参数）---
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
    # arXiv sources can be expanded from their LaTeX e-print before evidence extraction.
    # Failure falls back to the metadata abstract; this flag controls only the optional fetch.
    fulltext_enabled: bool = field(default_factory=lambda: _bool_env("FULLTEXT_ENABLED", True))
    fulltext_max_chars: int = field(default_factory=lambda: _int_env("FULLTEXT_MAX_CHARS", 12_000))
    # 开启后，仅允许至少两个独立发布方交叉印证且无冲突的论断进入反思与最终报告。
    require_corroboration: bool = field(
        default_factory=lambda: _bool_env("REQUIRE_CORROBORATION", False)
    )

    # 单次研究累计 token 预算上限（防反思/补洞无限烧）；None＝不限。引擎以 Tracer 累计为准，
    # 耗尽则跳过后续研究/反思但仍综合，产出尽力而为的部分报告而非报错。
    max_tokens: int | None = field(default_factory=lambda: _int_env_opt("MAX_TOKENS"))
    # 自组合（auto 流程）生成的流程执行失败/零产出时，Coordinator 重规划的最大次数。
    max_replans: int = field(default_factory=lambda: _int_env("MAX_REPLANS", 1))

    # --- 意图识别 ---
    # 关闭后 IntentRouter 直接放行（不判定、不路由、不拦截），用于排查误伤或做 A/B 对照。
    intent_enabled: bool = field(default_factory=lambda: _bool_env("INTENT_ENABLED", True))
    # 是否允许级联升级到 L3（LLM 兜底）。关闭则只跑规则 + 本地模型，零 token 零网络。
    intent_llm_fallback: bool = field(
        default_factory=lambda: _bool_env("INTENT_LLM_FALLBACK", True)
    )
    # 来源侧意图审查：对通过 SourcePolicy 的来源再做一次意图判定，命中则追加隔离。
    # 只收紧不放宽——规则已 deny 的来源不会因意图判定为 informational 而被放行。
    intent_source_screening: bool = field(
        default_factory=lambda: _bool_env("INTENT_SOURCE_SCREENING", True)
    )

    # --- 网络 ---
    request_timeout: float = field(default_factory=lambda: _float_env("REQUEST_TIMEOUT", 60.0))
    # Wall-clock deadline for one complete research run.  This is separate
    # from per-request timeouts because a workflow can make many provider calls.
    max_run_seconds: int = field(default_factory=lambda: _int_env("MAX_RUN_SECONDS", 3600))
    # Process-local admission limits for background research executions.  The
    # queue is intentionally bounded so overload is observable instead of
    # turning into an unbounded collection of asyncio tasks.
    max_active_runs: int = field(default_factory=lambda: _int_env("MAX_ACTIVE_RUNS", 8))
    max_queued_runs: int = field(default_factory=lambda: _int_env("MAX_QUEUED_RUNS", 32))

    # --- 执行拓扑 ---
    # ``inline``：研究任务在 API 进程内以 asyncio task 执行（默认，桌面版/单文件部署依赖它）。
    # ``worker``：API 只入队，由独立的 ``python -m deep_research.worker`` 进程抢占式领取。
    # 两种模式共用同一套租约 fencing 与 checkpoint 恢复语义，切换不改变任务本身的行为。
    execution_mode: str = field(default_factory=lambda: _str_env("DR_EXECUTION_MODE", "inline"))
    # worker 空闲时的领取轮询间隔；有任务时连续领取，不受此值限制。
    worker_poll_seconds: float = field(
        default_factory=lambda: _float_env("DR_WORKER_POLL_SECONDS", 1.0)
    )
    # 同一个 run 被领取并异常失败的最大次数。超过后置 error（原因 ``poison_run``），
    # 避免一个必然崩溃的任务在 worker 之间无限循环传递。
    max_claim_attempts: int = field(default_factory=lambda: _int_env("DR_MAX_CLAIM_ATTEMPTS", 3))

    # --- planner/artifact execution ---
    # Planner-driven is the default so every entry point uses the durable
    # execution-plan, artifact manifest, and global orchestration contract.
    # ``legacy`` remains available as an explicit compatibility override for
    # older deployments/checkpoints; ``hybrid`` supports staged migrations.
    orchestration_mode: str = field(
        default_factory=lambda: _str_env("DR_ORCHESTRATION_MODE", "planner-driven")
    )
    artifact_root: str = field(
        default_factory=lambda: _str_env("DR_ARTIFACT_ROOT", "./artifacts")
    )
    artifact_max_bytes: int = field(
        default_factory=lambda: _int_env("DR_ARTIFACT_MAX_BYTES", 100 * 1024 * 1024)
    )
    runner_enabled: bool = field(
        default_factory=lambda: _bool_env("DR_RUNNER_ENABLED", True)
    )
    runner_default_timeout: float = field(
        default_factory=lambda: _float_env("DR_RUNNER_DEFAULT_TIMEOUT", 300.0)
    )
    runner_max_output_bytes: int = field(
        default_factory=lambda: _int_env("DR_RUNNER_MAX_OUTPUT_BYTES", 256_000)
    )
    runner_max_processes: int = field(
        default_factory=lambda: _int_env("DR_RUNNER_MAX_PROCESSES", 2)
    )
    runner_allowed_operations: tuple[str, ...] = field(
        default_factory=lambda: _csv_env("DR_RUNNER_ALLOWED_OPERATIONS")
        or ("archive.unpack", "pdf.convert", "latex.compile")
    )

    def __post_init__(self) -> None:
        """范围校验：非法配置尽早失败（含 per-run 覆盖经 dataclasses.replace 时）。"""
        self.app_env = self.app_env.strip().lower()
        if self.app_env not in {"development", "test", "production"}:
            raise ValueError("app_env 必须是 development、test 或 production")
        if self.max_sub_questions < 1:
            raise ValueError("max_sub_questions 必须 >= 1")
        if self.max_rounds < 0:
            raise ValueError("max_rounds 必须 >= 0")
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency 必须 >= 1")
        if self.results_per_search < 1:
            raise ValueError("results_per_search 必须 >= 1")
        if not isinstance(self.fulltext_enabled, bool):
            raise ValueError("fulltext_enabled must be a boolean")
        if self.fulltext_max_chars < 1:
            raise ValueError("fulltext_max_chars 必须 >= 1")
        if not isinstance(self.require_corroboration, bool):
            raise ValueError("require_corroboration must be a boolean")
        if self.max_tokens is not None and self.max_tokens < 1:
            raise ValueError("max_tokens 必须 >= 1 或为 None（不限）")
        if self.max_replans < 0:
            raise ValueError("max_replans 必须 >= 0")
        for name in ("intent_enabled", "intent_llm_fallback", "intent_source_screening"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")
        if not math.isfinite(self.request_timeout) or self.request_timeout <= 0:
            raise ValueError("request_timeout 必须 > 0")
        if self.max_run_seconds < 1:
            raise ValueError("max_run_seconds 必须 >= 1")
        if self.max_active_runs < 1:
            raise ValueError("max_active_runs must be >= 1")
        if self.max_queued_runs < 0:
            raise ValueError("max_queued_runs must be >= 0")
        self.execution_mode = self.execution_mode.strip().lower()
        if self.execution_mode not in {"inline", "worker"}:
            raise ValueError("execution_mode 必须是 inline 或 worker")
        if not math.isfinite(self.worker_poll_seconds) or self.worker_poll_seconds <= 0:
            raise ValueError("worker_poll_seconds 必须 > 0")
        if self.max_claim_attempts < 1:
            raise ValueError("max_claim_attempts must be >= 1")
        self.orchestration_mode = self.orchestration_mode.strip().lower()
        if self.orchestration_mode not in {"legacy", "planner-driven", "hybrid"}:
            raise ValueError("orchestration_mode must be legacy, planner-driven, or hybrid")
        if not self.artifact_root.strip():
            raise ValueError("artifact_root must not be empty")
        if self.artifact_max_bytes < 0:
            raise ValueError("artifact_max_bytes must be >= 0")
        if not isinstance(self.runner_enabled, bool):
            raise ValueError("runner_enabled must be a boolean")
        if not math.isfinite(self.runner_default_timeout) or self.runner_default_timeout <= 0:
            raise ValueError("runner_default_timeout must be > 0")
        if self.runner_max_output_bytes < 1024:
            raise ValueError("runner_max_output_bytes must be >= 1024")
        if self.runner_max_processes < 1:
            raise ValueError("runner_max_processes must be >= 1")
        self.runner_allowed_operations = tuple(
            dict.fromkeys(item.strip() for item in self.runner_allowed_operations if item.strip())
        )
        if not self.runner_allowed_operations:
            raise ValueError("runner_allowed_operations must not be empty")
        self.search_backends = tuple(dict.fromkeys(self.search_backends))
        unknown = set(self.search_backends) - _KNOWN_SEARCH_BACKENDS
        if unknown:
            raise ValueError(
                f"未知检索后端：{', '.join(sorted(unknown))}"
                f"（可选 {', '.join(sorted(_KNOWN_SEARCH_BACKENDS))}）"
            )
        if not self.search_backends:
            raise ValueError("search_backends 至少需要一个后端")

    def validate_deployment(self) -> None:
        """Fail fast when production is configured with development defaults."""
        if self.app_env != "production":
            return
        if os.getenv("DR_DEMO_FAKE_BACKENDS", "").strip().casefold() in {"1", "true", "yes", "on"}:
            raise RuntimeError("DR_DEMO_FAKE_BACKENDS must be disabled in production")
        missing: list[str] = []
        if not self.api_key.strip():
            missing.append("API_KEY")
        if not self.database_url.startswith("postgresql+"):
            missing.append("PostgreSQL DATABASE_URL")
        from .security import SecretCipher

        if not SecretCipher.from_env().enabled:
            missing.append("CATALOG_ENCRYPTION_KEY")
        if missing:
            raise RuntimeError("生产配置缺失：" + ", ".join(missing))

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
