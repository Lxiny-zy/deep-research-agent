"""FastAPI + SSE：实时观看多 Agent 协作 + 持久化历史与回放。

端点：
  GET  /                       前端入口（构建后的 SPA；开发期前端走 Vite dev server）
  GET  /healthz                健康检查（容器探针）
  POST /api/runs               创建研究（后台执行），返回 run_id
  GET  /api/runs               历史列表
  GET  /api/runs/{id}          单次详情（计划 + 结果 + 报告）
  GET  /api/runs/{id}/events   事件回放（一次性，支持 after_seq 增量）
  GET  /api/runs/{id}/stream   SSE：进行中实时推送 / 已结束回放 DB
  GET  /api/research?q=        创建持久化运行并转发 SSE（向后兼容）

启动：uvicorn deep_research.api:app
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AliasChoices, BaseModel, Field, ValidationError, field_validator

from . import runtime_config
from .agents.intent_router import (
    INTENT_POLICY_KEY,
    INTENT_ROUTE_KEY,
    INTENT_SCRATCH_KEY,
    INTENT_SUB_QUESTION_KEY,
)
from .catalog.repository import CatalogRepository
from .config import Settings
from .execution import (
    ExecutionContext,
    RunExecutor,
    build_demo_backends,
    demo_fake_backends_enabled,
    validate_runtime_provider_url,
)
from .execution import (
    settings_for_resume as _settings_for_resume,
)
from .http.admission import (
    RunAdmission,
    RunAdmissionLease,
    RunAdmissionLimit,  # noqa: F401  经 api 命名空间再导出，保持既有调用方与测试不变
    _acquire_run_slot,
    _run_admission,
)
from .http.sse import (
    _SSE_EVENT_BATCH_SIZE,  # noqa: F401  经 api 命名空间再导出（既有测试依赖）
    _stream_run_sse,
)
from .intent import readiness as readiness_module
from .intent.cascade import IntentCascade
from .intent.readiness import MAX_CLARIFY_ROUNDS
from .intent.routing import RoutingPlan, preroute_workflow
from .intent.types import ConversationTurn, IntentDecision, IntentSlots
from .llm import LLM
from .metrics import metrics
from .models import RunManifest
from .observability import Event, EventHub, Tracer
from .orchestration import WorkflowRun
from .orchestrator import (
    RUN_SETTINGS_CHECKPOINT_KEY,
    DeepResearchAgent,
    create_initial_execution,
    snapshot_catalog_for_execution,
)
from .persistence.db import make_engine, make_sessionmaker, prepare_sqlite_schema
from .persistence.repository import (
    RUN_ACTIVE_STATUSES,
    IdempotencyConflictError,
    ResearchRepository,
    RunDetail,
    RunSummary,
    TagCount,
)
from .persistence.sql_repository import SqlRepository
from .planner_runtime import coerce_execution_plan
from .report import (
    ChartDataError,
    CsvTableNotFoundError,
    CsvTableSelectionError,
    PdfExportUnavailable,
    ReportDocument,
    XlsxDependencyError,
    XlsxTableNotFoundError,
    XlsxTableSelectionError,
    assemble_document,
    render_csv,
    render_markdown,
    render_pdf,
    render_xlsx,
)
from .reproducibility import RUN_MANIFEST_CHECKPOINT_KEY, quality_metrics
from .security import ProviderURLPolicyError, validate_provider_url_resolved
from .tools.base import SearchTool

logger = logging.getLogger(__name__)

_LEASE_RENEW_INTERVAL_SECONDS = 60.0
_CANCEL_POLL_SECONDS = 0.5
_RECOVERY_INTERVAL_SECONDS = 30.0
_RECOVERY_PAGE_SIZE = 1000
_RUN_DETAIL_EVENT_LIMIT = 2000
# 装配报告文档时取的事件上限。比详情用的上限更宽：source_policy 拦截数是安全
# 指标，被截断会让它偏小，而偏小的安全数字比明确缺失更糟。
_DOCUMENT_EVENT_LIMIT = 20000
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}")


# 优先用构建后的 SPA（frontend/dist/index.html）；否则回退到内置静态单页 Demo（frontend/index.html）
def _bundle_root() -> Path:
    """Return the project root in source mode, or PyInstaller data root when frozen."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[1]


def _frontend_paths() -> tuple[Path, Path]:
    """Locate SPA assets in source, frozen, or installed-wheel layouts."""
    bundle_frontend = _bundle_root() / "frontend"
    source_dist = bundle_frontend / "dist"
    if (source_dist / "index.html").is_file():
        return source_dist / "index.html", bundle_frontend / "index.html"

    candidates = [Path(sys.prefix) / "share" / "deep-research-agent" / "frontend"]
    try:
        installed = distribution("deep-research-agent")
    except PackageNotFoundError:
        installed = None
    if installed is not None:
        for entry in installed.files or ():
            if entry.as_posix().endswith("share/deep-research-agent/frontend/index.html"):
                candidates.append(Path(str(installed.locate_file(entry))).parent)
                break
    for candidate in candidates:
        index = candidate / "index.html"
        if index.is_file():
            return index, index
    return source_dist / "index.html", bundle_frontend / "index.html"


_FRONTEND_DIST, _FRONTEND_INDEX = _frontend_paths()


class ResearchParams(BaseModel):
    """per-run 研究参数覆盖（缺省字段沿用服务端 Settings）。"""

    max_sub_questions: int | None = Field(default=None, ge=1, le=12)
    max_rounds: int | None = Field(default=None, ge=0, le=5)
    max_concurrency: int | None = Field(default=None, ge=1, le=16)
    results_per_search: int | None = Field(default=None, ge=1, le=15)
    require_corroboration: bool | None = None
    max_tokens: int | None = Field(default=None, ge=1, le=10_000_000)  # 本次研究 token 预算上限
    max_run_seconds: int | None = Field(default=None, ge=1, le=86_400)


class CreateRunRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)  # 限长：query 全文进 prompt，防成本放大
    params: ResearchParams | None = None
    # Optional trusted planner document. ``plan`` remains an input alias for
    # clients using the extracted Vela terminology.
    execution_plan: dict[str, Any] | str | None = Field(
        default=None,
        validation_alias=AliasChoices("execution_plan", "plan"),
    )
    workflow: str | None = Field(default=None, max_length=64)  # 任务流程选择；None＝默认 deep
    # 多轮上下文由客户端携带，服务端不存会话：run 之间无状态是这个系统的既有性质
    # （崩溃恢复、租约 fencing、回放都建立在「一个 run 自包含」之上）；加一张会话表
    # 会把这些不变量全部拖进多轮语义里。限长 6 轮：消解只依赖最近的话题焦点。
    history: list[ConversationTurn] = Field(default_factory=list, max_length=6)
    # 客户端声明已走过 /api/intent/assess 的澄清循环（含用户显式点「直接研究」）。
    # 置位后本次创建不再复核澄清——否则用户刚跳过追问就被 422 打回，「直接研究」
    # 成了死胡同。真实性无法验证，但谎报的后果只是少一次追问建议；风险门禁
    # 不看这个字段，拒识照常拦截。
    clarified: bool = False


class CreateRunResponse(BaseModel):
    run_id: str


class CancelRunResponse(BaseModel):
    run_id: str
    status: str


class AssessRequest(BaseModel):
    """澄清循环的一轮输入。服务端不存任何东西——累积的答案由客户端携带，
    因此同样的请求体永远得到同样的判定（与 ``CreateRunRequest.history`` 同源的原则）。
    """

    query: str = Field(min_length=1, max_length=2000)
    # 已经问出来的答案。本质就是槽位值（选了「性能与效率」= aspects），
    # 因此直接复用 IntentSlots，不另造类型。
    answers: IntentSlots = Field(default_factory=IntentSlots)
    # 已经问过几轮。客户端可篡改，但每轮成本极低且有独立限流，
    # 为此引入服务端状态不划算。
    round: int = Field(0, ge=0, le=MAX_CLARIFY_ROUNDS)
    history: list[ConversationTurn] = Field(default_factory=list, max_length=6)
    # 用户显式跳过追问（点了「直接研究」）。服务端仍要把已答槽位合成进最终
    # 问题——跳过的是**后续追问**，不是已经给过的答案；风险门禁也照常跑。
    skip: bool = False


class AssessResponse(BaseModel):
    ready: bool
    # ready 时前端拿它去建 run：由服务端合成，前端不参与拼接。
    resolved_query: str = ""
    question: str = ""
    options: list[str] = Field(default_factory=list)
    gap: str = "none"
    # 安全拦截：前端照常走 create_run，让拒识留下审计痕迹。
    blocked: bool = False
    intent: str = ""
    reason: str = ""


class TagsUpdate(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("tags")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        # 去空白、丢空串、去重、截断到 64 字（与 ORM run_tag.tag 长度一致）
        out: list[str] = []
        for raw in v:
            t = raw.strip()[:64]
            if t and t not in out:
                out.append(t)
        return out


class BatchDeleteRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)


class BatchDeleteResponse(BaseModel):
    deleted: int
    skipped: int  # 进行中、跳过删除的数量


class ConfigView(BaseModel):
    """GET /api/config 响应：密钥脱敏，只透露是否已设置 + 尾部 hint。"""

    llm_model: str
    llm_base_url: str | None
    llm_api_key_set: bool
    llm_api_key_hint: str
    tavily_api_key_set: bool
    tavily_api_key_hint: str
    max_sub_questions: int
    max_rounds: int
    max_concurrency: int
    results_per_search: int
    fulltext_enabled: bool
    fulltext_max_chars: int
    require_corroboration: bool
    request_timeout: float
    max_run_seconds: int


