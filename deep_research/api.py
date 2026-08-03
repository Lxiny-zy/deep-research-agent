"""FastAPI + SSE：实时观看多 Agent 协作 + 持久化历史与回放。

端点：
  GET  /                       前端入口（构建后的 SPA；开发期前端走 Vite dev server）
  GET  /healthz                健康检查（容器探针）
  POST /api/runs               创建研究（后台执行），返回 run_id
  GET  /api/runs               历史列表
  GET  /api/runs/{id}          单次详情（计划 + 结果 + 报告）
  GET  /api/runs/{id}/events   事件回放（一次性，支持 after_seq 增量）
  GET  /api/runs/{id}/stream   SSE：进行中实时推送 / 已结束回放 DB
  GET  /api/research?q=        无持久化的即跑即看快路径（向后兼容）

启动：uvicorn deep_research.api:app
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator

from . import runtime_config
from .agents.intent_router import (
    INTENT_ROUTE_KEY,
    INTENT_SCRATCH_KEY,
    INTENT_SUB_QUESTION_KEY,
)
from .catalog.repository import CatalogRepository
from .config import Settings
from .intent.routing import preroute_workflow
from .intent.types import IntentDecision
from .models import RunManifest
from .observability import Event, EventHub
from .orchestration import WorkflowRun
from .orchestrator import (
    RUN_SETTINGS_CHECKPOINT_KEY,
    DeepResearchAgent,
    create_initial_execution,
    snapshot_catalog_for_execution,
)
from .persistence.db import make_engine, make_sessionmaker, prepare_sqlite_schema
from .persistence.repository import ResearchRepository, RunDetail, RunSummary, TagCount
from .persistence.sql_repository import SqlRepository
from .reproducibility import RUN_MANIFEST_CHECKPOINT_KEY, quality_metrics
from .tools.base import SearchTool

logger = logging.getLogger(__name__)

_LEASE_RENEW_INTERVAL_SECONDS = 60.0
_RECOVERY_INTERVAL_SECONDS = 30.0
_RECOVERY_PAGE_SIZE = 1000
_REMOTE_STREAM_POLL_SECONDS = 0.5
_REMOTE_STREAM_TERMINAL_GRACE_SECONDS = 2.0
_SSE_HEARTBEAT_SECONDS = 15.0
_CHECKPOINT_SETTING_FIELDS = {
    "max_sub_questions",
    "max_rounds",
    "max_concurrency",
    "results_per_search",
    "require_corroboration",
    "max_tokens",
    "max_replans",
    "request_timeout",
}


# 优先用构建后的 SPA（frontend/dist/index.html）；否则回退到内置静态单页 Demo（frontend/index.html）
def _bundle_root() -> Path:
    """Return the project root in source mode, or PyInstaller data root when frozen."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[1]


_FRONTEND_ROOT = _bundle_root() / "frontend"
_FRONTEND_DIST = _FRONTEND_ROOT / "dist" / "index.html"
_FRONTEND_INDEX = _FRONTEND_ROOT / "index.html"


class ResearchParams(BaseModel):
    """per-run 研究参数覆盖（缺省字段沿用服务端 Settings）。"""

    max_sub_questions: int | None = Field(default=None, ge=1, le=12)
    max_rounds: int | None = Field(default=None, ge=0, le=5)
    max_concurrency: int | None = Field(default=None, ge=1, le=16)
    results_per_search: int | None = Field(default=None, ge=1, le=15)
    require_corroboration: bool | None = None
    max_tokens: int | None = Field(default=None, ge=1, le=10_000_000)  # 本次研究 token 预算上限


class CreateRunRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)  # 限长：query 全文进 prompt，防成本放大
    params: ResearchParams | None = None
    workflow: str | None = Field(default=None, max_length=64)  # 任务流程选择；None＝默认 deep


