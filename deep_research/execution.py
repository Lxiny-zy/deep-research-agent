"""执行一次研究：与 HTTP 传输解耦的后台执行核心。

这里的代码原先内联在 ``api.py`` 中，只能在 FastAPI 请求进程内运行。抽出来之后
同一份实现同时服务两种执行拓扑：

* ``execution_mode=inline``：API 进程自己创建 asyncio task 执行（默认，桌面版依赖）；
* ``execution_mode=worker``：独立的 ``python -m deep_research.worker`` 进程领取执行。

两种拓扑共用同一套租约续期、取消轮询、checkpoint 续跑与资源清理语义——差别只在
「谁来调用 :meth:`RunExecutor.execute`」，不在任务本身如何执行。

``ExecutionContext`` 刻意只暴露执行真正需要的三样东西（仓储 / 角色目录 / 本地事件
中心），而不是整个 ``app.state``：worker 侧没有 FastAPI 应用可传。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .artifacts import ArtifactStore
from .config import Settings
from .observability import Event, EventHub
from .orchestration import WorkflowRun
from .orchestrator import RUN_SETTINGS_CHECKPOINT_KEY, DeepResearchAgent
from .persistence.repository import ResearchRepository
from .planning import stable_slug
from .runner import CommandRunner
from .security import validate_provider_url_resolved
from .skills import SkillResolver, default_skill_resolver
from .tools.base import SearchTool

logger = logging.getLogger(__name__)

_LEASE_RENEW_INTERVAL_SECONDS = 60.0
_CANCEL_POLL_SECONDS = 0.5

# 允许从 checkpoint 还原的 per-run 行为参数。密钥与端点**不在**其中：它们来自
# 当前进程配置，恢复一个旧 run 不应复活一份旧凭据。
_CHECKPOINT_SETTING_FIELDS = {
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
}


def settings_for_resume(base: Settings, execution: WorkflowRun) -> Settings:
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


@dataclass
class ExecutionContext:
    """执行一次研究所需的进程级依赖。

    ``live`` 是本进程的 SSE 加速层，不是正确性依赖：事件同时落库，跨进程订阅者
    经仓储读取（见 ``api._stream_run_sse`` 的 durable tail 分支）。worker 进程没有
    订阅者，传入空字典即可。
    """

    repo: ResearchRepository
    catalog: Any | None = None
    live: dict[str, EventHub] = field(default_factory=dict)
    # Planner/artifact capabilities are initialized lazily by ``RunExecutor``
    # so an explicitly legacy deployment can still avoid artifact folders and
    # subprocess infrastructure.
    artifact_store: ArtifactStore | None = None
    command_runner: CommandRunner | None = None
    skill_resolver: SkillResolver | None = None

    def close_hub(self, run_id: str, hub: EventHub | None = None) -> None:
        """Close a run's SSE hub and remove only that exact registration."""
        target = hub if hub is not None else self.live.get(run_id)
        if target is None:
            return
        target.close()
        if self.live.get(run_id) is target:
            self.live.pop(run_id, None)


async def validate_runtime_provider_url(settings: Settings) -> None:
    await validate_provider_url_resolved(
        settings.llm_base_url,
        allow_private=settings.allow_private_provider_urls,
        allowlist=settings.provider_host_allowlist,
    )


# --- Chaos 演示注入钩子（仅供演示/测试，见 scripts/chaos_demo.py） -----------------
# 设 DR_DEMO_FAKE_BACKENDS=1 后，build_agent 改用仓库内 tests/fakes 的假 LLM/检索：
# 完全离线运行，并按 DR_DEMO_STEP_DELAY 秒放慢每次后端调用（让 kill -9 能精确落在
# 中间步骤）、按 DR_DEMO_TOKENS_PER_CALL 计入模拟 token（度量断点续跑节省占比）。
# 未设置该环境变量时本钩子完全不生效，生产/默认行为零变化。


def demo_fake_backends_enabled() -> bool:
    return os.environ.get("DR_DEMO_FAKE_BACKENDS", "").strip().lower() in {"1", "true", "yes"}