class ConfigUpdate(BaseModel):
    """PUT /api/config 请求：全部可选，仅覆盖显式提供的字段。

    密钥空/省略＝保持不变（避免脱敏表单回写清空）；llm_base_url 显式空串＝清空。
    """

    llm_model: str | None = Field(default=None, min_length=1, max_length=100)
    llm_base_url: str | None = Field(default=None, max_length=500)
    llm_api_key: str | None = Field(default=None, max_length=500)
    tavily_api_key: str | None = Field(default=None, max_length=500)
    max_sub_questions: int | None = Field(default=None, ge=1, le=12)
    max_rounds: int | None = Field(default=None, ge=0, le=5)
    max_concurrency: int | None = Field(default=None, ge=1, le=16)
    results_per_search: int | None = Field(default=None, ge=1, le=15)
    fulltext_enabled: bool | None = None
    fulltext_max_chars: int | None = Field(default=None, ge=1, le=200_000)
    require_corroboration: bool | None = None
    request_timeout: float | None = Field(default=None, gt=0, le=600)
    max_run_seconds: int | None = Field(default=None, ge=1, le=86_400)

    @field_validator(
        "llm_model",
        "max_sub_questions",
        "max_rounds",
        "max_concurrency",
        "results_per_search",
        "fulltext_enabled",
        "fulltext_max_chars",
        "require_corroboration",
        "request_timeout",
        "max_run_seconds",
        mode="before",
    )
    @classmethod
    def reject_null_non_nullable_fields(cls, value: object) -> object:
        # 显式 null 非法：这些字段没有「清空」语义，None 一旦进 overrides 会污染持久化配置
        if value is None:
            raise ValueError("field cannot be null; omit it to keep the current value")
        return value


def _settings_for(base: Settings, params: ResearchParams | None) -> Settings:
    """把 per-run 覆盖合并进基础 Settings（仅覆盖显式提供的字段）。"""
    if params is None:
        return base
    overrides = {k: v for k, v in params.model_dump().items() if v is not None}
    return replace(base, **overrides) if overrides else base


def _run_request_hash(request: CreateRunRequest) -> str:
    payload = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _close_live_hub(app: FastAPI, run_id: str, hub: EventHub | None = None) -> None:
    """Close a run's SSE hub and remove only that exact registration."""
    target = hub if hub is not None else app.state.live.get(run_id)
    if target is None:
        return
    target.close()
    if app.state.live.get(run_id) is target:
        app.state.live.pop(run_id, None)


async def _settle_prestart_cancellation(
    app: FastAPI,
    run_id: str,
    lease_owner: str | None,
    hub: EventHub | None,
) -> None:
    """Settle and release a task cancelled before normal cleanup completed.

    Cancelling a freshly-created asyncio task can prevent ``_execute`` from
    entering its ``try/finally`` block at all.  The done callback invokes this
    durable fallback; normally ``_execute`` has already released the lease and
    changed the status to ``cancelled``, making those operations no-ops.
    """
    repo: ResearchRepository = app.state.repo
    try:
        try:
            status = await repo.get_run_status(run_id)
            attempt = await repo.get_run_attempt(run_id) or 1
        except Exception:
            logger.exception("run %s failed to inspect cancelled task state", run_id)
            return
        if status != "cancelling":
            return
        event = Event(
            attempt=attempt,
            stage="ORCHESTRATOR",
            type="cancelled",
            message="运行已取消",
            data={"status": "cancelled"},
        )
        published = event
        try:
            stored = await repo.append_events(run_id, [event], lease_owner=lease_owner)
            if stored:
                published = stored[0]
        except Exception:
            logger.exception("run %s failed to persist pre-start cancellation event", run_id)
        try:
            await repo.set_status(run_id, "cancelled", lease_owner=lease_owner)
        except Exception:
            logger.exception("run %s failed to persist pre-start cancellation status", run_id)
            return
        if hub is not None:
            hub.publish(published)
    except Exception:
        logger.exception("run %s pre-start cancellation fallback failed", run_id)
    finally:
        # A shutdown cancellation may leave the durable status active rather
        # than ``cancelling``. It is intentionally recoverable, but the dead
        # worker must still give up its lease immediately instead of waiting
        # for the TTL to expire.
        if lease_owner is not None:
            try:
                await repo.release_lease(run_id, lease_owner)
            except Exception:
                logger.exception("run %s failed to release cancelled task lease", run_id)
        if hub is not None:
            _close_live_hub(app, run_id, hub)


def _track_run_task(
    app: FastAPI,
    run_id: str,
    task: asyncio.Task[None],
    *,
    lease_owner: str | None = None,
    admission: RunAdmissionLease | None = None,
) -> None:
    """Keep strong references and a direct run-to-task cancellation index."""
    tasks: set[asyncio.Task[None]] = app.state.tasks
    stored_run_tasks: object = getattr(app.state, "run_tasks", None)
    if stored_run_tasks is None:
        run_tasks: dict[str, asyncio.Task[None]] = {}
        app.state.run_tasks = run_tasks
    else:
        run_tasks = cast(dict[str, asyncio.Task[None]], stored_run_tasks)
    tasks.add(task)
    run_tasks[run_id] = task
    live: dict[str, EventHub] = getattr(app.state, "live", {})
    tracked_hub = live.get(run_id)

    def discard(done: asyncio.Task[None]) -> None:
        tasks.discard(done)
        if run_tasks.get(run_id) is done:
            run_tasks.pop(run_id, None)
        if admission is not None:
            admission.release()
        requested_cancellations: set[str] = getattr(app.state, "cancellation_requested", set())
        cancellation_requested = run_id in requested_cancellations
        if cancellation_requested:
            requested_cancellations.discard(run_id)
        if done.cancelled():
            cleanup_coro = _settle_prestart_cancellation(app, run_id, lease_owner, tracked_hub)
            try:
                cleanup = done.get_loop().create_task(cleanup_coro)
            except BaseException:
                cleanup_coro.close()
                logger.exception("run %s failed to schedule pre-start cancellation cleanup", run_id)
            else:
                cleanup_tasks: set[asyncio.Task[None]] = getattr(app.state, "cleanup_tasks", set())
                if not hasattr(app.state, "cleanup_tasks"):
                    app.state.cleanup_tasks = cleanup_tasks
                tasks.add(cleanup)
                cleanup_tasks.add(cleanup)

                def discard_cleanup(completed: asyncio.Task[None]) -> None:
                    tasks.discard(completed)
                    cleanup_tasks.discard(completed)

                cleanup.add_done_callback(discard_cleanup)

    task.add_done_callback(discard)


def _mask_secret(secret: str) -> str:
    """密钥脱敏：只露尾 4 位（不足 4 位整体视为短密钥，仍只露尾部）。"""
    if not secret:
        return ""
    tail = secret[-4:] if len(secret) >= 4 else secret
    return f"…{tail}"


def _sanitized_overrides(overrides: dict) -> dict:
    """丢弃 overrides 中非法的 None 值（llm_base_url 除外：None＝清空回默认端点）。

    兜底旧版本可能写坏的持久化文件：None 覆盖到 Settings 会让 ConfigView 构造失败。
    """
    return {k: v for k, v in overrides.items() if v is not None or k == "llm_base_url"}


