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
import secrets
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import runtime_config
from .catalog.repository import CatalogRepository
from .config import Settings
from .observability import Event, EventHub
from .orchestration import WorkflowRun
from .orchestrator import DeepResearchAgent
from .persistence.db import create_all, make_engine, make_sessionmaker
from .persistence.repository import ResearchRepository, RunDetail, RunSummary, TagCount
from .persistence.sql_repository import SqlRepository

logger = logging.getLogger(__name__)


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
    request_timeout: float | None = Field(default=None, gt=0, le=600)


def _settings_for(base: Settings, params: ResearchParams | None) -> Settings:
    """把 per-run 覆盖合并进基础 Settings（仅覆盖显式提供的字段）。"""
    if params is None:
        return base
    overrides = {k: v for k, v in params.model_dump().items() if v is not None}
    return replace(base, **overrides) if overrides else base


def _mask_secret(secret: str) -> str:
    """密钥脱敏：只露尾 4 位（不足 4 位整体视为短密钥，仍只露尾部）。"""
    if not secret:
        return ""
    tail = secret[-4:] if len(secret) >= 4 else secret
    return f"…{tail}"


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
        return True


_run_limiter = _RateLimiter(max_calls=10, window_seconds=60.0)


def _check_rate_limit(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    if not _run_limiter.check(client_ip):
        raise HTTPException(status_code=429, detail="too many requests, slow down")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    # 叠加前端持久化的全局配置（runtime_config.json）；损坏/非法不阻塞启动
    overrides = runtime_config.load_overrides()
    if overrides:
        try:
            settings = runtime_config.apply_overrides(settings, overrides)
        except Exception:
            logger.exception("应用持久化配置失败，回退到环境变量配置")
    engine = make_engine(settings.database_url)
    # SQLite（本地/测试）自动建表；PostgreSQL（生产）由 alembic upgrade head 负责
    if settings.database_url.startswith("sqlite"):
        await create_all(engine)
    app.state.settings = settings
    app.state.engine = engine
    app.state.repo = SqlRepository(make_sessionmaker(engine))
    app.state.catalog = CatalogRepository(make_sessionmaker(engine))  # 角色广场 catalog 仓储
    app.state.live = {}  # run_id -> EventHub：进行中 run 的实时事件中枢（向多端 SSE 扇出）
    app.state.tasks = set()  # 持有后台任务引用，避免被 GC 提前回收
    app.state.instance_id = str(uuid4())
    # 自动恢复上次进程中断且已有 checkpoint 的任务；无 checkpoint 的孤儿任务置 error。
    try:
        orphaned = [
            *await app.state.repo.list_runs(status="pending", limit=1000),
            *await app.state.repo.list_runs(status="running", limit=1000),
        ]
        for summary in orphaned:
            detail = await app.state.repo.get_run(summary.id)
            execution = detail.orchestration if detail is not None else None
            if detail is None or execution is None or not execution.checkpoint:
                await app.state.repo.set_status(summary.id, "error")
                continue
            if not await app.state.repo.acquire_lease(summary.id, app.state.instance_id):
                continue
            app.state.live[summary.id] = EventHub()
            task = asyncio.create_task(
                _execute(
                    app,
                    summary.id,
                    detail.query,
                    settings,
                    execution.workflow_name,
                    execution,
                    app.state.instance_id,
                )
            )
            app.state.tasks.add(task)
            task.add_done_callback(app.state.tasks.discard)
    except Exception:
        logger.exception("启动恢复未完成任务失败（不阻塞启动）")
    try:
        yield
    finally:
        # 先取消并回收后台研究任务，再释放引擎——避免任务在 dispose 后继续发 SQL
        tasks = list(app.state.tasks)
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


async def _build_search_tool(app: FastAPI, settings: Settings):  # type: ignore[no-untyped-def]
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


async def _execute(
    app: FastAPI,
    run_id: str,
    query: str,
    settings: Settings,
    workflow: str | None = None,
    resume_execution: WorkflowRun | None = None,
    lease_owner: str | None = None,
) -> None:
    """后台执行一次研究：事件经 EventHub 实时扇出给 SSE 订阅者，全程落库。"""
    hub: EventHub = app.state.live[run_id]
    agent: DeepResearchAgent | None = None
    heartbeat: asyncio.Task[None] | None = None
    try:
        if lease_owner is not None:

            async def renew_lease() -> None:
                while True:
                    await asyncio.sleep(60)
                    await app.state.repo.acquire_lease(run_id, lease_owner)

            heartbeat = asyncio.create_task(renew_lease())
        # 构造必须在 try 内：缺 API key 等构造期异常同样要走 finally 收尾，
        # 否则 EventHub 泄漏、SSE 订阅者永久挂起、run 卡死在 pending
        search_tool = await _build_search_tool(app, settings)
        agent = DeepResearchAgent(
            settings,
            repo=app.state.repo,
            run_id=run_id,
            workflow=workflow,
            catalog_repo=app.state.catalog,
            search_tool=search_tool,
            resume_execution=resume_execution,
        )
        if resume_execution is not None:
            # Seed the new tracer because save_events uses replacement semantics.
            agent.tracer.events = await app.state.repo.get_events(run_id)
            for historical_event in agent.tracer.events:
                hub.publish(historical_event)
        agent.tracer.add_sink(hub.publish)
        await agent.run(query)
    except asyncio.CancelledError:
        # 服务关停：尽力把状态落库后继续传播取消
        try:
            await app.state.repo.set_status(run_id, "error")
        except Exception:
            logger.exception("run %s 取消后落库状态失败", run_id)
        raise
    except Exception:
        # run() 内部正常路径已 emit error 事件并置 status=error；
        # 落到这里的是构造期异常或落库自身失败，必须留痕并兜底状态
        logger.exception("run %s 执行失败", run_id)
        hub.publish(Event(stage="ORCHESTRATOR", type="error", message="服务器内部错误，运行已终止"))
        try:
            await app.state.repo.set_status(run_id, "error")
        except Exception:
            logger.exception("run %s 兜底置 error 状态失败", run_id)
    finally:
        if heartbeat is not None:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        if lease_owner is not None:
            await app.state.repo.release_lease(run_id, lease_owner)
        if agent is not None:
            try:
                await agent.aclose()
            except Exception:
                logger.exception("run %s 释放 LLM client 失败", run_id)
        hub.close()  # 通知所有在线 SSE 订阅者收尾
        app.state.live.pop(run_id, None)


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
    run_id = await repo.create_run(req.query)
    request.app.state.live[run_id] = EventHub()
    settings = _settings_for(request.app.state.settings, req.params)
    task = asyncio.create_task(_execute(request.app, run_id, req.query, settings, req.workflow))
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
    if execution is None or not execution.checkpoint or not execution.definition:
        raise HTTPException(status_code=409, detail="run has no recoverable checkpoint")
    if detail.status == "done":
        raise HTTPException(status_code=409, detail="completed run cannot be resumed")
    owner = getattr(request.app.state, "instance_id", "test-instance")
    if not await request.app.state.repo.acquire_lease(run_id, owner):
        raise HTTPException(status_code=409, detail="run is leased by another instance")
    request.app.state.live[run_id] = EventHub()
    task = asyncio.create_task(
        _execute(
            request.app,
            run_id,
            detail.query,
            request.app.state.settings,
            execution.workflow_name,
            execution,
            owner,
        )
    )
    request.app.state.tasks.add(task)
    task.add_done_callback(request.app.state.tasks.discard)
    return CreateRunResponse(run_id=run_id)


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


# 可编排进自定义工作流的内置角色（排除 coordinator/aggregator——它们是 compose/fanout 专用原语）
_COMPOSABLE_BUILTINS = {"planner", "researcher", "reflector", "synthesizer", "critic"}


@app.get("/api/roles", dependencies=[Depends(require_api_key)])
async def list_roles(request: Request) -> list[dict[str, object]]:
    """可编排角色（供构建器角色选择器）：内置可组合角色 + 已启用自定义卡片。"""
    from .registry import available

    builtin = {n for n in available() if n in _COMPOSABLE_BUILTINS}
    roles: list[dict[str, object]] = [
        {"name": n, "label": n, "icon": "◆", "builtin": True} for n in sorted(builtin)
    ]
    try:
        for card in await request.app.state.catalog.list_agents():
            if card.enabled and card.name not in builtin:
                roles.append(
                    {
                        "name": card.name,
                        "label": card.display_name or card.name,
                        "icon": card.icon,
                        "builtin": False,
                    }
                )
    except Exception:
        logger.exception("读取自定义角色卡片失败，仅返回内置角色")
    return roles


@app.get("/api/config", dependencies=[Depends(require_api_key)])
async def get_config(request: Request) -> ConfigView:
    """当前全局配置（密钥脱敏）。"""
    return _config_view(request.app.state.settings)


@app.put("/api/config", dependencies=[Depends(require_api_key)])
async def update_config(req: ConfigUpdate, request: Request) -> ConfigView:
    """更新并持久化全局配置；对后续创建的 run 生效。"""
    overrides = runtime_config.load_overrides()
    payload = req.model_dump(exclude_unset=True)
    for key, value in payload.items():
        if key in runtime_config.SECRET_FIELDS:
            if value:  # 空＝保持不变（不被脱敏表单覆盖清空）
                overrides[key] = value
        elif key == "llm_base_url":
            overrides[key] = value or None  # 显式空串＝清空回默认端点
        else:
            overrides[key] = value
    # 以「环境变量 + 新覆盖」重建，复用 Settings.__post_init__ 范围校验
    try:
        new_settings = runtime_config.apply_overrides(Settings(), overrides)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=422, detail=f"配置非法：{e}") from e
    request.app.state.settings = new_settings
    runtime_config.save_overrides(overrides)
    return _config_view(new_settings)


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