class CreateRunResponse(BaseModel):
    run_id: str


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
    require_corroboration: bool
    request_timeout: float


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
    require_corroboration: bool | None = None
    request_timeout: float | None = Field(default=None, gt=0, le=600)

    @field_validator(
        "llm_model",
        "max_sub_questions",
        "max_rounds",
        "max_concurrency",
        "results_per_search",
        "require_corroboration",
        "request_timeout",
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


def _settings_for_resume(base: Settings, execution: WorkflowRun) -> Settings:
    """Restore the original non-secret run limits from a durable checkpoint."""
    scratch = execution.checkpoint.get("scratch", {})
    raw = scratch.get(RUN_SETTINGS_CHECKPOINT_KEY, {}) if isinstance(scratch, dict) else {}
    if not isinstance(raw, dict):
        return base
    overrides = {name: raw[name] for name in _CHECKPOINT_SETTING_FIELDS if name in raw}
    if not overrides:
        return base
    try:
        return replace(base, **overrides)
    except (TypeError, ValueError):
        logger.warning("run checkpoint contains invalid settings; using current defaults")
        return base


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
        require_corroboration=s.require_corroboration,
        request_timeout=s.request_timeout,
    )


async def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Query(default=None),
) -> None:
    """API 认证：设置了 API_KEY 环境变量即启用，所有 /api 端点必须携带。

    支持 X-API-Key 头或 ?api_key= 查询参数（EventSource 无法自定义头）。
    未配置 API_KEY 时跳过（本地开发零摩擦），但生产部署应当配置。
    """
    expected: str = request.app.state.settings.api_key
    if not expected:
        return
    provided = x_api_key or api_key or ""
    if not secrets.compare_digest(provided.encode(), expected.encode()):
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
            self._hits[key] = hits
            return False
        hits.append(now)
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


_run_limiter = _RateLimiter(max_calls=10, window_seconds=60.0)


def _rate_limit_key(request: Request) -> str:
    """限流 key：默认直连对端 IP；APP_TRUST_PROXY=1 时取 X-Forwarded-For 第一跳。

    开关直读环境变量（部署拓扑属性，不进 config.Settings 的研究行为配置）。
    默认关闭：直连部署下该头可被客户端伪造，信任它等于放开限流。
    """
    if os.environ.get("APP_TRUST_PROXY", "").strip().lower() in {"1", "true", "yes"}:
        forwarded = request.headers.get("x-forwarded-for", "")
        first_hop = forwarded.split(",")[0].strip() if forwarded else ""
        if first_hop:
            return first_hop
    return request.client.host if request.client else "unknown"


def _check_rate_limit(request: Request) -> None:
    if not _run_limiter.check(_rate_limit_key(request)):
        raise HTTPException(status_code=429, detail="too many requests, slow down")