def _config_lock(request: Request) -> asyncio.Lock:
    """Return the per-process lock used for runtime configuration updates."""
    lock = getattr(request.app.state, "config_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        request.app.state.config_lock = lock
    return cast(asyncio.Lock, lock)


def _config_view(s: Settings) -> ConfigView:
    return ConfigView(
        llm_model=s.llm_model,
        llm_base_url=s.llm_base_url,
        llm_api_key_set=bool(s.llm_api_key),
        llm_api_key_hint=_mask_secret(s.llm_api_key),
        tavily_api_key_set=bool(s.tavily_api_key),
        tavily_api_key_hint=_mask_secret(s.tavily_api_key),
        max_sub_questions=s.max_sub_questions,
        max_rounds=s.max_rounds,
        max_concurrency=s.max_concurrency,
        results_per_search=s.results_per_search,
        fulltext_enabled=s.fulltext_enabled,
        fulltext_max_chars=s.fulltext_max_chars,
        require_corroboration=s.require_corroboration,
        request_timeout=s.request_timeout,
        max_run_seconds=s.max_run_seconds,
    )


async def _validate_runtime_provider_url(settings: Settings) -> None:
    await validate_runtime_provider_url(settings)


async def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> None:
    """API 认证：设置了 API_KEY 环境变量即启用，所有 /api 端点必须携带。

    支持标准 Authorization: Bearer 或兼容性的 X-API-Key 请求头。
    查询参数不会被接受，避免密钥进入 URL、代理日志和浏览器历史。
    未配置 API_KEY 时跳过（本地开发零摩擦），但生产部署应当配置。
    """
    expected: str = request.app.state.settings.api_key
    if not expected:
        return
    bearer = ""
    if authorization:
        scheme, _, credential = authorization.partition(" ")
        if scheme.casefold() == "bearer":
            bearer = credential.strip()
    candidates = [candidate for candidate in (bearer, x_api_key or "") if candidate]
    if not any(
        secrets.compare_digest(candidate.encode(), expected.encode()) for candidate in candidates
    ):
        raise HTTPException(status_code=401, detail="invalid or missing API key")


class _RateLimiter:
    """每 IP 滑动窗口限流（进程内）。只挡「触发 LLM 调用」的昂贵端点。"""

    # key 总量上限：超过即全量清理过期条目，防止海量来源 IP 让 _hits 无界增长
    _MAX_KEYS = 10_000

    def __init__(self, max_calls: int, window_seconds: float) -> None:
        self.max_calls = max_calls
        self.window = window_seconds
        self._hits: dict[str, list[float]] = {}

    def check(self, key: str) -> bool:
        now = time.monotonic()
        hits = [t for t in self._hits.get(key, []) if now - t < self.window]
        if len(hits) >= self.max_calls:
            # Refresh insertion order so capacity eviction retains recently
            # active clients.
            self._hits.pop(key, None)
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits.pop(key, None)
        self._hits[key] = hits
        if len(self._hits) > self._MAX_KEYS:
            self._prune(now)
        return True

    def _prune(self, now: float) -> None:
        """删除窗口内已无命中的 key（含清理后变空的条目），回收内存。"""
        for stale_key in list(self._hits):
            live = [t for t in self._hits[stale_key] if now - t < self.window]
            if live:
                self._hits[stale_key] = live
            else:
                del self._hits[stale_key]
        # Every key can remain live for the full window. Enforce the advertised
        # cap even when expiry cannot reclaim entries; check() refreshes dict
        # order on access, so the oldest entry is the least recently used one.
        while len(self._hits) > self._MAX_KEYS:
            del self._hits[next(iter(self._hits))]


_run_limiter = _RateLimiter(max_calls=10, window_seconds=60.0)
# 澄清判定独立限流：一次澄清循环最多 3 轮，与建 run 共用配额的话，
# 用户问三个问题就能把 10 次/分钟的建 run 额度耗掉大半。这条路径只跑
# 规则 + 本地模型（零 token、毫秒级），配额可以宽得多。
_assess_limiter = _RateLimiter(max_calls=30, window_seconds=60.0)


def _rate_limit_key(request: Request) -> str:
    """限流 key：默认直连对端 IP；信任代理时优先取其覆盖写入的 X-Real-IP。

    开关直读环境变量（部署拓扑属性，不进 config.Settings 的研究行为配置）。
    默认关闭：直连部署下该头可被客户端伪造，信任它等于放开限流。
    """
    if os.environ.get("APP_TRUST_PROXY", "").strip().lower() in {"1", "true", "yes"}:
        real_ip = request.headers.get("x-real-ip", "").strip()
        if real_ip:
            return real_ip
        forwarded = request.headers.get("x-forwarded-for", "")
        first_hop = forwarded.split(",")[0].strip() if forwarded else ""
        if first_hop:
            return first_hop
    return request.client.host if request.client else "unknown"


def _request_id(request: Request) -> str:
    """Use a safe upstream correlation id or create one for this request."""
    candidate = request.headers.get("x-request-id", "").strip()
    if _REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return uuid4().hex


def _route_label(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return "__unmatched__"


def _check_rate_limit(request: Request) -> None:
    if not _run_limiter.check(_rate_limit_key(request)):
        raise HTTPException(status_code=429, detail="too many requests, slow down")


def _check_assess_rate_limit(request: Request) -> None:
    if not _assess_limiter.check(_rate_limit_key(request)):
        raise HTTPException(status_code=429, detail="too many requests, slow down")


async def _recover_orphaned_runs(app: FastAPI, settings: Settings) -> None:
    """Start recoverable runs whose lease is absent or has expired.

    The lease check remains the final cross-instance arbiter; the repeated scan
    only closes the gap where a crashed worker's TTL expires after startup.
    """
    # Finish cancellation requests left behind by a crashed owner.  An active
    # owner keeps its lease and will observe the state in its monitor loop.
    cancelling_summaries: list[RunSummary] = []
    offset = 0
    while True:
        page = await app.state.repo.list_runs(
            status="cancelling", limit=_RECOVERY_PAGE_SIZE, offset=offset
        )
        cancelling_summaries.extend(page)
        if len(page) < _RECOVERY_PAGE_SIZE:
            break
        offset += len(page)
    for summary in cancelling_summaries:
        owner: str | None = None
        settled = False
        live: dict[str, EventHub] = getattr(app.state, "live", {})
        stale_hub = live.get(summary.id)
        try:
            detail = await app.state.repo.get_run(summary.id)
            if detail is None:
                continue
            if detail.orchestration is not None:
                candidate = uuid4().hex
                if not await app.state.repo.acquire_lease(summary.id, candidate):
                    continue
                owner = candidate
            event = Event(
                stage="ORCHESTRATOR",
                type="cancelled",
                message="运行已取消",
                data={"status": "cancelled", "recovered": True},
            )
            await app.state.repo.append_events(summary.id, [event], lease_owner=owner)
            await app.state.repo.set_status(summary.id, "cancelled", lease_owner=owner)
            settled = True
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("failed to recover cancelling run %s", summary.id)
        finally:
            if owner is not None:
                try:
                    await app.state.repo.release_lease(summary.id, owner)
                except Exception:
                    logger.exception("failed to release cancellation lease for %s", summary.id)
            if settled and stale_hub is not None:
                _close_live_hub(app, summary.id, stale_hub)

    if settings.execution_mode == "worker":
        # 执行归 worker：孤儿任务由领取循环接管（租约过期即可被领走），API 若在这里
        # 抢着执行就等于两种拓扑同时生效。取消结算仍留在上面——那不需要执行能力。
        return

    orphaned: list[RunSummary] = []
    for status in ("pending", "running"):
        offset = 0
        while True:
            page = await app.state.repo.list_runs(
                status=status, limit=_RECOVERY_PAGE_SIZE, offset=offset
            )
            orphaned.extend(page)
            if len(page) < _RECOVERY_PAGE_SIZE:
                break
            offset += len(page)
    for summary in orphaned:
        lease_owner: str | None = None
        admission: RunAdmissionLease | None = None
        handed_off = False
        live_created = False
        try:
            if summary.id in app.state.live:
                continue
            detail = await app.state.repo.get_run(summary.id)
            execution = detail.orchestration if detail is not None else None
            if detail is None or execution is None:
                # A legacy worker can create the root row before its workflow
                # checkpoint. Without a durable lease there is no safe way to
                # distinguish that startup window from an orphan, so leave it
                # pending for the next recovery scan instead of overwriting a
                # live worker's state.
                continue
            lease_owner = uuid4().hex
            if not await app.state.repo.acquire_lease(summary.id, lease_owner):
                lease_owner = None
                continue

            # The initial read only identifies a candidate. Always reload after
            # fencing so a just-expired worker cannot be resumed from a stale
            # checkpoint (or restart a run that completed in the meantime).
            detail = await app.state.repo.get_run(summary.id)
            execution = detail.orchestration if detail is not None else None
            if detail is None or execution is None:
                continue
            if detail.status not in {"pending", "running"}:
                continue
            if not execution.checkpoint:
                try:
                    await app.state.repo.set_status(summary.id, "error", lease_owner=lease_owner)
                finally:
                    await app.state.repo.release_lease(summary.id, lease_owner)
                    lease_owner = None
                continue

            admission = _run_admission(app).try_acquire()
            if admission is None:
                # Leave the fenced run recoverable; the next periodic scan
                # will pick it up after an active slot is released.
                continue

            execution.attempt = await app.state.repo.prepare_resume(
                summary.id, lease_owner=lease_owner
            )
            app.state.live[summary.id] = EventHub()
            live_created = True
            resume_settings = _settings_for_resume(settings, execution)
            execution_coro = _execute_with_admission(
                admission,
                app,
                summary.id,
                detail.query,
                resume_settings,
                execution.workflow_name,
                execution,
                lease_owner,
            )
            try:
                task = asyncio.create_task(execution_coro)
            except BaseException:
                execution_coro.close()
                raise
            handed_off = True
            _track_run_task(app, summary.id, task, lease_owner=lease_owner, admission=admission)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("failed to recover orphaned run %s", summary.id)
        finally:
            if not handed_off and lease_owner is not None:
                if admission is not None:
                    admission.release()
                if live_created:
                    _close_live_hub(app, summary.id)
                try:
                    await app.state.repo.release_lease(summary.id, lease_owner)
                except Exception:
                    logger.exception("failed to release recovery lease for %s", summary.id)


async def _recovery_loop(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(_RECOVERY_INTERVAL_SECONDS)
        try:
            await _recover_orphaned_runs(app, app.state.settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("周期恢复未完成任务失败")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    # 叠加前端持久化的全局配置；内容损坏/非法回退，权限与 I/O 错误 fail-fast。
    overrides = _sanitized_overrides(runtime_config.load_overrides())
    if overrides:
        try:
            settings = runtime_config.apply_overrides(settings, overrides)
        except Exception:
            logger.exception("应用持久化配置失败，回退到环境变量配置")
    settings.validate_deployment()
    await _validate_runtime_provider_url(settings)
    engine = make_engine(settings.database_url)
    recovery_task: asyncio.Task[None] | None = None
    try:
        # SQLite 本地启动也准备 schema，避免旧 create_all 库升级后缺列；
        # PostgreSQL 由 entrypoint 迁移。
        if settings.database_url.startswith("sqlite"):
            await prepare_sqlite_schema(engine, settings.database_url)
        app.state.settings = settings
        app.state.engine = engine
        app.state.repo = SqlRepository(make_sessionmaker(engine))
        app.state.catalog = CatalogRepository(make_sessionmaker(engine))  # 角色广场 catalog 仓储
        encrypt_legacy = getattr(app.state.catalog, "encrypt_legacy_secrets", None)
        if encrypt_legacy is not None:
            migrated = await encrypt_legacy()
            if migrated:
                logger.info("encrypted %s legacy catalog credentials", migrated)
        app.state.live = {}  # run_id -> EventHub：进行中 run 的实时事件中枢（向多端 SSE 扇出）
        app.state.tasks = set()  # 持有后台任务引用，避免被 GC 提前回收
        app.state.cleanup_tasks = set()  # 任务 done callback 派生的异步资源收尾
        app.state.run_tasks = {}  # run_id -> local execution task，用于精确取消
        app.state.cancellation_requested = set()  # 本实例主动取消、供 pre-start 收尾识别
        app.state.run_admission = RunAdmission(settings.max_active_runs, settings.max_queued_runs)
        app.state.config_lock = asyncio.Lock()
        # 自动恢复上次进程中断且已有 checkpoint 的任务；无 checkpoint 的孤儿任务置 error。
        try:
            await _recover_orphaned_runs(app, settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("启动恢复未完成任务失败（不阻塞启动）")
        recovery_coro = _recovery_loop(app)
        try:
            recovery_task = asyncio.create_task(recovery_coro)
        except BaseException:
            recovery_coro.close()
            raise
        app.state.tasks.add(recovery_task)
        recovery_task.add_done_callback(app.state.tasks.discard)
        yield
    finally:
        # Stop the producer of background work first. Otherwise a recovery
        # scan can hand off a new execution after the task snapshot below.
        if recovery_task is not None:
            recovery_task.cancel()
            await asyncio.gather(recovery_task, return_exceptions=True)
        # 再取消并回收后台研究任务，最后释放引擎——避免任务在 dispose 后继续发 SQL
        cleanup_tasks: set[asyncio.Task[None]] = getattr(app.state, "cleanup_tasks", set())
        tasks = [
            task
            for task in getattr(app.state, "tasks", set())
            if task is not recovery_task and task not in cleanup_tasks
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Cancelling a task before its coroutine starts schedules durable lease
        # cleanup from its done callback. Let those callbacks run and drain the
        # resulting tasks before disposing the database engine they depend on.
        await asyncio.sleep(0)
        pending_cleanup = [task for task in cleanup_tasks if not task.done()]
        if pending_cleanup:
            await asyncio.gather(*pending_cleanup, return_exceptions=True)
        await engine.dispose()


app = FastAPI(title="Deep Research Agent", lifespan=lifespan)

# 角色广场 catalog 路由（模型档案 / 角色卡片 / 搜索 key），统一套用 API key 鉴权
from .catalog_api import router as catalog_router  # noqa: E402 避免与 app 定义循环

app.include_router(catalog_router, dependencies=[Depends(require_api_key)])


@app.middleware("http")
async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Attach security/correlation headers and record bounded HTTP metrics."""
    request_id = _request_id(request)
    request.state.request_id = request_id
    started = time.perf_counter()
    response = None
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    except Exception:
        logger.exception(
            "http_request_failed request_id=%s method=%s path=%s",
            request_id,
            request.method,
            request.url.path,
        )
        raise
    finally:
        elapsed = time.perf_counter() - started
        labels = {
            "method": request.method,
            "route": _route_label(request),
            "status": str(status),
        }
        metrics.inc("deep_research_http_requests_total", labels)
        metrics.inc("deep_research_http_request_duration_seconds_sum", labels, elapsed)
        metrics.inc("deep_research_http_request_duration_seconds_count", labels)
        logger.info(
            "http_request request_id=%s method=%s path=%s status=%s duration_ms=%.1f",
            request_id,
            request.method,
            request.url.path,
            status,
            elapsed * 1000,
        )
        if response is not None:
            response.headers.setdefault("X-Request-ID", request_id)
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("X-Frame-Options", "DENY")
            response.headers.setdefault("Referrer-Policy", "same-origin")


# 生产同源托管：把 Vite 构建产物的静态资源挂在 /assets/*（须在末尾 catch-all SPA 路由
# 之前注册）。dist 未构建时跳过——开发期前端走 Vite dev server，不经后端静态服务。
_FRONTEND_ASSETS = _FRONTEND_DIST.parent / "assets"
if _FRONTEND_ASSETS.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_ASSETS)), name="assets")


def _worker_mode(app: FastAPI) -> bool:
    """True 表示执行由独立 worker 进程负责，本进程只入队。

    默认 ``inline``：改成 worker 是一次显式的部署决定，回滚只需改回环境变量。
    """
    settings = getattr(app.state, "settings", None)
    return getattr(settings, "execution_mode", "inline") == "worker"


async def _enqueue_run(
    request: Request,
    repo: ResearchRepository,
    req: CreateRunRequest,
    execution: WorkflowRun,
    idempotency_key: str | None,
    response: Response,
) -> CreateRunResponse:
    """在 worker 模式下持久化一个待领取的 run。

    与 inline 路径的唯一区别是不带 ``lease_owner``：租约必须由**实际执行**该 run
    的 worker 持有，API 提前占住只会让 worker 在租约到期前都领不走。
    """
    try:
        run_id, created = await repo.create_run_once(
            req.query,
            request_hash=_run_request_hash(req),
            idempotency_key=idempotency_key,
            execution=execution,
            claimable=True,
        )
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "idempotency_conflict",
                "message": "Idempotency-Key 已用于不同的请求",
            },
        ) from exc
    if not created:
        response.headers["Idempotency-Replayed"] = "true"
    return CreateRunResponse(run_id=run_id)


def _executor(app: FastAPI) -> RunExecutor:
    """本进程的执行器。执行核心住在 ``execution.py``，与 HTTP 传输无关。"""
    executor = getattr(app.state, "executor", None)
    if not isinstance(executor, RunExecutor):
        executor = RunExecutor(
            ExecutionContext(
                repo=app.state.repo,
                catalog=getattr(app.state, "catalog", None),
                live=app.state.live,
            )
        )
        app.state.executor = executor
    else:
        # 配置中心可在运行期换掉仓储/目录（见 update_config），执行器必须跟着更新。
        executor.ctx.repo = app.state.repo
        executor.ctx.catalog = getattr(app.state, "catalog", None)
        executor.ctx.live = app.state.live
    return executor


async def _build_agent(
    app: FastAPI, settings: Settings, **agent_kwargs: object
) -> tuple[DeepResearchAgent, SearchTool | None]:
    return await _executor(app).build_agent(settings, **agent_kwargs)


async def _execute(
    app: FastAPI,
    run_id: str,
    query: str,
    settings: Settings,
    workflow: str | None = None,
    resume_execution: WorkflowRun | None = None,
    lease_owner: str | None = None,
    initial_execution: WorkflowRun | None = None,
    requested_workflow: str | None = None,
    execution_plan: dict[str, Any] | str | None = None,
) -> None:
    await _executor(app).execute(
        run_id,
        query,
        settings,
        workflow=workflow,
        resume_execution=resume_execution,
        lease_owner=lease_owner,
        initial_execution=initial_execution,
        requested_workflow=requested_workflow,
        execution_plan=execution_plan,
    )


async def _execute_with_admission(
    admission: RunAdmissionLease,
    *args: Any,
    **kwargs: Any,
) -> None:
    """Wait for a queued slot while keeping the durable run lease alive.

    A run obtains its database lease before it enters the process-local queue.
    Without this heartbeat, a saturated process could hold a queued run for
    longer than the 120-second lease and another instance would recover it.
    The wait is shielded so a renewal timeout cannot cancel the admission
    waiter; cancellation remains explicit in the ``finally`` block.
    """
    if len(args) < 2:
        raise TypeError("_execute_with_admission requires app and run_id")
    app = cast(FastAPI, args[0])
    run_id = cast(str, args[1])
    lease_owner = kwargs.get("lease_owner")
    if lease_owner is None and len(args) > 6:
        lease_owner = args[6]
    live: dict[str, EventHub] = getattr(app.state, "live", {})
    queued_hub = live.get(run_id)
    wait_coro = admission.wait()
    try:
        wait_task = asyncio.create_task(wait_coro)
    except BaseException:
        wait_coro.close()
        admission.release()
        if isinstance(lease_owner, str):
            try:
                await app.state.repo.release_lease(run_id, lease_owner)
            except Exception:
                logger.exception("run %s queued task setup failed to release lease", run_id)
        if queued_hub is not None:
            _close_live_hub(app, run_id, queued_hub)
        raise
    execution_started = False
    next_renewal = time.monotonic() + _LEASE_RENEW_INTERVAL_SECONDS
    try:
        while not wait_task.done():
            timeout = _LEASE_RENEW_INTERVAL_SECONDS
            if isinstance(lease_owner, str):
                timeout = max(
                    0.0,
                    min(_CANCEL_POLL_SECONDS, next_renewal - time.monotonic()),
                )
            try:
                await asyncio.wait_for(asyncio.shield(wait_task), timeout=timeout)
            except TimeoutError:
                if not isinstance(lease_owner, str):
                    continue
                get_status = getattr(app.state.repo, "get_run_status", None)
                if get_status is not None:
                    try:
                        status = await get_status(run_id)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("run %s queued cancellation poll failed", run_id)
                    else:
                        if status not in {"pending", "running"}:
                            wait_task.cancel()
                            await asyncio.gather(wait_task, return_exceptions=True)
                            return
                if time.monotonic() < next_renewal:
                    continue
                try:
                    renewed = await app.state.repo.renew_lease(run_id, lease_owner)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("run %s queued lease renewal failed", run_id)
                    next_renewal = time.monotonic() + _CANCEL_POLL_SECONDS
                    continue
                if renewed:
                    next_renewal = time.monotonic() + _LEASE_RENEW_INTERVAL_SECONDS
                    continue
                # Another worker owns the run now.  Cancel the local waiter;
                # the recovery loop can safely pick it up after this task exits.
                logger.warning("run %s lost its lease while queued", run_id)
                wait_task.cancel()
                await asyncio.gather(wait_task, return_exceptions=True)
                return
        await wait_task
        if isinstance(lease_owner, str):
            # Admission can become available after the last periodic renewal.
            # Fence once more before any provider calls so an expired lease
            # acquired by another instance cannot result in duplicate work.
            try:
                still_owned = await app.state.repo.renew_lease(run_id, lease_owner)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("run %s final queued lease check failed", run_id)
                still_owned = False
            if not still_owned:
                logger.warning("run %s lost its lease before admission", run_id)
                return
            get_status = getattr(app.state.repo, "get_run_status", None)
            if get_status is not None:
                try:
                    status = await get_status(run_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("run %s final queued status check failed", run_id)
                    return
                if status not in {"pending", "running"}:
                    return
        # Keep compatibility with integrations/tests that still provide the
        # pre-planner ``_execute`` signature.  ``None`` carries no additional
        # contract and should not be forwarded as an unexpected keyword.
        invoke_kwargs = dict(kwargs)
        if invoke_kwargs.get("execution_plan") is None:
            invoke_kwargs.pop("execution_plan", None)
        execution_coro = _execute(*args, **invoke_kwargs)
        execution_started = True
        await execution_coro
    finally:
        if not wait_task.done():
            wait_task.cancel()
            await asyncio.gather(wait_task, return_exceptions=True)
        admission.release()
        if not execution_started:
            await _settle_prestart_cancellation(app, run_id, lease_owner, queued_hub)


@lru_cache(maxsize=8)
def _read_frontend(path_str: str, mtime: float) -> str:
    """带 mtime 失效的入口页缓存：catch-all SPA 路由每个请求都要它，避免反复读盘。"""
    return Path(path_str).read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    for path in (_FRONTEND_DIST, _FRONTEND_INDEX):
        if path.exists():
            return _read_frontend(str(path), path.stat().st_mtime)
    return (
        "<h1>Deep Research Agent</h1>"
        "<p>未找到前端页面（缺少 <code>frontend/index.html</code>）。</p>"
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Backward-compatible liveness probe."""
    return {"status": "ok"}


@app.get("/livez")
async def livez() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(request: Request) -> dict[str, str]:
    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="repository is not initialized")
    try:
        ready = await repo.healthcheck()
    except Exception as exc:
        logger.exception("readiness database check failed")
        raise HTTPException(status_code=503, detail="database is unavailable") from exc
    if not ready:
        raise HTTPException(status_code=503, detail="database is unavailable")
    return {"status": "ready"}


@app.get("/metrics", dependencies=[Depends(require_api_key)])
async def metrics_endpoint() -> PlainTextResponse:
    """Prometheus text exposition for internal service monitoring."""
    return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")


@asynccontextmanager
async def _intent_llm(app: FastAPI, settings: Settings) -> AsyncIterator[Any | None]:
    """为多轮消解临时借一个 LLM，用完必关。

    只有带 history 的请求才会走到这里。自建 client 必须显式 aclose——
    AsyncOpenAI 不关只能靠 GC 兜底，在 HTTP 处理路径上逐请求泄漏 FD 会很快耗尽。

    缺 key 或构造失败时返回 None：多轮消解是**增强**而非必需，退化成不消解
    （拿原始残句去分类，大概率弃权走默认流程）也比让创建研究整个失败好。
    """
    if demo_fake_backends_enabled():  # 演示/测试：复用假后端，不建真连接
        demo_llm, demo_search = build_demo_backends()
        try:
            yield demo_llm
        finally:
            await demo_search.aclose()
        return
    try:
        settings.validate_llm()
        await _validate_runtime_provider_url(settings)
        llm = LLM(settings, Tracer())
    except Exception:
        logger.warning("intent llm unavailable; multi-turn resolution degraded", exc_info=True)
        yield None
        return
    try:
        yield llm
    finally:
        try:
            await llm.aclose()
        except Exception:
            logger.exception("failed to close intent llm")


@asynccontextmanager
async def _no_llm() -> AsyncIterator[Any | None]:
    """与 ``_intent_llm`` 同形状的空实现：不构造任何连接，直接给 None。

    让「要不要 LLM」这个判断留在调用点的一行三元里，而不是散进
    ``_intent_llm`` 内部再加一个参数——后者会让那个函数同时负责
    「构造 LLM」和「决定要不要构造」两件事。
    """
    yield None


@app.post("/api/intent/assess", dependencies=[Depends(require_api_key)])
async def assess_intent(req: AssessRequest, request: Request) -> AssessResponse:
    """澄清循环的一轮：判断信息够不够开始研究，不够就给出追问与候选项。

    **这个端点不创建任何 run，也不写库。** 这正是它存在的理由——旧实现让澄清
    走完整的建 run 流程再 halt，于是历史列表里躺着一条状态 done、却什么都没
    研究的记录。把判定挪到建 run 之前，既不脏历史，也不需要引入「挂起态」
    （那会把崩溃恢复、租约 fencing、事件回放全部拖进多轮语义里）。

    成本纪律：第一轮只跑规则 + 本地模型（零 token）。只有进入第二轮才让 LLM
    生成贴合具体提问的候选项——能走到第二轮说明情况确实复杂，值得花这次钱。
    """
    _check_assess_rate_limit(request)
    settings = request.app.state.settings
    if not settings.intent_enabled:
        # 意图识别关掉了就不该由它拦路：直接放行，让用户照常提问。
        return AssessResponse(ready=True, resolved_query=req.query, reason="意图识别已关闭")

    # 累积到的答案先并进 query，再判定——判定必须作用在「补充之后」的问题上，
    # 否则每一轮看到的都是最初那句残缺的话，gap 永远消不掉。
    composed = readiness_module.compose_query(req.query, req.answers)

    # 只有真要用 LLM 时才构造它：第一轮纯规则 + 本地模型，多轮消解也只在带
    # history 时才需要。无谓地构造既白付一次连接开销，在没配 key 的环境里
    # 还会刷一串「llm unavailable」告警，把真正的问题淹掉。
    # skip 请求同理：它不再生成候选项，第二轮起的 LLM 只为选项服务——
    # 除非带 history 要做消解，否则纯属白建。
    needs_llm = (req.round >= 1 and not req.skip) or bool(req.history)

    async with _intent_llm(request.app, settings) if needs_llm else _no_llm() as intent_llm:
        cascade = IntentCascade(llm=intent_llm, enable_llm=intent_llm is not None)
        decision = await cascade.classify(
            composed,
            history=req.history,
            extract_entities=False,  # 与预路由同理：实体留给异步段抽
            allow_clarification=False,  # 澄清由 readiness 判，不走 clarify 那条兜底路径
        )
        # 已抽到的槽位要并回累积答案：用户这轮填的「医疗」既是答案也是槽位，
        # 下一轮的 gap 判定要看到它。
        merged = readiness_module.merge_slots_for_assess(req.answers, decision.slots)
        decision.slots = merged
        verdict = readiness_module.assess(decision, composed)

        if decision.blocked:
            # 拒识不在这里终止：前端照常走 create_run，让这条请求留下
            # 「为什么被拒」的审计记录。澄清是产品交互，拒识是安全事件。
            # resolved_query 保持 composed 原样：留痕要的是用户提交的内容，
            # 而不是消解器改写后的版本。
            return AssessResponse(
                ready=True,
                resolved_query=composed,
                blocked=True,
                intent=decision.intent,
                reason=verdict.reason,
            )

        # 用户显式跳过：不再追问，但已答的槽位必须并进最终问题——
        # 「直接研究”跳过的是后续追问，不是用户已经给过的答案。曾经前端
        # 对跳过直接拿**最初的残句**建 run，第一轮答的实体全被扔掉。
        if req.skip:
            return AssessResponse(
                ready=True,
                resolved_query=decision.effective_query(composed),
                intent=decision.intent,
                gap=verdict.gap,
                reason="用户选择跳过追问，带现有信息开始研究",
            )

        # 轮次上限：到顶强制放行。把用户问烦的代价，高于跑一次信息不全的研究。
        # 判据是 >= MAX：round 是「已答过几轮」，第 N 轮的提问在 round=N-1 时发出，
        # 用 MAX-1 会让 UI 里「第 3/3 轮」永远问不出来——分母成了谎言。
        if verdict.ready or req.round >= MAX_CLARIFY_ROUNDS:
            return AssessResponse(
                ready=True,
                # 消解过就返回消解结果：前端拿它去建 run，create_run 的二次消解
                # 在完整问题上不会再触发，同一条追问不必付两次消解调用——
                # 也不必把最终效果押在第二次消解恰好也成功上。
                resolved_query=decision.effective_query(composed),
                intent=decision.intent,
                gap=verdict.gap,
                reason=verdict.reason if verdict.ready else "已达追问上限，带现有信息开始研究",
            )

        options: list[str] = []
        question = verdict.question
        if req.round >= 1:
            generated = await readiness_module.llm_options(
                composed, verdict, merged, llm=intent_llm
            )
            if generated is not None:
                question, options = generated.question, generated.options
        if not options:
            # 第一轮，或 LLM 不可用/失败：退回零成本的规则模板。
            options = readiness_module.rule_options(verdict.gap)

    return AssessResponse(
        ready=False,
        resolved_query=composed,
        question=question,
        options=options,
        gap=verdict.gap,
        intent=decision.intent,
        reason=verdict.reason,
    )


@app.post("/api/runs", status_code=202, dependencies=[Depends(require_api_key)])
async def create_run(
    req: CreateRunRequest,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128),
) -> CreateRunResponse:
    _check_rate_limit(request)
    repo: ResearchRepository = request.app.state.repo
    settings = _settings_for(request.app.state.settings, req.params)
    supplied_plan = None
    if req.execution_plan is not None:
        try:
            supplied_plan = coerce_execution_plan(
                req.execution_plan,
                query=req.query,
                initial=True,
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_execution_plan",
                    "message": str(exc),
                },
            ) from exc
        # An explicit plan upgrades an explicitly legacy deployment to the
        # artifact/planner runtime so the caller's execution contract is honored.
        if settings.orchestration_mode == "legacy":
            settings = replace(settings, orchestration_mode="planner-driven")
    lease_owner = uuid4().hex

    # 意图预路由必须在 create_initial_execution 之前：工作流定义会被写进初始
    # checkpoint 且崩溃恢复直接读它，等流程跑起来再路由就改不动执行路径了。
    # 单轮请求只跑规则 + 本地模型——这里在 HTTP 同步段上，升级到 LLM 会把
    # 创建研究的响应从毫秒级拉到秒级。带 history 的多轮请求是例外（见 preroute_workflow）。
    from .workflows import WORKFLOWS, get_workflow

    workflow_name: str | None
    intent_decision: IntentDecision | None
    route: RoutingPlan | None
    if supplied_plan is not None:
        # A caller-authored plan is already routed.  Do not spend an intent
        # classifier call or let clarification rewrite its execution contract.
        workflow_name, intent_decision, route = (
            req.workflow or "deep",
            None,
            None,
        )
    elif req.history:
        # 借一个 LLM 做指代消解，用完立刻归还——作用域只包住预路由这一步，
        # 不能让它跨越后面的落库与任务派发（那会把连接白白占住）。
        async with _intent_llm(request.app, settings) as intent_llm:
            workflow_name, intent_decision, route = await preroute_workflow(
                req.query,
                requested_workflow=req.workflow,
                available_workflows=set(WORKFLOWS),
                enabled=settings.intent_enabled,
                llm=intent_llm,
                history=req.history,
                allow_clarification=not req.clarified,
            )
    else:
        workflow_name, intent_decision, route = await preroute_workflow(
            req.query,
            requested_workflow=req.workflow,
            available_workflows=set(WORKFLOWS),
            enabled=settings.intent_enabled,
            allow_clarification=not req.clarified,
        )
    if intent_decision is not None and intent_decision.needs_clarification:
        # 信息不全的请求不建 run：旧实现会建一个 run、跑到 IntentRouter 再 halt，
        # 于是历史里留下一条状态 done、却什么都没研究的记录。
        #
        # 注意这里**只拦澄清，不拦拒识**——拒识必须照常建 run，把「这条请求
        # 为什么被拒」留成审计痕迹。二者今天共用 halt 路径，但产品语义不同：
        # 拒识是安全事件（要留痕），澄清是产品交互（不该脏历史）。
        #
        # 正常前端不会撞到这个 422（它会先调 /api/intent/assess）。这是兜底：
        # 防止绕过 assess 的调用方把一个信息不全的请求直接送进研究。
        clarification = intent_decision.clarification
        raise HTTPException(
            status_code=422,
            detail={
                "code": "needs_clarification",
                # 这条文案会经前端错误框直达用户（assess 不可用时的兜底路径），
                # 不能是「请调用 /api/intent/assess」这种开发者语言。
                "message": "请求信息不足，请把问题说得更具体一些",
                "question": clarification.question if clarification else "",
                "options": list(clarification.options) if clarification else [],
            },
        )

    # 黑板 query 用消解后的完整问题：它是 Planner 拆解、Reflector 补洞、
    # Synthesizer 综合共同的靶子——只把消解结果放进 scratch 键的话，读键的
    # 只有 Planner，反思与综合仍对着「那第二个呢」进行。用户敲的原文保留在
    # run 记录里（下面的 repo.create_run），审计与历史列表不受影响。
    effective_query = (
        intent_decision.effective_query(req.query) if intent_decision is not None else req.query
    )
    execution = create_initial_execution(
        effective_query,
        workflow_name,
        settings,
        requested_workflow=req.workflow,
        execution_plan=supplied_plan,
    )
    if intent_decision is not None:
        if route is None:
            raise RuntimeError("intent route is missing for a classified request")
        # 把判定写进初始 checkpoint：流程内的 IntentRouter 复用它而不重判，
        # 恢复后的运行也拿到与首次完全一致的意图结论。
        scratch = execution.checkpoint.setdefault("scratch", {})
        if isinstance(scratch, dict):
            scratch[INTENT_SCRATCH_KEY] = intent_decision.model_dump(mode="json")
            if route.applied and route.max_sub_questions is not None:
                # 子问题预算也必须在这里落盘。意图门禁在所有入口统一执行，而路由
                # 结果通常是 deep/quick/teams——这些流程共享同一份 checkpoint；预算
                # 若只由某个角色写入就永远到不了 Planner。预算只能收紧：
                # 与用户配置取 min，绝不放宽。
                scratch[INTENT_SUB_QUESTION_KEY] = min(
                    route.max_sub_questions, settings.max_sub_questions
                )
            route_policy = route.execution_policy
            scratch[INTENT_ROUTE_KEY] = {
                "applied": route.applied,
                "workflow": route.workflow,
                "max_sub_questions": route.max_sub_questions,
                "reason": route.reason,
                "strategy": getattr(route, "strategy", ""),
                "execution_policy": route_policy.model_dump(mode="json") if route_policy else None,
            }
            # Persist the full execution policy alongside the legacy route
            # fields.  Checkpoint recovery must not re-derive behavior from a
            # newer classifier or model bundle.
            # A user-selected workflow remains authoritative.  Keep the policy
            # inside IntentDecision for audit, but only activate route-derived
            # runtime limits when automatic routing was actually applied.
            policy = route_policy if route.applied else None
            if policy is not None:
                scratch[INTENT_POLICY_KEY] = policy.model_dump(mode="json")
    catalog = getattr(request.app.state, "catalog", None)
    if not execution.definition:
        workflow_def = None
        if catalog is not None:
            try:
                workflow_def = await catalog.get_workflow_def(workflow_name)
            except Exception:
                logger.exception("failed to snapshot custom workflow %s", workflow_name)
        if workflow_def is not None and workflow_def.enabled:
            execution.definition = {
                "name": workflow_def.name,
                "description": workflow_def.description,
                "steps": workflow_def.steps,
                "nodes": workflow_def.nodes,
                "edges": workflow_def.edges,
            }
        else:
            execution.definition = get_workflow(workflow_name).model_dump(mode="json")
        execution.workflow_name = str(execution.definition["name"])
    await snapshot_catalog_for_execution(execution, catalog)
    normalized_key = idempotency_key.strip() if idempotency_key else None
    normalized_key = normalized_key or None
    if _worker_mode(request.app):
        # worker 模式：本进程只入队。不占准入名额、不建 EventHub、不派发 task——
        # 执行、租约与事件全部归领取到该 run 的 worker。
        return await _enqueue_run(request, repo, req, execution, normalized_key, response)
    admission = await _acquire_run_slot(request.app)
    try:
        run_id, created = await repo.create_run_once(
            req.query,
            request_hash=_run_request_hash(req),
            idempotency_key=normalized_key,
            execution=execution,
            lease_owner=lease_owner,
        )
    except IdempotencyConflictError as exc:
        admission.release()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "idempotency_conflict",
                "message": "Idempotency-Key 已用于不同的请求",
            },
        ) from exc
    except BaseException:
        admission.release()
        raise
    if not created:
        admission.release()
        response.headers["Idempotency-Replayed"] = "true"
        return CreateRunResponse(run_id=run_id)
    request.app.state.live[run_id] = EventHub()
    execution_kwargs: dict[str, Any] = {"requested_workflow": req.workflow}
    if supplied_plan is not None:
        execution_kwargs["execution_plan"] = supplied_plan
    execution_coro = _execute_with_admission(
        admission,
        request.app,
        run_id,
        effective_query,
        settings,
        workflow_name,
        None,
        lease_owner,
        execution,
        **execution_kwargs,
    )
    try:
        task = asyncio.create_task(execution_coro)
    except BaseException:
        # Task construction can fail (for example during loop shutdown or a
        # test-injected scheduler error). Release every resource acquired
        # above so the process does not become permanently saturated.
        execution_coro.close()
        _close_live_hub(request.app, run_id)
        admission.release()
        try:
            await request.app.state.repo.set_status(run_id, "error", lease_owner=lease_owner)
        except Exception:
            logger.exception("failed to mark run %s after task creation failure", run_id)
        try:
            await request.app.state.repo.release_lease(run_id, lease_owner)
        except Exception:
            logger.exception("failed to release run %s lease after task creation failure", run_id)
        raise
    _track_run_task(request.app, run_id, task, lease_owner=lease_owner, admission=admission)
    return CreateRunResponse(run_id=run_id)


@app.post(
    "/api/runs/{run_id}/cancel",
    status_code=202,
    dependencies=[Depends(require_api_key)],
)
async def cancel_run(run_id: str, request: Request) -> CancelRunResponse:
    repo: ResearchRepository = request.app.state.repo
    status = await repo.request_cancel(run_id)
    if status is None:
        raise HTTPException(status_code=404, detail="run not found")
    if status in {"done", "error"}:
        raise HTTPException(status_code=409, detail=f"run is already {status}")
    if status == "cancelled":
        return CancelRunResponse(run_id=run_id, status=status)

    run_tasks: dict[str, asyncio.Task[None]] = getattr(request.app.state, "run_tasks", {})
    task = run_tasks.get(run_id)
    if task is not None and not task.done():
        requested_cancellations: set[str] = getattr(
            request.app.state, "cancellation_requested", set()
        )
        if not hasattr(request.app.state, "cancellation_requested"):
            request.app.state.cancellation_requested = requested_cancellations
        requested_cancellations.add(run_id)
        task.cancel()
    return CancelRunResponse(run_id=run_id, status="cancelling")


@app.post(
    "/api/runs/{run_id}/resume",
    status_code=202,
    dependencies=[Depends(require_api_key)],
)
async def resume_run(run_id: str, request: Request) -> CreateRunResponse:
    if run_id in request.app.state.live:
        raise HTTPException(status_code=409, detail="run is already active")
    detail = await request.app.state.repo.get_run(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="run not found")
    execution = detail.orchestration
    if execution is None or not execution.checkpoint:
        raise HTTPException(status_code=409, detail="run has no recoverable checkpoint")
    if detail.status in {"done", "cancelled", "cancelling"}:
        raise HTTPException(status_code=409, detail="terminal or cancelling run cannot be resumed")
    if _worker_mode(request.app):
        # worker 模式：交还队列而不是自己执行。不取租约——取了 worker 反而领不走。
        if detail.status == "error":
            requeued = await request.app.state.repo.requeue_failed_run(run_id)
        else:
            requeued = await request.app.state.repo.enqueue_run(run_id)
        if not requeued:
            raise HTTPException(status_code=409, detail="run is no longer resumable")
        return CreateRunResponse(run_id=run_id)
    # A fresh token identifies this execution attempt, so concurrent resume
    # requests cannot renew and share the same lease.
    owner = uuid4().hex
    if not await request.app.state.repo.acquire_lease(run_id, owner):
        raise HTTPException(status_code=409, detail="run is leased by another instance")
    handed_off = False
    admission: RunAdmissionLease | None = None
    try:
        # Re-read after acquiring the lease. The first snapshot may have been
        # stale while the previous worker was finishing or writing a checkpoint.
        detail = await request.app.state.repo.get_run(run_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="run not found")
        execution = detail.orchestration
        if execution is None or not execution.checkpoint:
            raise HTTPException(status_code=409, detail="run has no recoverable checkpoint")
        if detail.status in {"done", "cancelled", "cancelling"}:
            raise HTTPException(
                status_code=409, detail="terminal or cancelling run cannot be resumed"
            )
        admission = await _acquire_run_slot(request.app)
        resume_settings = _settings_for_resume(request.app.state.settings, execution)
        # Publish the new attempt before returning 202 so clients cannot keep
        # treating the previous attempt's terminal status as authoritative.
        execution.attempt = await request.app.state.repo.prepare_resume(run_id, lease_owner=owner)
        request.app.state.live[run_id] = EventHub()
        execution_coro = _execute_with_admission(
            admission,
            request.app,
            run_id,
            detail.query,
            resume_settings,
            execution.workflow_name,
            execution,
            owner,
        )
        try:
            task = asyncio.create_task(execution_coro)
        except BaseException:
            execution_coro.close()
            raise
        handed_off = True
        _track_run_task(request.app, run_id, task, lease_owner=owner, admission=admission)
        return CreateRunResponse(run_id=run_id)
    finally:
        if not handed_off:
            _close_live_hub(request.app, run_id)
            if admission is not None:
                admission.release()
            try:
                await request.app.state.repo.release_lease(run_id, owner)
            except Exception:
                logger.exception("failed to release resume lease for %s", run_id)


@app.get("/api/workflows", dependencies=[Depends(require_api_key)])
async def list_workflows(request: Request) -> list[dict[str, str]]:
    """可选的任务流程列表（供前端选择器）：内置预置 + 已启用的自定义工作流。"""
    from .workflows import DEFAULT_WORKFLOW, PUBLIC_WORKFLOWS

    out = [
        {
            "name": wf.name,
            "description": wf.description,
            "default": str(wf.name == DEFAULT_WORKFLOW),
            "custom": "False",
        }
        for wf in PUBLIC_WORKFLOWS.values()
    ]
    try:  # 并入自定义工作流；catalog 不可用时优雅降级，仅返回内置
        for wd in await request.app.state.catalog.list_workflow_defs():
            if wd.enabled:
                out.append(
                    {
                        "name": wd.name,
                        "description": wd.description or wd.display_name,
                        "default": "False",
                        "custom": "True",
                    }
                )
    except Exception:
        logger.exception("读取自定义工作流失败，仅返回内置流程")
    return out


# 可编排进自定义工作流的内置角色（排除 coordinator/aggregator——它们是 compose/fanout 专用原语）。
# label/description 供构建器角色选择器展示——裸英文标识符对不熟悉本系统术语的用户过于晦涩。
# 声明顺序即管线执行序（规划→研究→反思→综合→评审），角色列表按此展示而非字母序。
_COMPOSABLE_BUILTINS: dict[str, tuple[str, str]] = {
    "planner": (
        "规划师",
        "把研究问题拆解为若干可独立检索的子问题，并标注子问题间的依赖关系。",
    ),
    "researcher": (
        "研究员",
        "对子问题并行检索网络，只保留带逐字证据、通过程序验证的发现。",
    ),
    "reflector": (
        "反思者",
        "评估现有证据是否充分，不足时提出补洞子问题（通常配合反思循环使用）。",
    ),
    "synthesizer": (
        "综合者",
        "把已验证的发现综合成带 [n] 引用的最终报告；必须是工作流的唯一终端节点。",
    ),
    "critic": (
        "评审员",
        "对综合后的报告做批判性复核，指出遗漏、矛盾与修订建议。",
    ),
}


@app.get("/api/roles", dependencies=[Depends(require_api_key)])
async def list_roles(request: Request) -> list[dict[str, object]]:
    """可编排角色（供构建器角色选择器）：内置可组合角色 + 已启用自定义卡片。"""
    from .catalog.runtime import terminal_roles_for_cards
    from .registry import available

    builtin = {n for n in available() if n in _COMPOSABLE_BUILTINS}
    roles: dict[str, dict[str, object]] = {
        name: {
            "name": name,
            "label": _COMPOSABLE_BUILTINS[name][0],
            "description": _COMPOSABLE_BUILTINS[name][1],
            "icon": "◆",
            "builtin": True,
            "produces_report": name == "synthesizer",
        }
        for name in _COMPOSABLE_BUILTINS
        if name in builtin
    }
    try:
        cards = [card for card in await request.app.state.catalog.list_agents() if card.enabled]
        terminal_roles = terminal_roles_for_cards(cards)
        for card in cards:
            roles[card.name] = {
                "name": card.name,
                "label": card.display_name or card.name,
                "description": card.description,
                "icon": card.icon,
                "builtin": False,
                "produces_report": card.name in terminal_roles,
            }
    except Exception:
        logger.exception("读取自定义角色卡片失败，仅返回内置角色")
    return list(roles.values())


@app.get("/api/config", dependencies=[Depends(require_api_key)])
async def get_config(request: Request) -> ConfigView:
    """当前全局配置（密钥脱敏）。"""
    try:
        return _config_view(request.app.state.settings)
    except ValidationError:
        # 自愈：内存里的 Settings 被历史污染的 overrides（如 llm_model=null）弄脏时，
        # 以「环境变量 + 清洗后覆盖」重建并回写文件，端点恢复可用而非永久 500
        overrides = _sanitized_overrides(runtime_config.load_overrides())
        settings = runtime_config.apply_overrides(Settings(), overrides)
        request.app.state.settings = settings
        try:
            runtime_config.save_overrides(overrides)
        except OSError:
            logger.exception("回写清洗后的持久化配置失败")
        return _config_view(settings)


@app.put("/api/config", dependencies=[Depends(require_api_key)])
async def update_config(req: ConfigUpdate, request: Request) -> ConfigView:
    # Serialize read/validate/write/switch as one transaction. Without this,
    # concurrent PUTs can lose fields and a failed disk write leaves memory
    # using a configuration that was never persisted.
    async with _config_lock(request):
        return await _update_config_unlocked(req, request)


async def _update_config_unlocked(req: ConfigUpdate, request: Request) -> ConfigView:
    """更新并持久化全局配置；对后续创建的 run 生效。"""
    overrides = _sanitized_overrides(runtime_config.load_overrides())
    # Runtime-entered credentials are intentionally absent from the JSON
    # file. Preserve them across later non-secret settings updates.
    for secret_field in runtime_config.SECRET_FIELDS:
        current_secret = getattr(request.app.state.settings, secret_field, "")
        if current_secret:
            overrides[secret_field] = current_secret
    payload = req.model_dump(exclude_unset=True)
    for key, value in payload.items():
        if key in runtime_config.SECRET_FIELDS:
            if value:  # 空＝保持不变（不被脱敏表单覆盖清空）
                overrides[key] = value
        elif key == "llm_base_url":
            overrides[key] = value or None  # 显式空串＝清空回默认端点
        else:
            overrides[key] = value
    # 以「环境变量 + 新覆盖」重建，复用 Settings.__post_init__ 范围校验；
    # 响应视图也必须先构造成功——全部校验通过后才允许提交内存状态与落盘
    try:
        new_settings = runtime_config.apply_overrides(Settings(), overrides)
        try:
            await validate_provider_url_resolved(
                new_settings.llm_base_url,
                allow_private=new_settings.allow_private_provider_urls,
                allowlist=new_settings.provider_host_allowlist,
            )
        except ProviderURLPolicyError as exc:
            raise ValueError(f"llm_base_url 无效：{exc}") from exc
        view = _config_view(new_settings)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=422, detail=f"配置非法：{e}") from e
    # Persist first. A failed replace must leave the running process on the
    # previous settings so a 500 response does not create split-brain state.
    try:
        runtime_config.save_overrides(overrides)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail="failed to persist runtime configuration"
        ) from exc
    request.app.state.settings = new_settings
    return view


