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
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel

from .agents import Planner, Reflector, Researcher, Synthesizer  # noqa: F401 触发角色注册
from .agents.base import Blackboard, RunContext
from .config import Settings
from .llm import LLM
from .models import Finding, Report, ResearchResult, SubQuestion
from .observability import Event, Tracer
from .orchestration import OrchestrationRuntime, WorkflowRun
from .persistence.repository import LeaseLostError, ResearchRepository
from .reproducibility import (
    RUN_MANIFEST_CHECKPOINT_KEY,
    RecordingSearchTool,
    build_run_manifest,
)
from .scheduler import research_dag
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


RUN_SETTINGS_CHECKPOINT_KEY = "_run_settings"
RUN_METRICS_CHECKPOINT_KEY = "_runtime_metrics"
RUN_CATALOG_CHECKPOINT_KEY = "_catalog_runtime"
_RUN_SETTING_FIELDS = (
    "max_sub_questions",
    "max_rounds",
    "max_concurrency",
    "results_per_search",
    "require_corroboration",
    "max_tokens",
    "max_replans",
    "request_timeout",
    "max_run_seconds",
)


def checkpoint_settings(settings: Settings) -> dict[str, bool | int | float | None]:
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
    query: str, workflow_name: str | None, settings: Settings
) -> WorkflowRun:
    """Create the durable, leased checkpoint used before a background task starts."""
    runtime = OrchestrationRuntime()
    execution = runtime.start(workflow_name or "deep", {"query": query})
    execution.checkpoint = Blackboard(
        query=query,
        scratch={RUN_SETTINGS_CHECKPOINT_KEY: checkpoint_settings(settings)},
    ).model_dump(mode="json")
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
        的流程（``guarded``）里生效，而 ``/api/research`` 快路径与 CLI 都不走那条
        流程，攻击请求会拿到一份正常报告。把安全属性挂在「用户/路由恰好选中了
        某条工作流」上，等于让它可被绕过——安全门禁必须在**所有执行路径的交汇点**
        上，而 ``_run_workflow`` 正是这个点。

        **为什么不把 intent_router 插进 wf.steps**：引擎按位置 ``step-{i+1}``
        匹配 checkpoint 做断点续跑，往前面插一步会让所有既有 run 的步骤编号错位，
        恢复时张冠李戴。这里改为在引擎之外跑，通过既有的 halt 原语终止——
        halt 本来就是为「角色请求提前终止」设计的通用机制。

        ``guarded`` 流程仍保留 ``intent_router`` 步骤：它是**显式声明**，
        且角色见到已有判定会复用而不重判，因此不会双重付费。
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
        # 让 IntentRouter 知道用户是否显式选了工作流：显式选择优先于意图路由。
        # 注意用的是 _requested_workflow 而非 _workflow_name——后者可能是意图
        # 预路由推断出来的结果，拿它当「显式选择」会让路由永久自我禁用。
        if self._requested_workflow:
            bb.scratch.setdefault("requested_workflow", self._requested_workflow)
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
        )
        budget = TokenBudget(max_tokens=self.settings.max_tokens)

        if existing_execution is not None and existing_execution.definition:
            wf = Workflow.model_validate(existing_execution.definition)
        else:
            wf = await self._resolve_workflow()
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
            if self.repo is not None and run_id is not None:
                await self.repo.save_orchestration(run_id, execution, lease_owner=self._lease_owner)
                await self._flush_events(run_id)

        engine = WorkflowEngine(
            ctx,
            resolver=cr.resolve_agent if cr is not None else None,
            budget=budget,
            checkpoint_sink=save_checkpoint,
            resume_run=self._resume_execution,
            initial_run=self._initial_execution,
            require_report=True,
            terminal_roles=cr.terminal_roles if cr is not None else None,
        )
        if wf.nodes:
            errors = validate_workflow_graph_terminal(
                wf.nodes,
                wf.edges,
                terminal_roles=cr.terminal_roles if cr is not None else None,
            )
            if errors:
                raise ValueError("工作流不合法：" + "；".join(errors))
        elif wf.name not in WORKFLOWS:
            errors = validate_workflow_steps_terminal(
                wf.steps,
                terminal_roles=cr.terminal_roles if cr is not None else None,
            )
            if errors:
                raise ValueError("工作流不合法：" + "；".join(errors))
        await self._apply_intent_gate(bb, ctx)
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
