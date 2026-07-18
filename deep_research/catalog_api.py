"""角色广场 catalog 的 REST API：模型档案 / 角色卡片 / 搜索 key 的 CRUD。

挂载在 /api 下，复用主 app 的 require_api_key 鉴权与 app.state.catalog 仓储。
密钥一律脱敏回显、空值不覆盖（与全局配置同语义）。
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

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
from .catalog.repository import CatalogRepository
from .config import Settings
from .observability import Tracer


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

    llm = LLM.from_params(
        Tracer(),
        api_key=profile.api_key,
        base_url=profile.base_url,
        model=profile.model,
        timeout=settings.request_timeout,
        user_agent=settings.llm_user_agent,
        temperature=0.0,
    )
    try:
        await llm.complete("connectivity test", "ping", temperature=0.0)
    finally:
        await llm.aclose()


async def _probe_search(api_key: str) -> None:
    """用单个 key 发一次最小检索验证可用;失败抛异常。"""
    from tavily import AsyncTavilyClient

    client = AsyncTavilyClient(api_key=api_key)
    await client.search("ping", max_results=1)


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


def _reject_builtin_name(name: str) -> None:
    """自定义流程名不得与内置流程同名（否则运行解析时被内置优先级永久屏蔽）。"""
    from .workflows import WORKFLOWS

    if name in WORKFLOWS:
        raise HTTPException(status_code=422, detail=f"名称「{name}」与内置流程冲突，请改名")


async def _validate_custom_steps(
    steps: list[dict], catalog: CatalogRepository, settings: Settings
) -> None:
    """校验自定义工作流的 steps 是否安全可执行（复用引擎的 validate_workflow）；不合法抛 422。"""
    from .registry import available
    from .workflow import Step, validate_workflow

    enabled_cards = [c for c in await catalog.list_agents() if c.enabled]
    # 可编排角色 = 内置注册角色 + 已启用自定义卡片名（运行期 resolve_agent 能解析这两类）
    available_roles = set(available()) | {c.name for c in enabled_cards}
    # 终端角色 = 内置终端 + behavior=synthesize 的自定义卡片（它们也能产出报告）
    terminal_roles = {"synthesizer", "aggregator"} | {
        c.name for c in enabled_cards if c.behavior == "synthesize"
    }
    try:
        parsed = [Step.model_validate(s) for s in steps]
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"步骤格式非法：{e}") from e
    errors = validate_workflow(
        parsed, available_roles, max_rounds_cap=settings.max_rounds, terminal_roles=terminal_roles
    )
    if errors:
        raise HTTPException(status_code=422, detail="工作流不合法：" + "；".join(errors))


def _normalize_graph_payload(payload: WorkflowDefCreate | WorkflowDefUpdate) -> list[dict] | None:
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
        if "nodes" in fields:
            nodes = [WorkflowNode.model_validate(node) for node in (payload.nodes or [])]
            edges = [WorkflowEdge.model_validate(edge) for edge in (payload.edges or [])]
            for edge in edges:
                evaluate_condition(edge.condition, {})
            steps = graph_topological_steps(nodes, edges)
            payload.steps = steps
            payload.nodes = [node.model_dump() for node in nodes]
            payload.edges = [edge.model_dump() for edge in edges]
            payload.viewport = WorkflowViewport.model_validate(payload.viewport or {}).model_dump()
            return steps
        if "steps" in fields or isinstance(payload, WorkflowDefCreate):
            steps = payload.steps or []
            nodes, edges = steps_to_graph(steps)
            payload.nodes = [node.model_dump() for node in nodes]
            payload.edges = [edge.model_dump() for edge in edges]
            payload.viewport = WorkflowViewport().model_dump()
            return steps
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


@router.put("/models/{profile_id}")
async def update_model(profile_id: str, req: ProfileUpdate, request: Request) -> ModelProfileView:
    view = await _catalog(request).update_profile(profile_id, req.model_dump(exclude_unset=True))
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
    profile = await _catalog(request).get_profile_full(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="model profile not found")
    if not profile.api_key:
        return TestResult(ok=False, latency_ms=0, detail="未设置 API Key")
    return await _run_probe(_probe_llm(profile, request.app.state.settings))


@router.post("/models/test-config")
async def test_model_config(req: ProfileProbe, request: Request) -> TestResult:
    profile = await _resolve_probe(req, _catalog(request))
    if not profile.api_key:
        return TestResult(ok=False, latency_ms=0, detail="未设置 API Key")
    return await _run_probe(_probe_llm(profile, request.app.state.settings))


@router.post("/models/discover")
async def discover_models(req: ProfileProbe, request: Request) -> ModelDiscoveryResult:
    from openai import AsyncOpenAI

    profile = await _resolve_probe(req, _catalog(request))
    if not profile.api_key:
        raise HTTPException(status_code=422, detail="请先填写 API Key")
    started = time.perf_counter()
    client = AsyncOpenAI(
        api_key=profile.api_key,
        base_url=profile.base_url,
        timeout=20.0,
        max_retries=0,
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
    return await _catalog(request).create_agent(req)


@router.put("/agents/{agent_id}")
async def update_agent(agent_id: str, req: AgentCardUpdate, request: Request) -> AgentCardView:
    if req.behavior is not None and req.behavior not in BEHAVIORS:
        raise HTTPException(status_code=422, detail=f"behavior 必须是 {list(BEHAVIORS)} 之一")
    view = await _catalog(request).update_agent(agent_id, req)
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
    await _validate_custom_steps(steps, _catalog(request), request.app.state.settings)
    return await _catalog(request).create_workflow_def(req)


@router.put("/workflows/custom/{workflow_id}")
async def update_custom_workflow(
    workflow_id: str, req: WorkflowDefUpdate, request: Request
) -> WorkflowDefView:
    steps = _normalize_graph_payload(req)
    if steps is not None:
        await _validate_custom_steps(steps, _catalog(request), request.app.state.settings)
    view = await _catalog(request).update_workflow_def(workflow_id, req)
    if view is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return view


@router.delete("/workflows/custom/{workflow_id}", status_code=204)
async def delete_custom_workflow(workflow_id: str, request: Request) -> Response:
    if not await _catalog(request).delete_workflow_def(workflow_id):
        raise HTTPException(status_code=404, detail="workflow not found")
    return Response(status_code=204)