@app.get("/api/runs", dependencies=[Depends(require_api_key)])
async def list_runs(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None),
    q: str | None = Query(None, max_length=200),
    tag: str | None = Query(None, max_length=64),
) -> list[RunSummary]:
    repo: ResearchRepository = request.app.state.repo
    return await repo.list_runs(limit=limit, offset=offset, status=status, q=q, tag=tag)


@app.get("/api/tags", dependencies=[Depends(require_api_key)])
async def list_tags(request: Request) -> list[TagCount]:
    repo: ResearchRepository = request.app.state.repo
    return await repo.list_tags()


async def _delete_run_if_idle(repo: ResearchRepository, run_id: str) -> str:
    detail = await repo.get_run(run_id)
    if detail is None:
        return "missing"
    if detail.orchestration is None:
        if detail.status in RUN_ACTIVE_STATUSES:
            # Legacy workers created the workflow row after the research row,
            # so these states can still represent active work without a lease.
            return "leased"
        return "deleted" if await repo.delete_run(run_id) else "missing"

    owner = uuid4().hex
    if not await repo.acquire_lease(run_id, owner):
        return "leased"
    try:
        return "deleted" if await repo.delete_run(run_id) else "missing"
    finally:
        # Deletion cascades the lease row; release remains useful if a
        # concurrent delete won after acquisition or deletion raised.
        try:
            await repo.release_lease(run_id, owner)
        except Exception:
            logger.exception("failed to release delete lease for %s", run_id)