def build_demo_backends() -> tuple[Any, SearchTool]:
    """构造「放慢 + 计量」的离线假后端；仅在 DR_DEMO_FAKE_BACKENDS=1 时被调用。"""
    import importlib

    fakes = importlib.import_module("tests.fakes")  # 演示需从仓库根目录启动服务
    delay = float(os.environ.get("DR_DEMO_STEP_DELAY", "2.0") or 0.0)
    tokens_per_call = int(os.environ.get("DR_DEMO_TOKENS_PER_CALL", "1000") or 0)

    class _PacedFakeLLM:
        """包装 FakeLLM：每次调用先 sleep 模拟真实延迟，并向 tracer 计入模拟 token。"""

        def __init__(self) -> None:
            self._inner = fakes.FakeLLM()
            self.tracer: Any = None  # 由 build_agent 在 agent 构造后回填

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


class RunExecutor:
    """执行一次持久化研究运行。

    与传输层无关：调用方负责决定何时执行（HTTP 请求内派发 / worker 领取），
    以及并发准入。本类只负责「拿到 run_id 之后到终态之前」的全部语义。
    """

    def __init__(self, ctx: ExecutionContext) -> None:
        self.ctx = ctx

    async def build_search_tool(self, settings: Settings) -> SearchTool | None:
        """按配置组装检索后端。

        返回 ``None`` 表示交给 ``DeepResearchAgent`` 用 ``settings.tavily_api_key``
        自建默认单后端——这是既有行为，只有显式配置了 key 池或多后端时才接管。
        """
        backends: list[SearchTool] = []
        tavily = await self._build_tavily(settings)
        if tavily is not None:
            backends.append(tavily)
        if "brave" in settings.search_backends and settings.brave_api_key:
            from .tools.brave_search import BraveSearch

            backends.append(BraveSearch(settings.brave_api_key))
        elif "brave" in settings.search_backends:
            logger.warning("检索后端 brave 已启用但缺少 BRAVE_API_KEY，本次跳过该后端")
        # 学术源不需要密钥，因此没有「缺 key 退化」这条分支：启用即可用。
        # OPENALEX_MAILTO 只影响配额档位（礼貌池），缺失不影响可用性。
        if "openalex" in settings.search_backends:
            from .tools.openalex import OpenAlexSearch

            backends.append(
                OpenAlexSearch(
                    mailto=settings.openalex_mailto,
                    fulltext=settings.fulltext_enabled,
                    fulltext_max_chars=settings.fulltext_max_chars,
                    timeout=settings.request_timeout,
                )
            )
        if "arxiv" in settings.search_backends:
            from .tools.arxiv_search import ArxivSearch

            backends.append(
                ArxivSearch(
                    fulltext=settings.fulltext_enabled,
                    fulltext_max_chars=settings.fulltext_max_chars,
                    timeout=settings.request_timeout,
                )
            )

        if not backends:
            return None
        if len(backends) == 1:
            return backends[0]
        from .tools.composite import MultiBackendSearch

        return MultiBackendSearch(backends)

    async def _build_tavily(self, settings: Settings) -> SearchTool | None:
        """优先用搜索 key 池（主备故障转移）；池为空则回退到全局单 key。"""
        if "tavily" not in settings.search_backends:
            return None
        keys: list[str] = []
        catalog = self.ctx.catalog
        if catalog is not None:
            try:
                keys = await catalog.active_keys()
            except Exception:
                logger.exception("读取搜索 key 池失败，回退单 key")
        if keys:
            from .tools.tavily_pool import TavilyKeyPoolSearch

            return TavilyKeyPoolSearch(keys)
        # None＝让 DeepResearchAgent 用 settings.tavily_api_key 自建（保持既有行为）；
        # 但只有在没有其他后端时这个「交还」才成立，因此由调用方决定。
        if settings.tavily_api_key and len(settings.search_backends) > 1:
            from .tools.tavily_search import TavilySearch

            return TavilySearch(settings.tavily_api_key)
        return None

    async def build_agent(
        self, settings: Settings, **agent_kwargs: object
    ) -> tuple[DeepResearchAgent, SearchTool | None]:
        """统一 agent 构造：为持久化执行注入搜索 key 池与 catalog。

        注入的 search_tool 不归 agent 所有（aclose 不会关它），调用方须负责关闭。
        """
        if demo_fake_backends_enabled():  # 仅供演示/测试：见上方钩子注释
            demo_llm, demo_search = build_demo_backends()
            agent = DeepResearchAgent(
                settings,
                catalog_repo=self.ctx.catalog,
                llm=demo_llm,
                search_tool=demo_search,
                **agent_kwargs,  # type: ignore[arg-type]  # 与下方既有 **agent_kwargs 模式一致
            )
            demo_llm.tracer = agent.tracer
            return agent, demo_search
        await validate_runtime_provider_url(settings)
        search_tool = await self.build_search_tool(settings)
        try:
            agent = DeepResearchAgent(
                settings,
                catalog_repo=self.ctx.catalog,
                search_tool=search_tool,
                **agent_kwargs,  # type: ignore[arg-type]
            )
        except BaseException:
            # 构造期异常（缺 key 等）时归还 search client，避免连接池泄漏
            if search_tool is not None:
                await search_tool.aclose()
            raise
        return agent, search_tool

    async def execute(
        self,
        run_id: str,
        query: str,
        settings: Settings,
        workflow: str | None = None,
        resume_execution: WorkflowRun | None = None,
        lease_owner: str | None = None,
        initial_execution: WorkflowRun | None = None,
        requested_workflow: str | None = None,
        execution_plan: object | None = None,
    ) -> None:
        """后台执行一次研究：事件经 EventHub 实时扇出给 SSE 订阅者，全程落库。"""
        ctx = self.ctx
        hub: EventHub | None = ctx.live.get(run_id)
        if hub is None:
            # A task must still release its lease and close subscribers if setup
            # raced with cancellation or a process-level state reset.
            hub = EventHub()
            ctx.live[run_id] = hub
        agent: DeepResearchAgent | None = None
        heartbeat: asyncio.Task[None] | None = None
        search_tool: SearchTool | None = None

        async def persist_cancellation() -> None:
            event = Event(
                stage="ORCHESTRATOR",
                type="cancelled",
                message="运行已取消",
                data={"status": "cancelled"},
            )
            hub.publish(event)
            try:
                await ctx.repo.append_events(run_id, [event], lease_owner=lease_owner)
            finally:
                await ctx.repo.set_status(run_id, "cancelled", lease_owner=lease_owner)

        try:
            artifact_store: ArtifactStore | None = self.ctx.artifact_store
            command_runner: CommandRunner | None = self.ctx.command_runner
            skill_resolver: SkillResolver | None = self.ctx.skill_resolver
            if settings.orchestration_mode != "legacy":
                if artifact_store is None:
                    artifact_store = ArtifactStore(
                        Path(settings.artifact_root),
                        max_bytes=settings.artifact_max_bytes,
                    )
                    self.ctx.artifact_store = artifact_store
                if settings.runner_enabled and command_runner is None:
                    command_runner = CommandRunner(
                        workspace_root=artifact_store.workspace_root,
                        allowed_operations=settings.runner_allowed_operations,
                        default_timeout_seconds=settings.runner_default_timeout,
                        max_output_bytes=settings.runner_max_output_bytes,
                        max_processes=settings.runner_max_processes,
                    )
                    self.ctx.command_runner = command_runner
                if skill_resolver is None:
                    skill_resolver = default_skill_resolver(Path.cwd())
                    self.ctx.skill_resolver = skill_resolver
            artifact_slug = None
            if settings.orchestration_mode != "legacy":
                source_execution = resume_execution or initial_execution
                checkpoint = source_execution.checkpoint if source_execution is not None else {}
                scratch = checkpoint.get("scratch", {}) if isinstance(checkpoint, dict) else {}
                if isinstance(scratch, dict) and isinstance(scratch.get("_artifact_slug"), str):
                    artifact_slug = scratch["_artifact_slug"]
                artifact_slug = artifact_slug or stable_slug(query)
            if lease_owner is not None:
                execution_task = asyncio.current_task()

                async def monitor_execution() -> None:
                    next_renewal = time.monotonic() + _LEASE_RENEW_INTERVAL_SECONDS
                    while True:
                        await asyncio.sleep(
                            max(
                                0.0,
                                min(_CANCEL_POLL_SECONDS, next_renewal - time.monotonic()),
                            )
                        )
                        get_status = getattr(ctx.repo, "get_run_status", None)
                        if get_status is not None:
                            try:
                                status = await get_status(run_id)
                            except asyncio.CancelledError:
                                raise
                            except Exception:
                                logger.exception("run %s cancellation poll failed", run_id)
                            else:
                                if status == "cancelling":
                                    if execution_task is not None:
                                        execution_task.cancel()
                                    return
                        if time.monotonic() < next_renewal:
                            continue
                        try:
                            renewed = await ctx.repo.renew_lease(run_id, lease_owner)
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            logger.exception("run %s lease renewal failed", run_id)
                            renewed = False
                        if renewed:
                            next_renewal = time.monotonic() + _LEASE_RENEW_INTERVAL_SECONDS
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

                heartbeat_coro = monitor_execution()
                try:
                    heartbeat = asyncio.create_task(heartbeat_coro)
                except BaseException:
                    heartbeat_coro.close()
                    raise
            # 构造必须在 try 内：缺 API key 等构造期异常同样要走 finally 收尾，
            # 否则 EventHub 泄漏、SSE 订阅者永久挂起、run 卡死在 pending
            agent, search_tool = await self.build_agent(
                settings,
                repo=ctx.repo,
                run_id=run_id,
                workflow=workflow,
                requested_workflow=requested_workflow,
                resume_execution=resume_execution,
                initial_execution=initial_execution,
                lease_owner=lease_owner,
                artifact_store=artifact_store,
                command_runner=command_runner,
                skill_resolver=skill_resolver,
                artifact_slug=artifact_slug,
                execution_plan=execution_plan,
            )
            if resume_execution is not None:
                # Replay useful prior progress locally, but never put historical
                # events back into the new tracer: append-only persistence keeps
                # attempts distinct and must not duplicate prior records.
                historical_events = await ctx.repo.get_events(run_id)
                hub.prime_sequence(historical_events)
                replayable = [
                    event
                    for event in historical_events
                    if not (
                        event.stage == "ORCHESTRATOR"
                        and event.type in {"done", "error", "cancelled"}
                    )
                ]
                for historical_event in replayable:
                    hub.publish(historical_event)
            agent.tracer.add_sink(hub.publish)
            async with asyncio.timeout(settings.max_run_seconds):
                await agent.run(query)
            status_reader = getattr(ctx.repo, "get_run_status", None)
            if status_reader is not None and await status_reader(run_id) == "cancelling":
                await persist_cancellation()
        except asyncio.CancelledError:
            # User cancellation is durable and terminal.  Process shutdown and
            # lease fencing leave the active state untouched so recovery can resume.
            get_status = getattr(ctx.repo, "get_run_status", None)
            status = await get_status(run_id) if get_status is not None else None
            if status == "cancelling":
                try:
                    await persist_cancellation()
                except Exception:
                    logger.exception("run %s failed to persist cancellation", run_id)
                return
            raise
        except TimeoutError:
            event = Event(
                stage="ORCHESTRATOR",
                type="error",
                message=f"运行超过 {settings.max_run_seconds} 秒期限，已终止",
                data={"status": "error", "reason": "deadline_exceeded"},
            )
            hub.publish(event)
            try:
                await ctx.repo.append_events(run_id, [event], lease_owner=lease_owner)
            except Exception:
                logger.exception("run %s failed to persist deadline event", run_id)
            try:
                await ctx.repo.set_status(run_id, "error", lease_owner=lease_owner)
            except Exception:
                logger.exception("run %s failed to persist deadline status", run_id)
        except Exception:
            # run() 内部正常路径已 emit error 事件并置 status=error；
            # 落到这里的是构造期异常或落库自身失败，必须留痕并兜底状态
            logger.exception("run %s 执行失败", run_id)
            event = Event(stage="ORCHESTRATOR", type="error", message="服务器内部错误，运行已终止")
            hub.publish(event)
            try:
                await ctx.repo.append_events(run_id, [event], lease_owner=lease_owner)
            except Exception:
                logger.exception("run %s 兜底事件落库失败", run_id)
            try:
                await ctx.repo.set_status(run_id, "error", lease_owner=lease_owner)
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
                        await ctx.repo.release_lease(run_id, lease_owner)
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
            cleanup_coro = cleanup_resources()
            try:
                cleanup_task = asyncio.create_task(cleanup_coro)
            except BaseException:
                # Event-loop shutdown or an injected scheduler failure must not
                # skip lease/client cleanup. Run it inline as the last resort.
                logger.exception("run %s failed to schedule resource cleanup", run_id)
                cleanup_coro.close()
                await cleanup_resources()
                ctx.close_hub(run_id, hub)
                raise
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
                ctx.close_hub(run_id, hub)
            if cleanup_interrupted:
                raise asyncio.CancelledError