async def _recover_orphaned_runs(app: FastAPI, settings: Settings) -> None:
    """Start recoverable runs whose lease is absent or has expired.

    The lease check remains the final cross-instance arbiter; the repeated scan
    only closes the gap where a crashed worker's TTL expires after startup.
    """
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
        handed_off = False
        live_created = False
        try:
            if summary.id in app.state.live:
                continue
            detail = await app.state.repo.get_run(summary.id)
            execution = detail.orchestration if detail is not None else None
            if detail is None or execution is None:
                await app.state.repo.set_status(summary.id, "error")
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

            await app.state.repo.prepare_resume(summary.id, lease_owner=lease_owner)
            app.state.live[summary.id] = EventHub()
            live_created = True
            try:
                resume_settings = _settings_for_resume(settings, execution)
                task = asyncio.create_task(
                    _execute(
                        app,
                        summary.id,
                        detail.query,
                        resume_settings,
                        execution.workflow_name,
                        execution,
                        lease_owner,
                    )
                )
            except BaseException:
                raise
            handed_off = True
            app.state.tasks.add(task)
            task.add_done_callback(app.state.tasks.discard)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("failed to recover orphaned run %s", summary.id)
        finally:
            if not handed_off and lease_owner is not None:
                if live_created:
                    app.state.live.pop(summary.id, None)
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
    # 叠加前端持久化的全局配置（runtime_config.json）；损坏/非法不阻塞启动
    overrides = _sanitized_overrides(runtime_config.load_overrides())
    if overrides:
        try:
            settings = runtime_config.apply_overrides(settings, overrides)
        except Exception:
            logger.exception("应用持久化配置失败，回退到环境变量配置")
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
        app.state.live = {}  # run_id -> EventHub：进行中 run 的实时事件中枢（向多端 SSE 扇出）
        app.state.tasks = set()  # 持有后台任务引用，避免被 GC 提前回收
        # 自动恢复上次进程中断且已有 checkpoint 的任务；无 checkpoint 的孤儿任务置 error。
        try:
            await _recover_orphaned_runs(app, settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("启动恢复未完成任务失败（不阻塞启动）")
        recovery_task = asyncio.create_task(_recovery_loop(app))
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
        tasks = [task for task in getattr(app.state, "tasks", set()) if task is not recovery_task]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await engine.dispose()


app = FastAPI(title="Deep Research Agent", lifespan=lifespan)

# 角色广场 catalog 路由（模型档案 / 角色卡片 / 搜索 key），统一套用 API key 鉴权
from .catalog_api import router as catalog_router  # noqa: E402 避免与 app 定义循环

app.include_router(catalog_router, dependencies=[Depends(require_api_key)])


@app.middleware("http")
async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    """统一注入基础安全响应头（SPA 由本服务直接托管，需自带防护）。"""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


# 生产同源托管：把 Vite 构建产物的静态资源挂在 /assets/*（须在末尾 catch-all SPA 路由
# 之前注册）。dist 未构建时跳过——开发期前端走 Vite dev server，不经后端静态服务。
_FRONTEND_ASSETS = _FRONTEND_DIST.parent / "assets"
if _FRONTEND_ASSETS.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_ASSETS)), name="assets")


def _sse(event: Event) -> str:
    return f"data: {event.model_dump_json()}\n\n"


async def _stream_run_sse(app: FastAPI, run_id: str) -> AsyncIterator[str]:
    """Stream locally when possible, otherwise follow shared durable state.

    Event persistence currently replaces the complete event list when an
    attempt finishes.  A non-owner instance therefore cannot provide live
    token deltas, but it must keep the SSE request open and eventually replay
    the durable terminal stream instead of returning an empty 200 response.
    """
    repo: ResearchRepository = app.state.repo
    next_heartbeat = time.monotonic() + _SSE_HEARTBEAT_SECONDS

    while True:
        hub: EventHub | None = app.state.live.get(run_id)
        if hub is not None:
            # 长静默步骤（LLM 超时可配到 600s）会被反代的空闲超时掐断连接；
            # 经中转队列带超时读取，静默期间持续发 SSE 注释行保活（客户端会忽略注释行）
            relay: asyncio.Queue[Event | None] = asyncio.Queue()

            async def _pump(source: EventHub, sink: asyncio.Queue[Event | None]) -> None:
                try:
                    async for event in source.stream():
                        sink.put_nowait(event)
                finally:
                    sink.put_nowait(None)  # None 哨兵＝hub 已关闭（run 结束）

            pump_task = asyncio.create_task(_pump(hub, relay))
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(relay.get(), timeout=_SSE_HEARTBEAT_SECONDS)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if event is None:
                        return
                    yield _sse(event)
            finally:
                pump_task.cancel()
                await asyncio.gather(pump_task, return_exceptions=True)

        status = await repo.get_run_status(run_id)
        if status is None:
            yield _sse(
                Event(
                    stage="ORCHESTRATOR",
                    type="error",
                    message="运行已被删除",
                    data={"status": "missing"},
                )
            )
            return

        if status in {"pending", "running"}:
            now = time.monotonic()
            if now >= next_heartbeat:
                yield ": keep-alive\n\n"
                next_heartbeat = now + _SSE_HEARTBEAT_SECONDS
            await asyncio.sleep(_REMOTE_STREAM_POLL_SECONDS)
            continue

        expected_type = "done" if status == "done" else "error"
        deadline = time.monotonic() + _REMOTE_STREAM_TERMINAL_GRACE_SECONDS
        events: list[Event] = []
        matching_terminal = False
        status_changed = False
        while True:
            # A resume transaction can move a previously terminal run back to
            # running while this subscriber is waiting. Never synthesize or
            # replay a terminal event from the stale status snapshot.
            if await repo.get_run_status(run_id) != status:
                status_changed = True
                break
            events = await repo.get_events(run_id)
            matching_terminal = any(
                event.stage == "ORCHESTRATOR" and event.type == expected_type for event in events
            )
            if matching_terminal or time.monotonic() >= deadline:
                if await repo.get_run_status(run_id) != status:
                    status_changed = True
                break
            await asyncio.sleep(_REMOTE_STREAM_POLL_SECONDS)

        if status_changed:
            continue
        for event in events:
            if (
                event.stage == "ORCHESTRATOR"
                and event.type in {"done", "error"}
                and event.type != expected_type
            ):
                continue
            yield _sse(event)
        if not matching_terminal:
            message = "运行已完成" if expected_type == "done" else "运行失败"
            yield _sse(
                Event(
                    stage="ORCHESTRATOR",
                    type=expected_type,
                    message=message,
                    data={"status": status},
                )
            )
        return


