"""角色广场 catalog 的 REST API：模型档案 / 角色卡片 / 搜索 key 的 CRUD。

挂载在 /api 下，复用主 app 的 require_api_key 鉴权与 app.state.catalog 仓储。
密钥一律脱敏回显、空值不覆盖（与全局配置同语义）。
"""

from __future__ import annotations

import asyncio
import time
from typing import NoReturn

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError

from .catalog.dto import (
    BEHAVIORS,
    AgentCardCreate,
    AgentCardUpdate,
    AgentCardView,
    ModelProfileFull,
    ModelProfileView,
    SearchKeyView,
    WorkflowDefCreate,
    WorkflowDefUpdate,
    WorkflowDefView,
)
from .catalog.repository import CatalogRepository, WorkflowVersionConflictError
from .config import Settings
from .observability import Tracer
from .security import (
    ProviderURLPolicyError,
    provider_http_client,
    validate_provider_url_resolved,
)

_PROBE_TIMEOUT_SECONDS = 20.0


class _ProbeLimiter:
    """Bound upstream connectivity probes per direct client address."""

    _MAX_KEYS = 10_000

    def __init__(self, max_calls: int = 10, window_seconds: float = 60.0) -> None:
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = {}

    def check(self, key: str) -> bool:
        now = time.monotonic()
        if len(self._hits) >= self._MAX_KEYS and key not in self._hits:
            self._hits = {
                stored_key: stamps
                for stored_key, stamps in self._hits.items()
                if any(now - stamp < self.window_seconds for stamp in stamps)
            }
            if len(self._hits) >= self._MAX_KEYS:
                return False
        hits = [stamp for stamp in self._hits.get(key, []) if now - stamp < self.window_seconds]
        if len(hits) >= self.max_calls:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True


_probe_limiter = _ProbeLimiter()


def _check_probe_limit(request: Request) -> None:
    key = request.client.host if request.client else "unknown"
    if not _probe_limiter.check(key):
        raise HTTPException(
            status_code=429,
            detail="too many upstream probes; retry later",
            headers={"Retry-After": "5"},
        )


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    base_url: str | None = Field(None, max_length=500)
    api_key: str = Field("", max_length=500)
    model: str = Field("gpt-4o-mini", min_length=1, max_length=100)
    temperature: float = Field(0.3, ge=0.0, le=2.0)
    parameter_mode: str = Field("temperature", pattern="^(temperature|reasoning)$")
    reasoning_effort: str = Field("medium", pattern="^(low|medium|high)$")
    is_default: bool = False


class ProfileUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    base_url: str | None = Field(None, max_length=500)
    api_key: str | None = Field(None, max_length=500)  # 空/省略＝不改
    model: str | None = Field(None, min_length=1, max_length=100)
    temperature: float | None = Field(None, ge=0.0, le=2.0)
    parameter_mode: str | None = Field(None, pattern="^(temperature|reasoning)$")
    reasoning_effort: str | None = Field(None, pattern="^(low|medium|high)$")
    is_default: bool | None = None

    @field_validator(
        "name",
        "model",
        "temperature",
        "parameter_mode",
        "reasoning_effort",
        "is_default",
        mode="before",
    )
    @classmethod
    def reject_null_non_nullable_fields(cls, value: object) -> object:
        if value is None:
            raise ValueError("field cannot be null; omit it to keep the current value")
        return value


class KeyCreate(BaseModel):
    label: str = Field("", max_length=64)
    api_key: str = Field(min_length=1, max_length=500)
    priority: int = Field(0, ge=0, le=1000)
    enabled: bool = True


class KeyUpdate(BaseModel):
    label: str | None = Field(None, max_length=64)
    api_key: str | None = Field(None, max_length=500)
    priority: int | None = Field(None, ge=0, le=1000)
    enabled: bool | None = None

    @field_validator("label", "priority", "enabled", mode="before")
    @classmethod
    def reject_null_non_nullable_fields(cls, value: object) -> object:
        if value is None:
            raise ValueError("field cannot be null; omit it to keep the current value")
        return value