@app.delete("/api/runs/{run_id}", status_code=204, dependencies=[Depends(require_api_key)])
async def delete_run(run_id: str, request: Request) -> Response:
    app_ = request.app
    if app_.state.live.get(run_id) is not None:
        raise HTTPException(status_code=409, detail="运行进行中，无法删除")
    outcome = await _delete_run_if_idle(app_.state.repo, run_id)
    if outcome == "leased":
        raise HTTPException(status_code=409, detail="运行进行中，无法删除")
    if outcome == "missing":
        raise HTTPException(status_code=404, detail="run not found")
    return Response(status_code=204)


@app.post("/api/runs/batch_delete", dependencies=[Depends(require_api_key)])
async def batch_delete(req: BatchDeleteRequest, request: Request) -> BatchDeleteResponse:
    app_ = request.app
    deleted = skipped = 0
    for run_id in req.ids:
        if app_.state.live.get(run_id) is not None:
            skipped += 1  # 进行中：跳过而非报错，批量操作尽量推进
            continue
        outcome = await _delete_run_if_idle(app_.state.repo, run_id)
        if outcome == "leased":
            skipped += 1
        elif outcome == "deleted":
            deleted += 1
    return BatchDeleteResponse(deleted=deleted, skipped=skipped)


@app.put("/api/runs/{run_id}/tags", dependencies=[Depends(require_api_key)])
async def set_tags(run_id: str, req: TagsUpdate, request: Request) -> RunDetail:
    repo: ResearchRepository = request.app.state.repo
    if await repo.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    await repo.set_tags(run_id, req.tags)
    detail = await repo.get_run(run_id)
    if detail is None:  # 理论不可达（上面已校验）；类型收敛
        raise HTTPException(status_code=404, detail="run not found")
    return await _enrich_run_detail(repo, detail)