async def _build_search_tool(app: FastAPI, settings: Settings) -> SearchTool | None:
    """优先用搜索 key 池（主备故障转移）；池为空则回退到全局单 key TavilySearch。"""
    keys: list[str] = []
    try:
        keys = await app.state.catalog.active_keys()
    except Exception:
        logger.exception("读取搜索 key 池失败，回退单 key")
    if keys:
        from .tools.tavily_pool import TavilyKeyPoolSearch

        return TavilyKeyPoolSearch(keys)
    return None  # None＝交给 DeepResearchAgent 用 settings.tavily_api_key 自建


# --- Chaos 演示注入钩子（仅供演示/测试，见 scripts/chaos_demo.py） -----------------
# 设 DR_DEMO_FAKE_BACKENDS=1 后，_build_agent 改用仓库内 tests/fakes 的假 LLM/检索：
# 完全离线运行，并按 DR_DEMO_STEP_DELAY 秒放慢每次后端调用（让 kill -9 能精确落在
# 中间步骤）、按 DR_DEMO_TOKENS_PER_CALL 计入模拟 token（度量断点续跑节省占比）。
# 未设置该环境变量时本钩子完全不生效，生产/默认行为零变化。


def _demo_fake_backends_enabled() -> bool:
    return os.environ.get("DR_DEMO_FAKE_BACKENDS", "").strip().lower() in {"1", "true", "yes"}