class TestResult(BaseModel):
    """「测试连接」结果：ok=是否可用、latency_ms=往返耗时、detail=失败原因。"""

    ok: bool
    latency_ms: int
    detail: str = ""


class ProfileProbe(BaseModel):
    profile_id: str | None = None
    base_url: str | None = Field(None, max_length=500)
    api_key: str = Field("", max_length=500)
    model: str = Field("gpt-4o-mini", max_length=100)
    parameter_mode: str | None = Field(None, pattern="^(temperature|reasoning)$")
    reasoning_effort: str | None = Field(None, pattern="^(low|medium|high)$")


class ModelDiscoveryResult(BaseModel):
    models: list[str]
    latency_ms: int


# ── 连通性探针（网络调用隔离到此，便于单测 monkeypatch）────────────────────
async def _probe_llm(profile: ModelProfileFull, settings: Settings) -> None:
    """用档案参数建一次性 LLM，发最小补全验证端点/key 可用；失败抛异常。"""
    from .llm import LLM

    await validate_provider_url_resolved(
        profile.base_url,
        allow_private=settings.allow_private_provider_urls,
        allowlist=settings.provider_host_allowlist,
    )
    llm = LLM.from_params(
        Tracer(),
        api_key=profile.api_key,
        base_url=profile.base_url,
        model=profile.model,
        timeout=settings.request_timeout,
        user_agent=settings.llm_user_agent,
        temperature=0.0,
        parameter_mode=profile.parameter_mode,
        reasoning_effort=profile.reasoning_effort,
        allow_private_provider_urls=settings.allow_private_provider_urls,
    )
    try:
        await llm.complete("connectivity test", "ping", temperature=0.0)
    finally:
        await llm.aclose()


async def _probe_search(api_key: str) -> None:
    """用单个 key 发一次最小检索验证可用;失败抛异常。"""
    from tavily import AsyncTavilyClient

    client = AsyncTavilyClient(api_key=api_key)
    try:
        async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
            await client.search("ping", max_results=1)
    finally:
        await client.close()


def _exception_detail(exc: BaseException) -> str:
    """Return a bounded exception chain so connection failures remain diagnosable."""
    parts: list[str] = []
    current: BaseException | None = exc
    while current is not None and len(parts) < 4:
        message = str(current).strip() or current.__class__.__name__
        item = f"{current.__class__.__name__}: {message}"
        if item not in parts:
            parts.append(item)
        current = current.__cause__ or current.__context__
    return " <- ".join(parts)[:600]


async def _resolve_probe(req: ProfileProbe, catalog: CatalogRepository) -> ModelProfileFull:
    stored = await catalog.get_profile_full(req.profile_id) if req.profile_id else None
    return ModelProfileFull(
        id=req.profile_id or "probe",
        name=stored.name if stored else "probe",
        base_url=(
            req.base_url if req.base_url is not None else (stored.base_url if stored else None)
        ),
        api_key=req.api_key or (stored.api_key if stored else ""),
        model=req.model or (stored.model if stored else "gpt-4o-mini"),
        temperature=stored.temperature if stored else 0.0,
        parameter_mode=req.parameter_mode or (stored.parameter_mode if stored else "temperature"),
        reasoning_effort=req.reasoning_effort or (stored.reasoning_effort if stored else "medium"),
    )


async def _run_probe(coro) -> TestResult:  # type: ignore[no-untyped-def]
    """统一计时 + 异常归一:成功 ok=true,异常 ok=false 并截断 detail。"""
    start = time.perf_counter()
    try:
        await coro
    except Exception as e:  # noqa: BLE001 —— 任何失败都归一为不可用,不向上抛
        elapsed = int((time.perf_counter() - start) * 1000)
        return TestResult(ok=False, latency_ms=elapsed, detail=str(e)[:300])
    elapsed = int((time.perf_counter() - start) * 1000)
    return TestResult(ok=True, latency_ms=elapsed)