@app.delete("/api/runs/{run_id}", status_code=204, dependencies=[Depends(require_api_key)])
async def delete_run(run_id: str, request: Request) -> Response:
    app_ = request.app
    if app_.state.live.get(run_id) is not None:
        raise HTTPException(status_code=409, detail="运行进行中，无法删除")
    deleted = await app_.state.repo.delete_run(run_id)
    if not deleted:
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
        if await app_.state.repo.delete_run(run_id):
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
    return detail


@app.get("/api/runs/{run_id}", dependencies=[Depends(require_api_key)])
async def get_run(run_id: str, request: Request) -> RunDetail:
    repo: ResearchRepository = request.app.state.repo
    detail = await repo.get_run(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="run not found")
    return detail


@app.get("/api/runs/{run_id}/events", dependencies=[Depends(require_api_key)])
async def get_events(run_id: str, request: Request, after_seq: int = Query(0, ge=0)) -> list[Event]:
    """事件回放。after_seq 为「跳过前 N 条」的偏移语义（客户端传已收到的条数）。"""
    repo: ResearchRepository = request.app.state.repo
    return await repo.get_events(run_id, after_seq=after_seq)


@app.get("/api/runs/{run_id}/stream", dependencies=[Depends(require_api_key)])
async def stream_run(run_id: str, request: Request) -> StreamingResponse:
    app_ = request.app
    # 未知 run 返回 404，而非 200 + 空流（让客户端能区分「不存在」与「无事件」）
    if app_.state.live.get(run_id) is None and await app_.state.repo.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")

    async def gen() -> AsyncIterator[str]:
        hub: EventHub | None = app_.state.live.get(run_id)
        if hub is not None:
            # 进行中：从 EventHub 订阅（回放已发生事件 + 续收实时事件），支持多端同时观看
            async for event in hub.stream():
                yield _sse(event)
        else:
            # 已结束（或未知）：从 DB 按 seq 回放
            for event in await app_.state.repo.get_events(run_id):
                yield _sse(event)

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
    agent = DeepResearchAgent(Settings())

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event in agent.run_stream(q):
                yield _sse(event)
        except Exception:  # 兜底：异常详情只进日志，不下发内部信息（base_url/路径等）
            logger.exception("即时研究流执行失败")
            payload = {"stage": "ORCHESTRATOR", "type": "error", "message": "服务器内部错误"}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        finally:
            await agent.aclose()

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