def _build_demo_backends() -> tuple[Any, SearchTool]:
    """构造「放慢 + 计量」的离线假后端；仅在 DR_DEMO_FAKE_BACKENDS=1 时被调用。"""
    import importlib

    fakes = importlib.import_module("tests.fakes")  # 演示需从仓库根目录启动服务
    delay = float(os.environ.get("DR_DEMO_STEP_DELAY", "2.0") or 0.0)
    tokens_per_call = int(os.environ.get("DR_DEMO_TOKENS_PER_CALL", "1000") or 0)

    class _PacedFakeLLM:
        """包装 FakeLLM：每次调用先 sleep 模拟真实延迟，并向 tracer 计入模拟 token。"""

        def __init__(self) -> None:
            self._inner = fakes.FakeLLM()
            self.tracer: Any = None  # 由 _build_agent 在 agent 构造后回填

        async def _pace(self) -> None:
            if delay > 0:
                await asyncio.sleep(delay)
            if self.tracer is not None and tokens_per_call > 0:
                self.tracer.add_tokens(tokens_per_call)

        async def complete(self, system: str, user: str, *, temperature: float = 0.3) -> str:
            await self._pace()
            return await self._inner.complete(system, user, temperature=temperature)

        async def parse(
            self, system: str, user: str, schema: Any, *, temperature: float = 0.2, retries: int = 2
        ) -> Any:
            await self._pace()
            return await self._inner.parse(
                system, user, schema, temperature=temperature, retries=retries
            )

        async def stream(
            self, system: str, user: str, *, temperature: float = 0.4
        ) -> AsyncIterator[str]:
            await self._pace()
            async for piece in self._inner.stream(system, user, temperature=temperature):
                yield piece

        async def aclose(self) -> None:
            return None

    class _PacedFakeSearch(SearchTool):
        def __init__(self) -> None:
            self._inner = fakes.FakeSearch()

        async def search(self, query: str, *, max_results: int = 5) -> list[Any]:
            if delay > 0:
                await asyncio.sleep(delay)
            return await self._inner.search(query, max_results=max_results)

    return _PacedFakeLLM(), _PacedFakeSearch()