def _catalog(request: Request) -> CatalogRepository:
    return request.app.state.catalog


async def _validate_profile_url(base_url: str | None, settings: Settings) -> None:
    try:
        await validate_provider_url_resolved(
            base_url,
            allow_private=settings.allow_private_provider_urls,
            allowlist=settings.provider_host_allowlist,
        )
    except ProviderURLPolicyError as exc:
        raise HTTPException(status_code=422, detail=f"base_url 无效：{exc}") from exc


def _raise_for_integrity(exc: IntegrityError, *, duplicate_detail: str, fk_detail: str) -> NoReturn:
    """把数据库完整性错误翻译为语义化 HTTP 状态：唯一约束 → 409，外键缺失 → 422。

    仓储写方法的 ``async with s.begin()`` 在异常时已回滚并关闭会话，
    此处只做错误翻译，无残留事务状态需要处理。无法识别的完整性错误原样上抛。
    """
    message = str(getattr(exc, "orig", None) or exc).lower()
    if "unique" in message or "duplicate" in message:
        raise HTTPException(status_code=409, detail=duplicate_detail) from exc
    if "foreign key" in message:
        raise HTTPException(status_code=422, detail=fk_detail) from exc
    raise exc


_PROFILE_DUPLICATE = "同名模型档案已存在，请改名"
_AGENT_DUPLICATE = "同名角色卡片已存在，请改名"
_AGENT_BAD_PROFILE = "绑定的模型档案不存在（model_profile_id 无效）"
_WORKFLOW_DUPLICATE = "同名自定义工作流已存在，请改名"


def _reject_reserved_agent_name(name: str) -> None:
    """角色名 "orchestrator"（大小写不敏感）为系统保留：与运行终态事件的保留 Stage 冲突。"""
    if name.strip().lower() == "orchestrator":
        raise HTTPException(
            status_code=422,
            detail=f"名称「{name}」为系统保留（与运行终态事件的 ORCHESTRATOR Stage 冲突），请改名",
        )


def _reject_builtin_name(name: str) -> None:
    """自定义流程名不得与内置流程同名（否则运行解析时被内置优先级永久屏蔽）。"""
    from .workflows import WORKFLOWS

    if name in WORKFLOWS:
        raise HTTPException(status_code=422, detail=f"名称「{name}」与内置流程冲突，请改名")


async def _validate_custom_steps(
    steps: list[dict],
    catalog: CatalogRepository,
    settings: Settings,
    *,
    nodes: list[dict] | None = None,
    edges: list[dict] | None = None,
) -> None:
    """校验自定义工作流的 steps 是否安全可执行（复用引擎的 validate_workflow）；不合法抛 422。"""
    from .catalog.runtime import terminal_roles_for_cards
    from .registry import available
    from .workflow import Step, validate_workflow, validate_workflow_graph_terminal

    enabled_cards = [c for c in await catalog.list_agents() if c.enabled]
    # 可编排角色 = 内置注册角色 + 已启用自定义卡片名（运行期 resolve_agent 能解析这两类）
    available_roles = set(available()) | {c.name for c in enabled_cards}
    # 终端角色按最终生效的角色卡片行为计算，避免同名覆盖造成误判。
    terminal_roles = terminal_roles_for_cards(enabled_cards)
    try:
        parsed = [Step.model_validate(s) for s in steps]
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"步骤格式非法：{e}") from e
    errors = validate_workflow(
        parsed,
        available_roles,
        max_rounds_cap=settings.max_rounds,
        terminal_roles=terminal_roles,
        require_terminal_last=nodes is None,
    )
    if nodes is not None and edges is not None:
        errors.extend(
            validate_workflow_graph_terminal(
                nodes,
                edges,
                terminal_roles=terminal_roles,
            )
        )
    if errors:
        raise HTTPException(status_code=422, detail="工作流不合法：" + "；".join(errors))