async def _enrich_run_detail(repo: ResearchRepository, detail: RunDetail) -> RunDetail:
    # Keep the detail payload bounded; callers that need the complete history
    # can page through the dedicated events endpoint.
    detail.events = await repo.get_events(detail.id, limit=_RUN_DETAIL_EVENT_LIMIT)
    scratch = detail.orchestration.checkpoint.get("scratch", {}) if detail.orchestration else {}
    raw_manifest = scratch.get(RUN_MANIFEST_CHECKPOINT_KEY) if isinstance(scratch, dict) else None
    if raw_manifest is not None:
        detail.manifest = RunManifest.model_validate(raw_manifest)
    raw_intent = scratch.get(INTENT_SCRATCH_KEY) if isinstance(scratch, dict) else None
    if raw_intent is not None:
        try:
            detail.intent = IntentDecision.model_validate(raw_intent)
        except ValidationError:
            # 旧 checkpoint 的意图结构可能与当前 schema 不符；不能让历史详情整体 500。
            logger.warning("run %s has an unreadable intent decision", detail.id)
    require_corroboration = _run_requires_corroboration(detail)
    detail.metrics = quality_metrics(detail, require_corroboration=require_corroboration)
    return detail


def _run_requires_corroboration(detail: RunDetail) -> bool:
    """Read the persisted per-run corroboration gate for report consumers.

    Export endpoints may be called without first going through
    ``_enrich_run_detail``.  Prefer the parsed manifest, then fall back to
    the original settings checkpoint for older runs whose manifest was not
    written yet.  The value is a serialized boolean; malformed values are
    treated as disabled rather than relying on truthiness (``"false"``).
    """
    if detail.manifest is not None and "require_corroboration" in detail.manifest.settings:
        return detail.manifest.settings.get("require_corroboration") is True
    execution = detail.orchestration
    checkpoint = execution.checkpoint if execution is not None else None
    scratch = checkpoint.get("scratch") if isinstance(checkpoint, dict) else None
    if not isinstance(scratch, dict):
        return False
    raw_manifest = scratch.get(RUN_MANIFEST_CHECKPOINT_KEY)
    if isinstance(raw_manifest, dict):
        settings = raw_manifest.get("settings")
        if isinstance(settings, dict) and settings.get("require_corroboration") is True:
            return True
    raw_settings = scratch.get(RUN_SETTINGS_CHECKPOINT_KEY)
    return isinstance(raw_settings, dict) and raw_settings.get("require_corroboration") is True