async def _build_agent(
    app: FastAPI, settings: Settings, **agent_kwargs: object
) -> tuple[DeepResearchAgent, SearchTool | None]:
    """统一 agent 构造：/api/runs 与 /api/research 共用，注入搜索 key 池与 catalog。

    注入的 search_tool 不归 agent 所有（aclose 不会关它），调用方须负责关闭。
    """
    if _demo_fake_backends_enabled():  # 仅供演示/测试：见上方钩子注释
        demo_llm, demo_search = _build_demo_backends()
        agent = DeepResearchAgent(
            settings,
            catalog_repo=getattr(app.state, "catalog", None),
            llm=demo_llm,
            search_tool=demo_search,
            **agent_kwargs,  # type: ignore[arg-type]  # 与下方既有 **agent_kwargs 模式一致
        )
        demo_llm.tracer = agent.tracer
        return agent, demo_search
    search_tool = await _build_search_tool(app, settings)
    try:
        agent = DeepResearchAgent(
            settings,
            catalog_repo=getattr(app.state, "catalog", None),
            search_tool=search_tool,
            **agent_kwargs,  # type: ignore[arg-type]
        )
    except BaseException:
        # 构造期异常（缺 key 等）时归还 search client，避免连接池泄漏
        if search_tool is not None:
            await search_tool.aclose()
        raise
    return agent, search_tool


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
) -> None:
    """后台执行一次研究：事件经 EventHub 实时扇出给 SSE 订阅者，全程落库。"""
    hub: EventHub = app.state.live.get(run_id)
    if hub is None:
        # A task must still release its lease and close subscribers if setup
        # raced with cancellation or a process-level state reset.
        hub = EventHub()
        app.state.live[run_id] = hub
    agent: DeepResearchAgent | None = None
    heartbeat: asyncio.Task[None] | None = None
    search_tool: SearchTool | None = None
    try:
        if lease_owner is not None:
            execution_task = asyncio.current_task()

            async def renew_lease() -> None:
                while True:
                    await asyncio.sleep(_LEASE_RENEW_INTERVAL_SECONDS)
                    try:
                        renewed = await app.state.repo.renew_lease(run_id, lease_owner)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("run %s lease renewal failed", run_id)
                        renewed = False
                    if renewed:
                        continue
                    hub.publish(
                        Event(
                            stage="ORCHESTRATOR",
                            type="error",
                            message="执行租约续期失败，任务已终止",
                        )
                    )
                    if execution_task is not None:
                        execution_task.cancel()
                    return

            heartbeat = asyncio.create_task(renew_lease())
        # 构造必须在 try 内：缺 API key 等构造期异常同样要走 finally 收尾，
        # 否则 EventHub 泄漏、SSE 订阅者永久挂起、run 卡死在 pending
        agent, search_tool = await _build_agent(
            app,
            settings,
            repo=app.state.repo,
            run_id=run_id,
            workflow=workflow,
            requested_workflow=requested_workflow,
            resume_execution=resume_execution,
            initial_execution=initial_execution,
            lease_owner=lease_owner,
        )
        if resume_execution is not None:
            # Seed the new tracer because save_events uses replacement semantics.
            historical_events = await app.state.repo.get_events(run_id)
            # A resumed attempt must not replay the previous attempt's terminal
            # marker: clients would abort the new stream before live events arrive.
            agent.tracer.events = [
                event
                for event in historical_events
                if not (event.stage == "ORCHESTRATOR" and event.type in {"done", "error"})
            ]
            for historical_event in agent.tracer.events:
                hub.publish(historical_event)
        agent.tracer.add_sink(hub.publish)
        await agent.run(query)
    except asyncio.CancelledError:
        # Cancellation also represents an orderly process shutdown or lease
        # fencing.  Keep the durable run active so another instance can resume
        # its checkpoint; orphan recovery will mark checkpoint-less runs error.
        raise
    except Exception:
        # run() 内部正常路径已 emit error 事件并置 status=error；
        # 落到这里的是构造期异常或落库自身失败，必须留痕并兜底状态
        logger.exception("run %s 执行失败", run_id)
        hub.publish(Event(stage="ORCHESTRATOR", type="error", message="服务器内部错误，运行已终止"))
        try:
            await app.state.repo.set_status(run_id, "error", lease_owner=lease_owner)
        except Exception:
            logger.exception("run %s 兜底置 error 状态失败", run_id)
    finally:

        async def cleanup_resources() -> None:
            if heartbeat is not None:
                heartbeat.cancel()
                try:
                    await asyncio.gather(heartbeat, return_exceptions=True)
                except BaseException:
                    logger.exception("run %s lease heartbeat cleanup failed", run_id)
            if lease_owner is not None:
                try:
                    await app.state.repo.release_lease(run_id, lease_owner)
                except BaseException:
                    logger.exception("run %s release lease failed", run_id)
            if agent is not None:
                try:
                    await agent.aclose()
                except BaseException:
                    logger.exception("run %s 释放 LLM client 失败", run_id)
            if search_tool is not None:
                try:
                    await search_tool.aclose()
                except BaseException:
                    logger.exception("run %s 释放搜索 client 失败", run_id)

        # A second task.cancel() must not interrupt resource cleanup. Shielding
        # an independent task lets this task retain cancellation semantics while
        # cleanup runs to completion.
        cleanup_task = asyncio.create_task(cleanup_resources())
        cleanup_interrupted = False
        try:
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError:
                    cleanup_interrupted = True
            cleanup_task.result()
        except BaseException:
            logger.exception("run %s resource cleanup failed", run_id)
        finally:
            try:
                hub.close()  # notify every online SSE subscriber
            finally:
                app.state.live.pop(run_id, None)
        if cleanup_interrupted:
            raise asyncio.CancelledError


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
    return {"status": "ok"}