def _normalize_graph_payload(
    payload: WorkflowDefCreate | WorkflowDefUpdate,
    *,
    existing: WorkflowDefView | None = None,
) -> list[dict] | None:
    """Keep legacy steps and the graph representation synchronized during migration."""
    from .orchestration import (
        WorkflowEdge,
        WorkflowNode,
        WorkflowViewport,
        evaluate_condition,
        graph_topological_steps,
        steps_to_graph,
    )

    fields = payload.model_fields_set
    try:
        if fields & {"nodes", "edges"}:
            existing_nodes = existing.nodes if existing is not None else []
            existing_edges = existing.edges if existing is not None else []
            if existing is not None and not existing_nodes and existing.steps:
                legacy_nodes, legacy_edges = steps_to_graph(existing.steps)
                existing_nodes = [node.model_dump() for node in legacy_nodes]
                existing_edges = [edge.model_dump() for edge in legacy_edges]
            raw_nodes = payload.nodes if "nodes" in fields else existing_nodes
            raw_edges = payload.edges if "edges" in fields else existing_edges
            nodes = [WorkflowNode.model_validate(node) for node in (raw_nodes or [])]
            edges = [WorkflowEdge.model_validate(edge) for edge in (raw_edges or [])]
            for edge in edges:
                evaluate_condition(edge.condition, {})
            steps = graph_topological_steps(nodes, edges)
            payload.steps = steps
            payload.nodes = [node.model_dump() for node in nodes]
            payload.edges = [edge.model_dump() for edge in edges]
            raw_viewport = (
                payload.viewport
                if "viewport" in fields
                else (existing.viewport if existing is not None else {})
            )
            payload.viewport = WorkflowViewport.model_validate(raw_viewport or {}).model_dump()
            return steps
        if "steps" in fields or isinstance(payload, WorkflowDefCreate):
            steps = payload.steps or []
            nodes, edges = steps_to_graph(steps)
            payload.nodes = [node.model_dump() for node in nodes]
            payload.edges = [edge.model_dump() for edge in edges]
            raw_viewport = (
                payload.viewport
                if "viewport" in fields
                else (existing.viewport if existing is not None else {})
            )
            payload.viewport = WorkflowViewport.model_validate(raw_viewport or {}).model_dump()
            return steps
        if "viewport" in fields:
            payload.viewport = WorkflowViewport.model_validate(payload.viewport or {}).model_dump()
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"工作流图非法：{exc}") from exc
    return None


router = APIRouter(prefix="/api")


# ── 元信息 ──────────────────────────────────────────────────────────────
@router.get("/behaviors")
async def list_behaviors() -> list[str]:
    """可选的角色行为模板（新建角色卡片时从中选一）。"""
    return list(BEHAVIORS)


# ── 模型档案 ────────────────────────────────────────────────────────────
@router.get("/models")
async def list_models(request: Request) -> list[ModelProfileView]:
    return await _catalog(request).list_profiles()


@router.post("/models", status_code=201)
async def create_model(req: ProfileCreate, request: Request) -> ModelProfileView:
    await _validate_profile_url(req.base_url, request.app.state.settings)
    try:
        return await _catalog(request).create_profile(
            name=req.name,
            base_url=req.base_url,
            api_key=req.api_key,
            model=req.model,
            temperature=req.temperature,
            parameter_mode=req.parameter_mode,
            reasoning_effort=req.reasoning_effort,
            is_default=req.is_default,
        )
    except IntegrityError as exc:
        _raise_for_integrity(exc, duplicate_detail=_PROFILE_DUPLICATE, fk_detail=_AGENT_BAD_PROFILE)


@router.put("/models/{profile_id}")
async def update_model(profile_id: str, req: ProfileUpdate, request: Request) -> ModelProfileView:
    if "base_url" in req.model_fields_set:
        await _validate_profile_url(req.base_url, request.app.state.settings)
    try:
        view = await _catalog(request).update_profile(
            profile_id, req.model_dump(exclude_unset=True)
        )
    except IntegrityError as exc:
        _raise_for_integrity(exc, duplicate_detail=_PROFILE_DUPLICATE, fk_detail=_AGENT_BAD_PROFILE)
    if view is None:
        raise HTTPException(status_code=404, detail="model profile not found")
    return view