@app.get("/api/runs/{run_id}", dependencies=[Depends(require_api_key)])
async def get_run(run_id: str, request: Request) -> RunDetail:
    repo: ResearchRepository = request.app.state.repo
    detail = await repo.get_run(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="run not found")
    return await _enrich_run_detail(repo, detail)


@app.get("/api/runs/{run_id}/document", dependencies=[Depends(require_api_key)])
async def get_run_document(
    run_id: str,
    request: Request,
    include_hsi_tables: bool = Query(default=False),
) -> ReportDocument:
    """结构化报告文档：证据装置随报告一起交付，不再只存在于前端的即时 join。

    这是 Markdown / HTML / 打印三种导出的共同数据源。它与 ``GET /api/runs/{id}``
    并列而不是取代后者——``RunDetail`` 仍按原样返回 ``report.citations`` 等字段，
    既有前端与质量指标链路不受影响。
    """
    repo: ResearchRepository = request.app.state.repo
    detail = await repo.get_run(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="run not found")
    # 拦截数来自 source_policy 审计事件。这里取完整事件流而不是详情里被截断的那份：
    # 截断会让长运行的拦截数偏小，而偏小的安全指标比缺失更糟。
    events = await repo.get_events(run_id, limit=_DOCUMENT_EVENT_LIMIT)
    return assemble_document(
        detail.report,
        detail.results,
        events=events,
        query=detail.query,
        require_corroboration=_run_requires_corroboration(detail),
        include_hsi_tables=include_hsi_tables,
    )