@app.post("/api/runs", status_code=202, dependencies=[Depends(require_api_key)])
async def create_run(req: CreateRunRequest, request: Request) -> CreateRunResponse:
    _check_rate_limit(request)
    repo: ResearchRepository = request.app.state.repo
    settings = _settings_for(request.app.state.settings, req.params)
    lease_owner = uuid4().hex

    # 意图预路由必须在 create_initial_execution 之前：工作流定义会被写进初始
    # checkpoint 且崩溃恢复直接读它，等流程跑起来再路由就改不动执行路径了。
    # 只跑规则 + 本地模型（enable_llm=False）——这里在 HTTP 同步段上，
    # 升级到 LLM 会把创建研究的响应从毫秒级拉到秒级。
    from .workflows import WORKFLOWS, get_workflow

    workflow_name, intent_decision, route = await preroute_workflow(
        req.query,
        requested_workflow=req.workflow,
        available_workflows=set(WORKFLOWS),
        enabled=settings.intent_enabled,
    )
    execution = create_initial_execution(req.query, workflow_name, settings)
    if intent_decision is not None:
        # 把判定写进初始 checkpoint：流程内的 IntentRouter 复用它而不重判，
        # 恢复后的运行也拿到与首次完全一致的意图结论。
        scratch = execution.checkpoint.setdefault("scratch", {})
        if isinstance(scratch, dict):
            scratch[INTENT_SCRATCH_KEY] = intent_decision.model_dump(mode="json")
            if route.applied and route.max_sub_questions is not None:
                # 子问题预算也必须在这里落盘。intent_router 只被编排进 guarded
                # 流程，而路由的结果通常是 deep/quick/teams——那些流程里没有这个
                # 角色，预算若只由角色写入就永远到不了 Planner。预算只能收紧：
                # 与用户配置取 min，绝不放宽。
                scratch[INTENT_SUB_QUESTION_KEY] = min(
                    route.max_sub_questions, settings.max_sub_questions
                )
            scratch[INTENT_ROUTE_KEY] = {
                "applied": route.applied,
                "workflow": route.workflow,
                "max_sub_questions": route.max_sub_questions,
                "reason": route.reason,
            }
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
    run_id = await repo.create_run(req.query, execution=execution, lease_owner=lease_owner)
    request.app.state.live[run_id] = EventHub()
    task = asyncio.create_task(
        _execute(
            request.app,
            run_id,
            req.query,
            settings,
            workflow_name,
            None,
            lease_owner,
            execution,
            # 用户原始选择：与 workflow_name（可能已被意图预路由改写）区分，
            # 让 IntentRouter 只在用户真没指定时才路由。
            requested_workflow=req.workflow,
        )
    )
    request.app.state.tasks.add(task)
    task.add_done_callback(request.app.state.tasks.discard)
    return CreateRunResponse(run_id=run_id)


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
    if detail.status == "done":
        raise HTTPException(status_code=409, detail="completed run cannot be resumed")
    # A fresh token identifies this execution attempt, so concurrent resume
    # requests cannot renew and share the same lease.
    owner = uuid4().hex
    if not await request.app.state.repo.acquire_lease(run_id, owner):
        raise HTTPException(status_code=409, detail="run is leased by another instance")
    handed_off = False
    try:
        # Re-read after acquiring the lease. The first snapshot may have been
        # stale while the previous worker was finishing or writing a checkpoint.
        detail = await request.app.state.repo.get_run(run_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="run not found")
        execution = detail.orchestration
        if execution is None or not execution.checkpoint:
            raise HTTPException(status_code=409, detail="run has no recoverable checkpoint")
        if detail.status == "done":
            raise HTTPException(status_code=409, detail="completed run cannot be resumed")
        resume_settings = _settings_for_resume(request.app.state.settings, execution)
        # Publish the new attempt before returning 202 so clients cannot keep
        # treating the previous attempt's terminal status as authoritative.
        await request.app.state.repo.prepare_resume(run_id, lease_owner=owner)
        request.app.state.live[run_id] = EventHub()
        try:
            task = asyncio.create_task(
                _execute(
                    request.app,
                    run_id,
                    detail.query,
                    resume_settings,
                    execution.workflow_name,
                    execution,
                    owner,
                )
            )
        except BaseException:
            request.app.state.live.pop(run_id, None)
            raise
        handed_off = True
        request.app.state.tasks.add(task)
        task.add_done_callback(request.app.state.tasks.discard)
        return CreateRunResponse(run_id=run_id)
    finally:
        if not handed_off:
            request.app.state.live.pop(run_id, None)
            try:
                await request.app.state.repo.release_lease(run_id, owner)
            except Exception:
                logger.exception("failed to release resume lease for %s", run_id)