@router.delete("/models/{profile_id}", status_code=204)
async def delete_model(profile_id: str, request: Request) -> Response:
    if not await _catalog(request).delete_profile(profile_id):
        raise HTTPException(status_code=404, detail="model profile not found")
    return Response(status_code=204)


@router.post("/models/{profile_id}/test")
async def test_model(profile_id: str, request: Request) -> TestResult:
    """对模型档案发一个最小补全,验证 base_url/key/model 可用。"""
    _check_probe_limit(request)
    profile = await _catalog(request).get_profile_full(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="model profile not found")
    if not profile.api_key:
        return TestResult(ok=False, latency_ms=0, detail="未设置 API Key")
    return await _run_probe(_probe_llm(profile, request.app.state.settings))


@router.post("/models/test-config")
async def test_model_config(req: ProfileProbe, request: Request) -> TestResult:
    _check_probe_limit(request)
    profile = await _resolve_probe(req, _catalog(request))
    if not profile.api_key:
        return TestResult(ok=False, latency_ms=0, detail="未设置 API Key")
    return await _run_probe(_probe_llm(profile, request.app.state.settings))


@router.post("/models/discover")
async def discover_models(req: ProfileProbe, request: Request) -> ModelDiscoveryResult:
    _check_probe_limit(request)
    from openai import AsyncOpenAI

    profile = await _resolve_probe(req, _catalog(request))
    if not profile.api_key:
        raise HTTPException(status_code=422, detail="请先填写 API Key")
    try:
        await validate_provider_url_resolved(
            profile.base_url,
            allow_private=request.app.state.settings.allow_private_provider_urls,
            allowlist=request.app.state.settings.provider_host_allowlist,
        )
    except ProviderURLPolicyError as exc:
        raise HTTPException(status_code=422, detail=f"base_url 无效：{exc}") from exc
    started = time.perf_counter()
    client = AsyncOpenAI(
        api_key=profile.api_key,
        base_url=profile.base_url,
        timeout=20.0,
        max_retries=0,
        http_client=provider_http_client(
            allow_private=request.app.state.settings.allow_private_provider_urls,
            timeout=20.0,
        ),
    )
    try:
        page = await client.models.list()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"拉取模型列表失败：{_exception_detail(exc)}",
        ) from exc
    finally:
        await client.close()
    models = sorted({item.id for item in page.data if item.id}, key=str.lower)
    latency_ms = int((time.perf_counter() - started) * 1000)
    return ModelDiscoveryResult(models=models, latency_ms=latency_ms)


# ── 角色卡片 ────────────────────────────────────────────────────────────
@router.get("/agents")
async def list_agents(request: Request) -> list[AgentCardView]:
    return await _catalog(request).list_agents()


@router.post("/agents", status_code=201)
async def create_agent(req: AgentCardCreate, request: Request) -> AgentCardView:
    if req.behavior not in BEHAVIORS:
        raise HTTPException(status_code=422, detail=f"behavior 必须是 {list(BEHAVIORS)} 之一")
    _reject_reserved_agent_name(req.name)
    try:
        return await _catalog(request).create_agent(req)
    except IntegrityError as exc:
        _raise_for_integrity(exc, duplicate_detail=_AGENT_DUPLICATE, fk_detail=_AGENT_BAD_PROFILE)


@router.put("/agents/{agent_id}")
async def update_agent(agent_id: str, req: AgentCardUpdate, request: Request) -> AgentCardView:
    if req.behavior is not None and req.behavior not in BEHAVIORS:
        raise HTTPException(status_code=422, detail=f"behavior 必须是 {list(BEHAVIORS)} 之一")
    try:
        view = await _catalog(request).update_agent(agent_id, req)
    except IntegrityError as exc:
        _raise_for_integrity(exc, duplicate_detail=_AGENT_DUPLICATE, fk_detail=_AGENT_BAD_PROFILE)
    if view is None:
        raise HTTPException(status_code=404, detail="agent card not found")
    return view


