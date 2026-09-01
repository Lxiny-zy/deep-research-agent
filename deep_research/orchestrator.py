"""编排器：用声明式工作流引擎把角色串起来，跑完落库并产出报告。

重构后本类不再写死「Planner→Researcher→Reflector→Synthesizer」的流程，而是：
  - 组装 RunContext（共享 llm/search/tracer/settings）
  - 选择一份 Workflow（默认 deep；可经 workflow 参数路由到 quick 等）
  - 交给 WorkflowEngine 执行，再把黑板上的产物落库

两种运行方式与持久化语义与重构前完全一致：
  - run(query)        ：跑完返回 Report（CLI / 评估用）
  - run_stream(query) ：以事件流方式产出，供 FastAPI 用 SSE 实时推送（Web Demo 用）

可选注入 repo：注入后把运行全过程落库（计划/结果/报告/事件）；repo=None 时
行为与无持久化完全一致。可传入外部 run_id（API 先 create_run 拿到 id 再后台执行）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel

from .agents import Planner, Reflector, Researcher, Synthesizer  # noqa: F401 触发角色注册
from .agents.base import Blackboard, RunContext
from .artifacts import ArtifactStore
from .config import Settings
from .llm import LLM
from .models import Finding, Report, ResearchResult, SubQuestion
from .observability import Event, Tracer
from .orchestration import OrchestrationRuntime, WorkflowRun
from .persistence.repository import LeaseLostError, ResearchRepository
from .planner_runtime import (
    ARTIFACT_MANIFEST_SCRATCH_KEY,
    ARTIFACT_SLUG_SCRATCH_KEY,
    PLAN_SCRATCH_KEY,
    build_execution_plan,
    coerce_execution_plan,
    load_persisted_plan,
    persist_blackboard_artifacts,
    persist_plan,
    plan_json,
    project_blackboard,
    store_plan_in_blackboard,
    sync_plan_from_workflow,
)
from .planning import stable_slug
from .prompting import load_global_rules
from .reproducibility import (
    RUN_MANIFEST_CHECKPOINT_KEY,
    RecordingSearchTool,
    build_run_manifest,
)
from .runner import CommandRunner
from .scheduler import research_dag
from .skills import default_skill_resolver
from .token_budget import TokenBudget
from .tools.base import SearchTool
from .workflow import (
    Step,
    Workflow,
    WorkflowEngine,
    validate_workflow_graph_terminal,
    validate_workflow_steps_terminal,
)
from .workflows import WORKFLOWS, get_workflow

if TYPE_CHECKING:
    from .catalog.runtime import CatalogRuntime, CatalogSource


logger = logging.getLogger(__name__)
ModelT = TypeVar("ModelT", bound=BaseModel)

# Generic planner steps can produce a useful overall report even when a model
# refuses or times out on one intermediate step.  Those task-level gaps map to
# Vela's ``partial`` state.  Failures that clearly belong to the execution
# substrate (storage, authorization, leasing, or command dispatch) remain
# ``failed`` so they can trigger retry/alert handling.
_INFRA_FAILURE_MARKERS = (
    "artifact",
    "storage",
    "permission",
    "unauthorized",
    "authentication",
    "forbidden",
    "lease",
    "scheduling",
    "scheduler",
    "command runner",
    "operation runner",
    "required operation output",
    "path escapes",
)


def _is_infrastructure_step_failure(error: object) -> bool:
    message = str(error or "").casefold()
    return any(marker in message for marker in _INFRA_FAILURE_MARKERS)


RUN_SETTINGS_CHECKPOINT_KEY = "_run_settings"
RUN_METRICS_CHECKPOINT_KEY = "_runtime_metrics"
RUN_CATALOG_CHECKPOINT_KEY = "_catalog_runtime"
_RUN_SETTING_FIELDS = (
    "max_sub_questions",
    "max_rounds",
    "max_concurrency",
    "results_per_search",
    "fulltext_enabled",
    "fulltext_max_chars",
    "require_corroboration",
    "max_tokens",
    "max_replans",
    "request_timeout",
    "max_run_seconds",
    "orchestration_mode",
)


def checkpoint_settings(settings: Settings) -> dict[str, bool | int | float | str | None]:
    """Serialize non-secret run behavior so recovery keeps the original limits."""
    return {name: getattr(settings, name) for name in _RUN_SETTING_FIELDS}


def workflow_catalog_roles(workflow: Workflow) -> set[str]:
    """Return role names whose catalog semantics can affect this workflow."""
    roles = {"evidence_verifier"}
    steps = [*workflow.steps]
    steps.extend(Step.model_validate(node["step"]) for node in workflow.nodes)
    for step in steps:
        if step.fallback_agent:
            roles.add(step.fallback_agent)
        if step.kind == "reflect_loop":
            roles.update((step.reflector, step.researcher))
        elif step.kind == "team_fanout":
            roles.update((step.aggregator, "researcher"))
        else:
            if step.agent:
                roles.add(step.agent)
            if step.kind == "compose":
                # Coordinator-generated workflows are restricted to these roles.
                roles.update(("planner", "researcher", "reflector", "synthesizer", "critic"))
    roles.discard("")
    return roles


async def snapshot_catalog_for_execution(
    execution: WorkflowRun,
    catalog_repo: CatalogSource | None,
) -> None:
    """Persist the non-secret role semantics needed to recover this execution."""
    if catalog_repo is None or not execution.definition:
        return
    scratch = execution.checkpoint.setdefault("scratch", {})
    if not isinstance(scratch, dict):
        raise ValueError("execution checkpoint scratch must be an object")
    if RUN_CATALOG_CHECKPOINT_KEY in scratch:
        return

    from .catalog.runtime import create_catalog_runtime_snapshot

    workflow = Workflow.model_validate(execution.definition)
    snapshot = await create_catalog_runtime_snapshot(catalog_repo, workflow_catalog_roles(workflow))
    scratch[RUN_CATALOG_CHECKPOINT_KEY] = snapshot.model_dump(mode="json")


def create_initial_execution(
    query: str,
    workflow_name: str | None,
    settings: Settings,
    *,
    requested_workflow: str | None = None,
    execution_plan: Any | None = None,
) -> WorkflowRun:
    """Create the durable, leased checkpoint used before a background task starts."""
    runtime = OrchestrationRuntime()
    execution = runtime.start(workflow_name or "deep", {"query": query})
    scratch: dict[str, Any] = {
        RUN_SETTINGS_CHECKPOINT_KEY: checkpoint_settings(settings),
    }
    # Preserve the user's explicit choice across queue admission and worker
    # recovery.  ``workflow_name`` may instead be an intent-derived route,
    # which must not be treated as an explicit choice on resume.
    if requested_workflow:
        scratch["requested_workflow"] = requested_workflow
    # Persist the deterministic workspace identity before a worker starts so
    # a crash between queue admission and the first checkpoint cannot create a
    # second artifact tree on recovery.
    if settings.orchestration_mode != "legacy" or execution_plan is not None:
        scratch[ARTIFACT_SLUG_SCRATCH_KEY] = stable_slug(query)
    if execution_plan is not None:
        # Normalize before queue admission so malformed plans fail at request
        # time and the worker receives a complete, slugged snapshot.
        normalized_plan = coerce_execution_plan(execution_plan, query=query, initial=True)
        scratch[PLAN_SCRATCH_KEY] = normalized_plan.model_dump(mode="json")
        scratch[ARTIFACT_SLUG_SCRATCH_KEY] = normalized_plan.slug
    execution.checkpoint = Blackboard(query=query, scratch=scratch).model_dump(mode="json")
    if workflow_name is None or workflow_name in WORKFLOWS:
        execution.definition = get_workflow(workflow_name).model_dump(mode="json")
    return execution


class _LazyOwnedLLM(LLM):
    """Delay opening the default LLM client until a fallback call needs it."""

    def __init__(self, factory: Callable[[], LLM]) -> None:
        self._factory = factory
        self._value: LLM | None = None

    def _get(self) -> LLM:
        if self._value is None:
            self._value = self._factory()
        return self._value

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get(), name)

    async def complete(self, system: str, user: str, *, temperature: float = 0.3) -> str:
        return await self._get().complete(system, user, temperature=temperature)

    async def parse(
        self,
        system: str,
        user: str,
        schema: type[ModelT],
        *,
        temperature: float = 0.2,
        retries: int = 2,
    ) -> ModelT:
        return await self._get().parse(
            system, user, schema, temperature=temperature, retries=retries
        )

    async def stream(
        self, system: str, user: str, *, temperature: float = 0.4
    ) -> AsyncIterator[str]:
        async for chunk in self._get().stream(system, user, temperature=temperature):
            yield chunk

    async def aclose(self) -> None:
        if self._value is None:
            return
        await self._value.aclose()
        self._value = None


class _LazyOwnedSearchTool(SearchTool):
    """Delay opening the built-in search client while preserving ownership."""

    def __init__(self, factory: Callable[[], SearchTool]) -> None:
        self._factory = factory
        self._value: SearchTool | None = None

    @property
    def backend_name(self) -> str:
        return "TavilySearch"

    def _get(self) -> SearchTool:
        if self._value is None:
            self._value = self._factory()
        return self._value

    async def search(self, query: str, *, max_results: int = 5):  # type: ignore[no-untyped-def]
        return await self._get().search(query, max_results=max_results)

    async def aclose(self) -> None:
        if self._value is None:
            return
        await self._value.aclose()
        self._value = None


def _default_search_tool(settings: Settings) -> SearchTool:
    from .tools.tavily_search import TavilySearch

    return TavilySearch(settings.tavily_api_key)


class DeepResearchAgent:
    def __init__(
        self,
        settings: Settings,
        *,
        llm: LLM | None = None,
        search_tool: SearchTool | None = None,
        repo: ResearchRepository | None = None,
        run_id: str | None = None,
        workflow: str | None = None,
        requested_workflow: str | None = None,
        catalog_repo: CatalogSource | None = None,
        resume_execution: WorkflowRun | None = None,
        initial_execution: WorkflowRun | None = None,
        lease_owner: str | None = None,
        artifact_store: ArtifactStore | None = None,
        command_runner: Any | None = None,
        skill_resolver: Any | None = None,
        artifact_slug: str | None = None,
        execution_plan: Any | None = None,
    ) -> None:
        self.settings = settings
        self.tracer = Tracer()
        self.repo = repo
        self._run_id = run_id
        self._workflow_name = workflow
        # 用户**显式**指定的工作流，与上面解析后的 workflow 区分开。
        # 二者混用是个真实的坑：意图预路由会把 workflow 改写成推断结果，
        # 若拿它当「用户的显式选择」，plan_route 会认为用户已指定而完全让位，
        # 意图路由与子问题预算就永远不生效。默认 None＝用户没指定。
        self._requested_workflow = requested_workflow
        self._catalog_repo = catalog_repo  # 数据驱动角色/模型档案来源（鸭子类型）
        self._resume_execution = resume_execution
        self._initial_execution = initial_execution
        self._lease_owner = lease_owner
        self._artifact_store = artifact_store
        self._command_runner = command_runner
        self._skill_resolver = skill_resolver
        self._artifact_slug = artifact_slug
        # Keep the raw value until ``run(query)`` supplies the query-derived
        # fallback slug.  Validation then happens exactly once at the planner
        # boundary and the normalized model is persisted in the checkpoint.
        self._execution_plan_input = execution_plan
        self._persisted_event_count = 0
        existing_execution = resume_execution or initial_execution
        if existing_execution is not None:
            scratch = existing_execution.checkpoint.get("scratch", {})
            metrics = (
                scratch.get(RUN_METRICS_CHECKPOINT_KEY, {}) if isinstance(scratch, dict) else {}
            )
            if isinstance(metrics, dict):
                try:
                    self.tracer.restore_metrics(
                        total_tokens=int(metrics.get("total_tokens", 0) or 0),
                        estimated_tokens=int(metrics.get("estimated_tokens", 0) or 0),
                        elapsed=float(metrics.get("elapsed", 0.0) or 0.0),
                    )
                except (TypeError, ValueError):
                    pass  # corrupted optional metrics must not block checkpoint recovery
        # 运行期延迟加载（需 await），收尾时关闭其 LLM 池
        self._catalog_runtime: CatalogRuntime | None = None
        self._run_started = False

        # Validate only dependencies constructed here. A catalog default model
        # is loaded asynchronously, so its LLM validation is deferred to run().
        if search_tool is None:
            settings.validate_search()
        if llm is None and catalog_repo is None:
            settings.validate_llm()
        self._owns_llm = llm is None  # 自建的 client 由本实例负责关闭；注入的归调用方管
        tracer = self.tracer
        self.llm = llm if llm is not None else _LazyOwnedLLM(lambda: LLM(settings, tracer))
        self._owns_search_tool = search_tool is None
        raw_search_tool = (
            search_tool
            if search_tool is not None
            else _LazyOwnedSearchTool(lambda: _default_search_tool(settings))
        )
        self.search_tool = RecordingSearchTool(raw_search_tool)

        # 仍构造具名角色实例：向后兼容（测试替换 self.researcher、直接调用各角色）
        self.planner = Planner(self.llm, self.tracer, settings)
        self.researcher = Researcher(self.llm, self.search_tool, self.tracer, settings)
        self.reflector = Reflector(self.llm, self.tracer, settings)
        self.synthesizer = Synthesizer(self.llm, self.tracer, settings)
        self._sem = asyncio.Semaphore(settings.max_concurrency)

    async def aclose(self) -> None:
        """释放自建 LLM client 的底层 HTTP 连接池。注入的 client 归调用方管。"""
        errors: list[Exception] = []
        if self._catalog_runtime is not None:
            try:
                await self._catalog_runtime.aclose()  # 关闭按档案新建的 LLM 池
            except Exception as exc:
                errors.append(exc)
            else:
                self._catalog_runtime = None
        if self._owns_llm:
            try:
                await self.llm.aclose()
            except Exception as exc:
                errors.append(exc)
        if self._owns_search_tool:
            try:
                await self.search_tool.aclose()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise errors[0]

    def _claim_run(self) -> None:
        if self._run_started:
            raise RuntimeError("DeepResearchAgent instances are single-use")
        self._run_started = True

    async def _research_one(
        self, question: str, context_findings: list[Finding] | None = None
    ) -> ResearchResult | None:
        async with self._sem:  # 限流，避免打爆检索 API
            return await self.researcher.run(question, context_findings=context_findings)

    async def _research_dag(self, sub_questions: list[SubQuestion]) -> list[ResearchResult]:
        """按依赖拓扑分层并行检索（保留为公开方法：测试替换 self.researcher 后直接调用）。"""
        return await research_dag(sub_questions, self._research_one, self.tracer)

    async def run(self, query: str) -> Report:
        self._claim_run()
        return await self._run_once(query)

    async def _run_once(self, query: str) -> Report:
        run_id = self._run_id
        try:
            if self.repo is not None:
                if run_id is None:
                    run_id = await self.repo.create_run(query)
                await self.repo.set_status(run_id, "running", lease_owner=self._lease_owner)

            self.tracer.emit("ORCHESTRATOR", "start", f"开始深度研究：{query}")

            report = await self._run_workflow(query, run_id)

            self.tracer.emit("ORCHESTRATOR", "report", "报告生成完成", data=report.model_dump())

            # 先落库再发 done：客户端收到 done 后立刻读详情，必须能读到完整数据
            if self.repo is not None and run_id is not None:
                await self.repo.finalize(
                    run_id,
                    elapsed=self.tracer.elapsed,
                    total_tokens=self.tracer.total_tokens,
                    lease_owner=self._lease_owner,
                )
            self.tracer.emit(
                "ORCHESTRATOR",
                "done",
                f"完成 ｜ 用时 {self.tracer.elapsed:.1f}s ｜ token {self.tracer.total_tokens}",
                data={
                    "elapsed": round(self.tracer.elapsed, 1),
                    "total_tokens": self.tracer.total_tokens,
                    "tokens_estimated": self.tracer.tokens_estimated,
                    "sources": len(report.citations),
                },
            )
            return report
        except Exception as e:
            self.tracer.emit("ORCHESTRATOR", "error", f"运行失败：{e}")
            if self.repo is not None and run_id is not None:
                try:
                    await self.repo.set_status(run_id, "error", lease_owner=self._lease_owner)
                except LeaseLostError:
                    # A successor owns the run now; never overwrite its state.
                    pass
                except Exception:
                    logger.exception("failed to persist error status for run %s", run_id)
            raise
        finally:
            # Checkpoints flush incrementally; this final flush captures the
            # report and terminal event emitted after the last checkpoint.
            if self.repo is not None and run_id is not None:
                try:
                    await self._flush_events(run_id)
                except LeaseLostError:
                    # Do not mask the original failure/cancellation after fencing.
                    pass
                except Exception:
                    logger.exception("failed to persist events for run %s", run_id)

    async def _flush_events(self, run_id: str) -> None:
        if self.repo is None:
            return
        pending = self.tracer.events[self._persisted_event_count :]
        if not pending:
            return
        stored = await self.repo.append_events(run_id, pending, lease_owner=self._lease_owner)
        for original, durable in zip(pending, stored, strict=True):
            original.seq = durable.seq
            original.attempt = durable.attempt
        self._persisted_event_count += len(pending)

    async def _resolve_workflow(self) -> Workflow:
        """解析本次要跑的工作流：内置预置优先 → 自定义（catalog 按名）→ 兜底默认。

        自定义工作流来自 catalog DB（构建器页面存的命名流程）；存时已 validate_workflow 校验过，
        这里再 Step.model_validate 还原。catalog 不可用 / 未命中时一律回退 get_workflow（→deep）。
        """
        name = self._workflow_name
        if name and name not in WORKFLOWS and self._catalog_repo is not None:
            try:
                wd = await self._catalog_repo.get_workflow_def(name)
            except Exception:
                self.tracer.emit("ORCHESTRATOR", "info", f"加载自定义工作流「{name}」失败，回退")
                wd = None
            if wd is not None and wd.enabled:
                return Workflow(
                    name=wd.name,
                    description=wd.description,
                    steps=[Step.model_validate(s) for s in wd.steps],
                    nodes=wd.nodes,
                    edges=wd.edges,
                )
        return get_workflow(name)

    async def _apply_intent_gate(self, bb: Blackboard, ctx: RunContext) -> None:
        """在引擎启动前统一执行意图门禁——所有入口的必经之路。

        **为什么不做成工作流里的一个步骤**：那样门禁只在编排了 ``intent_router``
        的流程（历史上的 ``guarded``）里生效，而 ``/api/research`` 快路径、CLI、
        自定义流程和 planner-authored 流程都可能不走那条流程。把安全属性挂在
        「用户/路由恰好选中了某条工作流」上，等于让它可被绕过——安全门禁必须在
        **所有执行路径的交汇点**上，而 ``_run_workflow`` 正是这个点。

        **为什么不把 intent_router 插进 wf.steps**：引擎按位置 ``step-{i+1}``
        匹配 checkpoint 做断点续跑，往前面插一步会让所有既有 run 的步骤编号错位，
        恢复时张冠李戴。这里改为在引擎之外跑，通过既有的 halt 原语终止——
        halt 本来就是为「角色请求提前终止」设计的通用机制。

        历史 ``guarded`` 流程仍保留 ``intent_router`` 步骤以兼容旧 checkpoint；它
        不是安全保证，也不会出现在公共模板或自动路由中。角色见到已有判定会复用
        而不重判，因此不会双重付费。
        """
        if not self.settings.intent_enabled:
            return
        # 已有判定（API 预路由或 checkpoint 恢复）由 IntentRouter 自己处理复用与
        # 补跑逻辑，这里只负责保证「门禁一定跑过一次」。
        from .agents.intent_router import IntentRouter

        await IntentRouter().step(bb, ctx)

    async def _run_workflow(self, query: str, run_id: str | None) -> Report:
        """组装上下文、执行工作流，并在产出关键里程碑时落库（计划/结果/报告）。

        落库经黑板的「持久化钩子」在引擎步骤之间触发，保证语义与重构前一致：
        计划生成即落库、研究结果产出即落库、报告生成即落库。
        """
        # 加载数据驱动角色/模型档案（无 catalog 或无自定义角色时为 None，走纯内置路径）
        from .catalog.runtime import load_catalog_runtime

        if self._catalog_runtime is not None:
            raise RuntimeError("catalog runtime already initialized")
        existing_execution = self._resume_execution or self._initial_execution
        bb = (
            Blackboard.model_validate(existing_execution.checkpoint)
            if existing_execution is not None and existing_execution.checkpoint
            else Blackboard(query=query)
        )
        bb.scratch.setdefault(RUN_SETTINGS_CHECKPOINT_KEY, checkpoint_settings(self.settings))
        # Supplying a plan also upgrades an explicitly legacy deployment to the
        # planner runtime, keeping the Python/API plan entry point self-contained.
        planner_mode = (
            self.settings.orchestration_mode != "legacy"
            or self._execution_plan_input is not None
        )
        provided_plan = None
        if self._execution_plan_input is not None:
            provided_plan = coerce_execution_plan(
                self._execution_plan_input,
                query=query,
                initial=True,
            )
            # The plan owns the workspace identity.  Reject an explicit slug
            # mismatch instead of silently writing one run into another tree.
            if self._artifact_slug is not None and self._artifact_slug != provided_plan.slug:
                raise ValueError(
                    "artifact_slug does not match execution_plan.slug"
                )
            self._artifact_slug = provided_plan.slug
            # ``execution_plan`` is an explicit caller contract.  Force the
            # source marker even when a payload contains an internal-looking
            # value such as ``workflow_projection``; otherwise it could skip
            # compilation and silently execute the legacy workflow instead.
            provided_plan.metadata["source"] = "external"
        if planner_mode:
            # Direct/CLI callers may construct DeepResearchAgent without the
            # RunExecutor factory.  Create the same isolated store lazily so
            # both entry points obey the artifact-first contract.
            if self._artifact_store is None:
                self._artifact_store = ArtifactStore(
                    Path(self.settings.artifact_root),
                    max_bytes=self.settings.artifact_max_bytes,
                )
            raw_slug = bb.scratch.get(ARTIFACT_SLUG_SCRATCH_KEY)
            self._artifact_slug = (
                self._artifact_slug
                or (raw_slug if isinstance(raw_slug, str) else None)
                or (provided_plan.slug if provided_plan is not None else None)
                or stable_slug(query)
            )
            bb.scratch[ARTIFACT_SLUG_SCRATCH_KEY] = self._artifact_slug
            if self._command_runner is None and self.settings.runner_enabled:
                self._command_runner = CommandRunner(
                    workspace_root=self._artifact_store.workspace_root,
                    allowed_operations=self.settings.runner_allowed_operations,
                    default_timeout_seconds=self.settings.runner_default_timeout,
                    max_output_bytes=self.settings.runner_max_output_bytes,
                    max_processes=self.settings.runner_max_processes,
                )
            if self._skill_resolver is None:
                self._skill_resolver = default_skill_resolver(Path.cwd())
        artifact_slug = self._artifact_slug
        if planner_mode:
            # The planner branch always establishes a stable slug above; keep
            # a local non-optional binding for the persistence helpers.
            assert artifact_slug is not None
        # 让 IntentRouter 知道用户是否显式选了工作流：显式选择优先于意图路由。
        # 注意用的是 _requested_workflow 而非 _workflow_name——后者可能是意图
        # 预路由推断出来的结果，拿它当「显式选择」会让路由永久自我禁用。
        if self._requested_workflow:
            bb.scratch.setdefault("requested_workflow", self._requested_workflow)
        elif (
            existing_execution is None
            and self._execution_plan_input is None
            and self._workflow_name not in {None, "guarded"}
        ):
            # Direct library/CLI callers do not have the API's separate
            # ``requested_workflow`` field.  Treat a named workflow as an
            # explicit choice so a factual query cannot silently replace
            # ``quick`` (or a catalog workflow) with an inferred route.  The
            # legacy ``guarded`` alias is intentionally excluded: old callers
            # used it as an intent-gate entry point, and its compatibility
            # intent_router step must remain runnable.
            bb.scratch.setdefault("requested_workflow", self._workflow_name)
        raw_catalog_snapshot = bb.scratch.get(RUN_CATALOG_CHECKPOINT_KEY)
        if raw_catalog_snapshot is not None and not isinstance(raw_catalog_snapshot, dict):
            raise ValueError("catalog checkpoint snapshot must be an object")
        cr = await load_catalog_runtime(
            self._catalog_repo,
            self.tracer,
            self.settings,
            snapshot=raw_catalog_snapshot,
        )
        self._catalog_runtime = cr
        if self._owns_llm and (cr is None or not cr.has_default_profile):
            self.settings.validate_llm()

        ctx = RunContext(
            llm=self.llm,
            search_tool=self.search_tool,
            tracer=self.tracer,
            settings=self.settings,
            llm_resolver=cr.resolve_llm if cr is not None else None,
            artifact_store=self._artifact_store,
            command_runner=self._command_runner,
            skill_resolver=self._skill_resolver,
            run_id=run_id,
            artifact_slug=artifact_slug,
            global_rules=load_global_rules(),
        )
        budget = TokenBudget(max_tokens=self.settings.max_tokens)

        # The API normally performs this preflight before creating the durable
        # run.  Direct Python/CLI callers do not have that outer request layer,
        # so the same gate must run before resolving the workflow here.  This
        # ordering is important: a route discovered after ``wf`` (or after the
        # planner shadow plan) would only be telemetry and the actual execution
        # would still follow the default deep workflow.
        #
        # Existing checkpoints and caller-authored plans are immutable execution
        # contracts.  Their cached intent decision is still checked globally,
        # but neither recovery nor an external plan may be rewritten by a new
        # automatic route.  ``requested_workflow`` is the explicit-choice
        # marker used by the API.  A direct ``workflow="guarded"`` call keeps
        # its legacy compatibility chain (including the intent_router step),
        # while still recording the inferred route for audit.
        await self._apply_intent_gate(bb, ctx)

        if existing_execution is not None and existing_execution.definition:
            wf = Workflow.model_validate(existing_execution.definition)
        else:
            wf = await self._resolve_workflow()

        if (
            existing_execution is None
            and self._execution_plan_input is None
            and self._requested_workflow is None
            and self._workflow_name is None
        ):
            from .agents.intent_router import INTENT_ROUTE_KEY

            raw_route = bb.scratch.get(INTENT_ROUTE_KEY)
            if isinstance(raw_route, Mapping) and raw_route.get("applied"):
                routed_name = raw_route.get("workflow")
                if isinstance(routed_name, str) and routed_name in WORKFLOWS:
                    wf = get_workflow(routed_name)
                    self._workflow_name = routed_name
                    self.tracer.emit(
                        "ORCHESTRATOR",
                        "info",
                        f"按意图选择工作流：{routed_name}",
                        data={
                            "event_name": "workflow.routed",
                            "workflow": routed_name,
                            "reason": raw_route.get("reason", ""),
                        },
                    )

        # The planner contract is a durable shadow of the existing workflow on
        # the first migration pass.  A checkpoint-provided plan always wins;
        # otherwise a persisted control plan is used only for recovery.  This
        # keeps a brand-new run from accidentally inheriting an old same-query
        # workspace while still making worker restarts deterministic.
        runtime_plan = None
        # External plans are compiled into workflow node IDs.  Keep the
        # mapping alongside the in-memory plan so checkpoint projection can
        # associate runtime outcomes by identity rather than by execution
        # order (DAG layers may complete in a different order than authored
        # steps).
        runtime_plan_step_mapping: dict[str, str] = {}
        if planner_mode:
            assert artifact_slug is not None
            # A supplied plan is authoritative for a fresh run.  On recovery,
            # prefer the fenced checkpoint snapshot so a caller cannot mutate
            # the plan halfway through execution.
            raw_plan = bb.scratch.get(PLAN_SCRATCH_KEY)
            if provided_plan is not None and self._resume_execution is None:
                runtime_plan = provided_plan
            elif isinstance(raw_plan, Mapping):
                runtime_plan = coerce_execution_plan(
                    raw_plan,
                    query=query,
                    initial=False,
                )
            elif existing_execution is not None and self._artifact_store is not None:
                runtime_plan = load_persisted_plan(self._artifact_store, artifact_slug)
            if runtime_plan is None:
                runtime_plan = build_execution_plan(
                    query,
                    wf,
                    slug=artifact_slug,
                    title=wf.description or wf.name,
                )
                runtime_plan.metadata["source"] = "workflow_projection"
            else:
                if provided_plan is not None:
                    runtime_plan.metadata.setdefault("source", "external")
                else:
                    runtime_plan.metadata.setdefault("source", "workflow_projection")
            # An explicitly supplied planner plan can become the workflow
            # source.  Shadow plans produced by this migration carry the
            # ``workflow_projection`` marker and intentionally keep the
            # authored legacy workflow (including reflect/team control steps).
            if (
                runtime_plan.metadata.get("source") not in {None, "workflow_projection"}
            ):
                from .orchestration import compile_plan
                from .registry import available

                compiled = compile_plan(
                    runtime_plan,
                    available_agents=set(available()),
                    skill_resolver=self._skill_resolver,
                )
                wf = compiled.workflow
                runtime_plan_step_mapping = dict(compiled.step_mapping)
            store_plan_in_blackboard(bb, runtime_plan)
            assert self._artifact_store is not None
            persist_plan(self._artifact_store, runtime_plan)
            manifest = self._artifact_store.load_manifest(artifact_slug)
            bb.scratch[ARTIFACT_MANIFEST_SCRATCH_KEY] = manifest.model_dump(mode="json")
            self.tracer.emit(
                "ORCHESTRATOR",
                "info",
                "execution plan ready",
                data={
                    "event_name": "execution.plan",
                    "slug": artifact_slug,
                    "step_count": len(runtime_plan.steps),
                    "steps": [step.id for step in runtime_plan.steps],
                    "recovered": existing_execution is not None,
                },
            )
        if RUN_CATALOG_CHECKPOINT_KEY not in bb.scratch and cr is not None:
            bb.scratch[RUN_CATALOG_CHECKPOINT_KEY] = cr.snapshot(
                workflow_catalog_roles(wf)
            ).model_dump(mode="json")

        if RUN_MANIFEST_CHECKPOINT_KEY not in bb.scratch:
            catalog_snapshot = bb.scratch.get(RUN_CATALOG_CHECKPOINT_KEY)
            catalog_profiles = (
                catalog_snapshot.get("profiles", []) if isinstance(catalog_snapshot, dict) else []
            )
            bb.scratch[RUN_MANIFEST_CHECKPOINT_KEY] = build_run_manifest(
                query=query,
                workflow_name=wf.name,
                workflow_definition=wf.model_dump(mode="json"),
                settings=checkpoint_settings(self.settings),
                llm_model=self.settings.llm_model,
                llm_endpoint=self.settings.llm_base_url,
                search_backend=getattr(
                    self.search_tool.delegate,
                    "backend_name",
                    type(self.search_tool.delegate).__name__,
                ),
                catalog_snapshot=catalog_snapshot,
                catalog_model_profiles=(
                    catalog_profiles if isinstance(catalog_profiles, list) else []
                ),
            ).model_dump(mode="json")

        if self.repo is not None and run_id is not None:

            async def save_source_snapshots(sources):  # type: ignore[no-untyped-def]
                await self.repo.save_sources(run_id, sources, lease_owner=self._lease_owner)

            self.search_tool.set_sink(save_source_snapshots)

            def record_snapshot_error(exc, sources):  # type: ignore[no-untyped-def]
                self.tracer.emit(
                    "RESEARCHER",
                    "error",
                    f"来源快照持久化失败，研究继续：{exc}",
                    data={
                        "category": "source_snapshot_persistence",
                        "source_count": len(sources),
                        "source_urls": [source.url for source in sources],
                    },
                )

            self.search_tool.set_error_sink(record_snapshot_error)

        async def save_checkpoint(execution):  # type: ignore[no-untyped-def]
            if planner_mode and runtime_plan is not None:
                # Mirror in-memory role outputs before persisting the fenced
                # checkpoint.  A storage error intentionally escapes here and
                # is classified as infrastructure failure by the outer run.
                assert self._artifact_store is not None
                attempt = getattr(execution, "attempt", None)
                output_paths = persist_blackboard_artifacts(
                    self._artifact_store,
                    artifact_slug,
                    bb,
                    attempt=attempt,
                )
                partial_ids: set[str] = set()
                runtime_steps = list(getattr(execution, "steps", []))
                node_to_plan = {
                    node_id: plan_id for plan_id, node_id in runtime_plan_step_mapping.items()
                }
                for runtime_index, runtime_step in enumerate(runtime_steps):
                    status = getattr(runtime_step.status, "value", runtime_step.status)
                    if status != "failed":
                        continue
                    # Research/reflect stages (and generic external-plan
                    # executors) can yield useful evidence even when one
                    # provider/model call is incomplete.  Keep that truthful
                    # distinction in the planner state; operation, storage,
                    # auth and scheduling errors remain failed.
                    role = str(getattr(runtime_step, "agent", ""))
                    useful_output = bool(
                        bb.results
                        or bb.plan is not None
                        or bb.report is not None
                        or output_paths
                    )
                    if (
                        role in {"researcher", "reflector", "planner", "plan_executor"}
                        and useful_output
                        and not _is_infrastructure_step_failure(runtime_step.error)
                    ):
                        plan_id = node_to_plan.get(str(getattr(runtime_step, "node_id", "")))
                        if plan_id is None and runtime_index < len(runtime_plan.steps):
                            # Legacy/projection workflows do not have a
                            # compiler mapping; retain the authored-index
                            # fallback for those checkpoints.
                            plan_id = runtime_plan.steps[runtime_index].id
                        if plan_id is not None:
                            partial_ids.add(plan_id)
                output_by_step: dict[str, list[str]] = {}
                for plan_step in runtime_plan.steps:
                    stage = str(
                        plan_step.metadata.get("workflow_agent")
                        or plan_step.metadata.get("workflow_kind")
                        or ""
                    ).lower().replace(" ", "-")
                    if stage:
                        output_by_step[plan_step.id] = [
                            path
                            for path in output_paths
                            if f"/{stage}/" in path
                        ]
                sync_plan_from_workflow(
                    runtime_plan,
                    execution,
                    step_mapping=runtime_plan_step_mapping or None,
                    output_paths_by_step=output_by_step,
                    partial_step_ids={item for item in partial_ids if item},
                )
                store_plan_in_blackboard(bb, runtime_plan)
                persist_plan(self._artifact_store, runtime_plan)
                # Keep a user-readable copy of the execution contract in the
                # work tree as well as the private control file.
                self._artifact_store.write_text(
                    artifact_slug,
                    "planner",
                    "execution-plan.json",
                    plan_json(runtime_plan),
                    mime_type="application/json",
                    attempt=attempt,
                )
                snapshot = project_blackboard(
                    self._artifact_store,
                    artifact_slug,
                    query=bb.query,
                    results_count=len(bb.results),
                    reflection_count=len(bb.reflections),
                    report_markdown=bb.report.markdown if bb.report is not None else None,
                    attempt=attempt,
                )
                bb.scratch[ARTIFACT_MANIFEST_SCRATCH_KEY] = snapshot.model_dump(mode="json")
                # ``execution.checkpoint`` was built before this sink ran;
                # replace it with the enriched scratch snapshot so the plan
                # and manifest survive a process restart.
                execution.checkpoint = bb.model_dump(mode="json")
            if self.repo is not None and run_id is not None:
                await self.repo.save_orchestration(run_id, execution, lease_owner=self._lease_owner)
                await self._flush_events(run_id)

        runtime_terminal_roles: set[str] | None = (
            set(cr.terminal_roles) if cr is not None else None
        )
        if (
            runtime_plan is not None
            and runtime_plan.metadata.get("source") == "external"
            and any(
                step.agent in {"plan_executor", "operation_runner"}
                for step in (
                    [Step.model_validate(node["step"]) for node in wf.nodes]
                    if wf.nodes
                    else wf.steps
                )
            )
        ):
            if runtime_terminal_roles is None:
                runtime_terminal_roles = {"synthesizer", "aggregator"}
            runtime_terminal_roles.update({"plan_executor", "operation_runner"})
        engine = WorkflowEngine(
            ctx,
            resolver=cr.resolve_agent if cr is not None else None,
            budget=budget,
            checkpoint_sink=save_checkpoint,
            resume_run=self._resume_execution,
            initial_run=self._initial_execution,
            require_report=True,
            terminal_roles=runtime_terminal_roles,
        )
        if wf.nodes:
            errors = validate_workflow_graph_terminal(
                wf.nodes,
                wf.edges,
                terminal_roles=runtime_terminal_roles,
            )
            if errors:
                raise ValueError("工作流不合法：" + "；".join(errors))
        elif wf.name not in WORKFLOWS:
            errors = validate_workflow_steps_terminal(
                wf.steps,
                terminal_roles=runtime_terminal_roles,
            )
            if errors:
                raise ValueError("工作流不合法：" + "；".join(errors))
        await engine.run(wf, bb)

        if bb.report is None:  # WorkflowEngine(require_report=True) should have raised first.
            raise RuntimeError("工作流结束但未生成报告")
        report = bb.report
        if self.repo is not None and run_id is not None:
            if engine.runtime.run is not None:
                await self.repo.save_orchestration(
                    run_id, engine.runtime.run, lease_owner=self._lease_owner
                )
            reflection_rounds: list[tuple[int, list[SubQuestion]]] = []
            for raw_round in bb.scratch.get("reflection_rounds", []):
                if not isinstance(raw_round, dict):
                    continue
                sub_questions = [
                    SubQuestion.model_validate(item) for item in raw_round.get("sub_questions", [])
                ]
                reflection_rounds.append((int(raw_round.get("round", 0)), sub_questions))
            # The final derived artifacts are one unit. Replacing them in one
            # transaction makes recovery safe after any earlier partial write.
            await self.repo.replace_artifacts(
                run_id,
                plan=bb.plan,
                reflection_rounds=reflection_rounds,
                results=bb.results,
                report=report,
                lease_owner=self._lease_owner,
            )
        return report

    async def run_stream(self, query: str) -> AsyncIterator[Event]:
        """以事件流方式运行，供 SSE 实时推送。"""
        self._claim_run()
        queue: asyncio.Queue[Event] = asyncio.Queue()
        sink = queue.put_nowait  # 存引用，便于 finally 精确移除
        self.tracer.add_sink(sink)
        task = asyncio.create_task(self._run_once(query))
        finished = False  # 是否走到 ORCHESTRATOR 终态（区别于客户端中途断连）
        try:
            while True:
                event = await queue.get()
                yield event
                # 只有 ORCHESTRATOR 的 done/error 才是运行终态；
                # RESEARCHER 等阶段的 error 是被隔离的单点失败，运行仍在继续
                if event.stage == "ORCHESTRATOR" and event.type in ("done", "error"):
                    finished = True
                    break
        finally:
            self.tracer.remove_sink(sink)
            if not finished and not task.done():
                task.cancel()  # 客户端断连：停止研究，避免无人消费仍持续烧 LLM/检索
            try:
                await task  # 回收任务并取出可能的异常，避免 "never retrieved" 警告
            except (Exception, asyncio.CancelledError):
                pass