@app.get("/api/workflows", dependencies=[Depends(require_api_key)])
async def list_workflows(request: Request) -> list[dict[str, str]]:
    """可选的任务流程列表（供前端选择器）：内置预置 + 已启用的自定义工作流。"""
    from .workflows import DEFAULT_WORKFLOW, WORKFLOWS

    out = [
        {
            "name": wf.name,
            "description": wf.description,
            "default": str(wf.name == DEFAULT_WORKFLOW),
            "custom": "False",
        }
        for wf in WORKFLOWS.values()
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
    """更新并持久化全局配置；对后续创建的 run 生效。"""
    overrides = _sanitized_overrides(runtime_config.load_overrides())
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
        view = _config_view(new_settings)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=422, detail=f"配置非法：{e}") from e
    request.app.state.settings = new_settings
    runtime_config.save_overrides(overrides)
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
        if detail.status in {"pending", "running"}:
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
    detail.events = await repo.get_events(detail.id)
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
    require_corroboration = bool(
        detail.manifest and detail.manifest.settings.get("require_corroboration", False)
    )
    detail.metrics = quality_metrics(detail, require_corroboration=require_corroboration)
    return detail


@app.get("/api/runs/{run_id}", dependencies=[Depends(require_api_key)])
async def get_run(run_id: str, request: Request) -> RunDetail:
    repo: ResearchRepository = request.app.state.repo
    detail = await repo.get_run(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="run not found")
    return await _enrich_run_detail(repo, detail)


@app.get("/api/runs/{run_id}/events", dependencies=[Depends(require_api_key)])
async def get_events(run_id: str, request: Request, after_seq: int = Query(0, ge=0)) -> list[Event]:
    """事件回放。after_seq 为「跳过前 N 条」的偏移语义（客户端传已收到的条数）。"""
    repo: ResearchRepository = request.app.state.repo
    # 与 /stream 一致：未知 run 返回 404，而非 200 + 空列表（区分「不存在」与「无事件」）
    if request.app.state.live.get(run_id) is None and await repo.get_run_status(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    return await repo.get_events(run_id, after_seq=after_seq)


@app.get("/api/runs/{run_id}/stream", dependencies=[Depends(require_api_key)])
async def stream_run(run_id: str, request: Request) -> StreamingResponse:
    app_ = request.app
    # 未知 run 返回 404，而非 200 + 空流（让客户端能区分「不存在」与「无事件」）
    if app_.state.live.get(run_id) is None and await app_.state.repo.get_run_status(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")

    async def gen() -> AsyncIterator[str]:
        async for chunk in _stream_run_sse(app_, run_id):
            yield chunk

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/research", dependencies=[Depends(require_api_key)])
async def research(
    request: Request,
    q: str = Query(..., description="研究问题", min_length=1, max_length=2000),
) -> StreamingResponse:
    """无持久化的即跑即看快路径（向后兼容旧前端）。"""
    _check_rate_limit(request)
    # 与 /api/runs 同一构造路径：搜索 key 池 / catalog 注入行为保持一致
    agent, search_tool = await _build_agent(request.app, request.app.state.settings)

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event in agent.run_stream(q):
                yield _sse(event)
        except Exception:  # 兜底：异常详情只进日志，不下发内部信息（base_url/路径等）
            logger.exception("即时研究流执行失败")
            payload = {"stage": "ORCHESTRATOR", "type": "error", "message": "服务器内部错误"}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        finally:
            try:
                await agent.aclose()
            finally:
                if search_tool is not None:  # 注入的 search client 归本端点关闭
                    await search_tool.aclose()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/{full_path:path}", response_class=HTMLResponse)
async def spa_fallback(full_path: str) -> str:
    """SPA history 路由回退：非 /api 路径一律返回前端入口，支持深链接刷新。

    具体路由（/、/healthz、/api/*）按声明顺序优先匹配；此 catch-all 只接管
    其余未注册的 GET 路径（如 /history、/runs/{id}）。
    """
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="not found")
    return await index()