@router.delete("/agents/{agent_id}", status_code=204)
async def delete_agent(agent_id: str, request: Request) -> Response:
    if not await _catalog(request).delete_agent(agent_id):
        raise HTTPException(status_code=404, detail="agent card not found")
    return Response(status_code=204)


# ── 搜索 key 池 ─────────────────────────────────────────────────────────
@router.get("/search-keys")
async def list_keys(request: Request) -> list[SearchKeyView]:
    return await _catalog(request).list_keys()


@router.post("/search-keys", status_code=201)
async def create_key(req: KeyCreate, request: Request) -> SearchKeyView:
    return await _catalog(request).create_key(
        label=req.label, api_key=req.api_key, priority=req.priority, enabled=req.enabled
    )


@router.put("/search-keys/{key_id}")
async def update_key(key_id: str, req: KeyUpdate, request: Request) -> SearchKeyView:
    view = await _catalog(request).update_key(key_id, req.model_dump(exclude_unset=True))
    if view is None:
        raise HTTPException(status_code=404, detail="search key not found")
    return view


@router.delete("/search-keys/{key_id}", status_code=204)
async def delete_key(key_id: str, request: Request) -> Response:
    if not await _catalog(request).delete_key(key_id):
        raise HTTPException(status_code=404, detail="search key not found")
    return Response(status_code=204)


@router.post("/search-keys/{key_id}/test")
async def test_key(key_id: str, request: Request) -> TestResult:
    """对搜索 key 发一次最小检索,验证 key 可用。"""
    _check_probe_limit(request)
    secret = await _catalog(request).get_key_secret(key_id)
    if secret is None:
        raise HTTPException(status_code=404, detail="search key not found")
    return await _run_probe(_probe_search(secret))


# ── 自定义工作流 ──────────────────────────────────────────────────────────
@router.get("/workflows/custom")
async def list_custom_workflows(request: Request) -> list[WorkflowDefView]:
    return await _catalog(request).list_workflow_defs()


@router.post("/workflows/custom", status_code=201)
async def create_custom_workflow(req: WorkflowDefCreate, request: Request) -> WorkflowDefView:
    _reject_builtin_name(req.name)
    steps = _normalize_graph_payload(req) or []
    await _validate_custom_steps(
        steps,
        _catalog(request),
        request.app.state.settings,
        nodes=req.nodes,
        edges=req.edges,
    )
    try:
        return await _catalog(request).create_workflow_def(req)
    except IntegrityError as exc:
        _raise_for_integrity(
            exc, duplicate_detail=_WORKFLOW_DUPLICATE, fk_detail=_AGENT_BAD_PROFILE
        )


@router.put("/workflows/custom/{workflow_id}")
async def update_custom_workflow(
    workflow_id: str, req: WorkflowDefUpdate, request: Request
) -> WorkflowDefView:
    catalog = _catalog(request)
    existing = await catalog.get_workflow_def_by_id(workflow_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    if req.version is not None and req.version != existing.version:
        raise HTTPException(
            status_code=409,
            detail=(
                f"工作流已被其他会话更新（当前版本 {existing.version}，"
                f"提交版本 {req.version}），请刷新后重试"
            ),
        )
    steps = _normalize_graph_payload(req, existing=existing)
    if steps is not None:
        await _validate_custom_steps(
            steps,
            catalog,
            request.app.state.settings,
            nodes=req.nodes,
            edges=req.edges,
        )
    try:
        view = await catalog.update_workflow_def(workflow_id, req)
    except WorkflowVersionConflictError as exc:
        raise HTTPException(status_code=409, detail="工作流已被其他会话更新，请刷新后重试") from exc
    if view is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return view


@router.delete("/workflows/custom/{workflow_id}", status_code=204)
async def delete_custom_workflow(workflow_id: str, request: Request) -> Response:
    if not await _catalog(request).delete_workflow_def(workflow_id):
        raise HTTPException(status_code=404, detail="workflow not found")
    return Response(status_code=204)