@app.get("/api/runs/{run_id}/document.md", dependencies=[Depends(require_api_key)])
async def get_run_document_markdown(
    run_id: str,
    request: Request,
    include_hsi_tables: bool = Query(default=False),
) -> PlainTextResponse:
    """Download the report as Markdown, with the evidence apparatus included.

    This is deliberately not the same bytes as ``report.markdown``.  That field
    is the synthesizer's prose alone; downloading it drops the very things that
    make the report auditable — the per-claim verification status, the quote
    that was matched, the snapshot hash.  ``render_markdown`` projects the same
    assembled document the CSV/XLSX/PDF exports use, so every format makes the
    same claims about the same run.
    """
    repo: ResearchRepository = request.app.state.repo
    detail = await repo.get_run(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="run not found")
    events = await repo.get_events(run_id, limit=_DOCUMENT_EVENT_LIMIT)
    document = assemble_document(
        detail.report,
        detail.results,
        events=events,
        query=detail.query,
        require_corroboration=_run_requires_corroboration(detail),
        include_hsi_tables=include_hsi_tables,
    )
    try:
        markdown_text = render_markdown(document)
    except ChartDataError as exc:
        # A chart whose source table is missing means assembly produced an
        # inconsistent document.  Surface it rather than shipping a file with
        # a silently dropped section.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    safe_run_id = re.sub(r"[^A-Za-z0-9._-]", "_", run_id).strip("._-")[:80] or "run"
    return PlainTextResponse(
        markdown_text,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="research-{safe_run_id}.md"'},
    )


@app.get("/api/runs/{run_id}/document.csv", dependencies=[Depends(require_api_key)])
async def get_run_document_csv(
    run_id: str,
    request: Request,
    table_id: str | None = Query(default=None, min_length=1, max_length=128),
    include_hsi_tables: bool = Query(default=False),
) -> PlainTextResponse:
    """Download one structured report table as UTF-8 CSV.

    Reports can contain heterogeneous tables, so ``table_id`` is required
    when more than one ``TableBlock`` is present.  A run without a table (for
    example, while it is still streaming) returns an empty CSV body.
    """
    repo: ResearchRepository = request.app.state.repo
    detail = await repo.get_run(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="run not found")

    events = await repo.get_events(run_id, limit=_DOCUMENT_EVENT_LIMIT)
    document = assemble_document(
        detail.report,
        detail.results,
        events=events,
        query=detail.query,
        require_corroboration=_run_requires_corroboration(detail),
        include_hsi_tables=include_hsi_tables,
    )
    try:
        csv_text = render_csv(document, table_id=table_id)
    except CsvTableNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CsvTableSelectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Run ids are normally UUIDs, but sanitising the path value keeps the
    # download header safe for custom repository implementations as well.
    safe_run_id = re.sub(r"[^A-Za-z0-9._-]", "_", run_id).strip("._-")[:80] or "run"
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="research-{safe_run_id}.csv"'},
    )


@app.get("/api/runs/{run_id}/document.xlsx", dependencies=[Depends(require_api_key)])
async def get_run_document_xlsx(
    run_id: str,
    request: Request,
    table_id: str | None = Query(default=None, min_length=1, max_length=128),
    include_hsi_tables: bool = Query(default=False),
) -> Response:
    """Download one structured report table as an XLSX workbook.

    ``openpyxl`` is an optional installation extra.  Delaying its import to
    ``render_xlsx`` keeps the API usable in minimal deployments; those
    deployments receive a clear 501 response only when this endpoint is
    requested.
    """
    repo: ResearchRepository = request.app.state.repo
    detail = await repo.get_run(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="run not found")

    events = await repo.get_events(run_id, limit=_DOCUMENT_EVENT_LIMIT)
    document = assemble_document(
        detail.report,
        detail.results,
        events=events,
        query=detail.query,
        require_corroboration=_run_requires_corroboration(detail),
        include_hsi_tables=include_hsi_tables,
    )
    try:
        xlsx_bytes = render_xlsx(document, table_id=table_id)
    except XlsxDependencyError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except XlsxTableNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except XlsxTableSelectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    safe_run_id = re.sub(r"[^A-Za-z0-9._-]", "_", run_id).strip("._-")[:80] or "run"
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="research-{safe_run_id}.xlsx"'},
    )


@app.get("/api/runs/{run_id}/document.pdf", dependencies=[Depends(require_api_key)])
async def get_run_document_pdf(
    run_id: str,
    request: Request,
    include_hsi_tables: bool = Query(default=False),
) -> Response:
    """Download a server-rendered PDF when the optional PDF extra is installed."""
    repo: ResearchRepository = request.app.state.repo
    detail = await repo.get_run(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="run not found")
    events = await repo.get_events(run_id, limit=_DOCUMENT_EVENT_LIMIT)
    document = assemble_document(
        detail.report,
        detail.results,
        events=events,
        query=detail.query,
        require_corroboration=_run_requires_corroboration(detail),
        include_hsi_tables=include_hsi_tables,
    )
    try:
        pdf_bytes = render_pdf(document)
    except PdfExportUnavailable as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    safe_run_id = re.sub(r"[^A-Za-z0-9._-]", "_", run_id).strip("._-")[:80] or "run"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="research-{safe_run_id}.pdf"'},
    )


@app.get("/api/runs/{run_id}/events", dependencies=[Depends(require_api_key)])
async def get_events(
    run_id: str,
    request: Request,
    after_seq: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
) -> list[Event]:
    """事件回放。after_seq 为「跳过前 N 条」的偏移语义（客户端传已收到的条数）。"""
    repo: ResearchRepository = request.app.state.repo
    # 与 /stream 一致：未知 run 返回 404，而非 200 + 空列表（区分「不存在」与「无事件」）
    if request.app.state.live.get(run_id) is None and await repo.get_run_status(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    return await repo.get_events(run_id, after_seq=after_seq, limit=limit)


@app.get("/api/runs/{run_id}/stream", dependencies=[Depends(require_api_key)])
async def stream_run(
    run_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    app_ = request.app
    # 未知 run 返回 404，而非 200 + 空流（让客户端能区分「不存在」与「无事件」）
    if app_.state.live.get(run_id) is None and await app_.state.repo.get_run_status(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")

    after_seq = 0
    if last_event_id:
        try:
            after_seq = int(last_event_id) + 1
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid Last-Event-ID") from exc
        if after_seq < 0:
            raise HTTPException(status_code=400, detail="invalid Last-Event-ID")

    async def gen() -> AsyncIterator[str]:
        async for chunk in _stream_run_sse(app_, run_id, after_seq=after_seq):
            yield chunk

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/research", dependencies=[Depends(require_api_key)], deprecated=True)
async def research(
    request: Request,
    q: str = Query(..., description="研究问题", min_length=1, max_length=2000),
) -> StreamingResponse:
    """兼容旧 SSE 客户端，但执行统一走持久化 run 交付链路。"""
    created = await create_run(
        CreateRunRequest(query=q, clarified=True),
        request,
        Response(),
        idempotency_key=None,
    )
    response = await stream_run(created.run_id, request, last_event_id=None)
    response.headers["X-Run-ID"] = created.run_id
    return response


@app.api_route(
    "/api/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    dependencies=[Depends(require_api_key)],
)
async def unknown_api_route(full_path: str) -> None:
    """Keep unknown API paths JSON/unauthenticated instead of SPA HTML."""
    raise HTTPException(status_code=404, detail="not found")


@app.get("/{full_path:path}", response_class=HTMLResponse)
async def spa_fallback(full_path: str) -> str:
    """SPA history 路由回退：非 /api 路径一律返回前端入口，支持深链接刷新。

    具体路由（/、/healthz、/api/*）按声明顺序优先匹配；此 catch-all 只接管
    其余未注册的 GET 路径（如 /history、/runs/{id}）。
    """
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="not found")
    return await index()
