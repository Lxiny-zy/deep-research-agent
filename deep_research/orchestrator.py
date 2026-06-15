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
from collections.abc import AsyncIterator

from .agents import Planner, Reflector, Researcher, Synthesizer  # noqa: F401 触发角色注册
from .agents.base import Blackboard, RunContext
from .config import Settings
from .llm import LLM
from .models import Finding, Report, ResearchResult, SubQuestion
from .observability import Event, Tracer
from .persistence.repository import ResearchRepository
from .scheduler import research_dag
from .tools.base import SearchTool
from .workflow import WorkflowEngine
from .workflows import get_workflow


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
    ) -> None:
        self.settings = settings
        self.tracer = Tracer()
        self.repo = repo
        self._run_id = run_id
        self._workflow_name = workflow

        # 依赖注入：测试时传入假的 LLM / 检索工具，无需真实密钥与网络
        if llm is None or search_tool is None:
            settings.validate()
        self._owns_llm = llm is None  # 自建的 client 由本实例负责关闭；注入的归调用方管
        self.llm = llm or LLM(settings, self.tracer)
        if search_tool is None:
            from .tools.tavily_search import TavilySearch  # 延迟导入，避免无谓依赖

            search_tool = TavilySearch(settings.tavily_api_key)
        self.search_tool = search_tool

        # 仍构造具名角色实例：向后兼容（测试替换 self.researcher、直接调用各角色）
        self.planner = Planner(self.llm, self.tracer, settings)
        self.researcher = Researcher(self.llm, self.search_tool, self.tracer, settings)
        self.reflector = Reflector(self.llm, self.tracer, settings)
        self.synthesizer = Synthesizer(self.llm, self.tracer, settings)
        self._sem = asyncio.Semaphore(settings.max_concurrency)

    async def aclose(self) -> None:
        """释放自建 LLM client 的底层 HTTP 连接池。注入的 client 归调用方管。"""
        if self._owns_llm:
            await self.llm.aclose()

    async def _research_one(
        self, question: str, context_findings: list[Finding] | None = None
    ) -> ResearchResult | None:
        async with self._sem:  # 限流，避免打爆检索 API
            return await self.researcher.run(question, context_findings=context_findings)

    async def _research_dag(self, sub_questions: list[SubQuestion]) -> list[ResearchResult]:
        """按依赖拓扑分层并行检索（保留为公开方法：测试替换 self.researcher 后直接调用）。"""
        return await research_dag(sub_questions, self._research_one, self.tracer)

    async def run(self, query: str) -> Report:
        run_id = self._run_id
        try:
            if self.repo is not None:
                if run_id is None:
                    run_id = await self.repo.create_run(query)
                await self.repo.set_status(run_id, "running")

            self.tracer.emit("ORCHESTRATOR", "start", f"开始深度研究：{query}")

            report = await self._run_workflow(query, run_id)

            self.tracer.emit("ORCHESTRATOR", "report", "报告生成完成", data=report.model_dump())

            # 先落库再发 done：客户端收到 done 后立刻读详情，必须能读到完整数据
            if self.repo is not None and run_id is not None:
                await self.repo.finalize(
                    run_id, elapsed=self.tracer.elapsed, total_tokens=self.tracer.total_tokens
                )
            self.tracer.emit(
                "ORCHESTRATOR",
                "done",
                f"完成 ｜ 用时 {self.tracer.elapsed:.1f}s ｜ token {self.tracer.total_tokens}",
                data={
                    "elapsed": round(self.tracer.elapsed, 1),
                    "total_tokens": self.tracer.total_tokens,
                    "sources": len(report.citations),
                },
            )
            return report
        except Exception as e:
            self.tracer.emit("ORCHESTRATOR", "error", f"运行失败：{e}")
            if self.repo is not None and run_id is not None:
                await self.repo.set_status(run_id, "error")
            raise
        finally:
            # run 结束（含异常）后一次性落库全部非 token 事件，seq 即顺序，保证回放有序
            if self.repo is not None and run_id is not None:
                await self.repo.save_events(run_id, self.tracer.events)

    async def _run_workflow(self, query: str, run_id: str | None) -> Report:
        """组装上下文、执行工作流，并在产出关键里程碑时落库（计划/结果/报告）。

        落库经黑板的「持久化钩子」在引擎步骤之间触发，保证语义与重构前一致：
        计划生成即落库、研究结果产出即落库、报告生成即落库。
        """
        ctx = RunContext(
            llm=self.llm,
            search_tool=self.search_tool,
            tracer=self.tracer,
            settings=self.settings,
        )
        bb = Blackboard(query=query)
        engine = WorkflowEngine(ctx)
        wf = get_workflow(self._workflow_name)
        await engine.run(wf, bb)

        report = bb.report or Report(query=query, markdown="（未生成报告）", citations=[])
        if self.repo is not None and run_id is not None:
            if bb.plan is not None:
                await self.repo.save_plan(run_id, bb.plan)
            # 回放反思补洞轮次（origin="reflection"），保留重构前的落库语义
            for rnd in bb.scratch.get("reflection_rounds", []):
                await self.repo.add_sub_questions(
                    run_id, rnd["sub_questions"], origin="reflection", round=rnd["round"]
                )
            for r in bb.results:
                await self.repo.save_result(run_id, r)
            await self.repo.save_report(run_id, report)
        return report

    async def run_stream(self, query: str) -> AsyncIterator[Event]:
        """以事件流方式运行，供 SSE 实时推送。"""
        queue: asyncio.Queue[Event] = asyncio.Queue()
        sink = queue.put_nowait  # 存引用，便于 finally 精确移除
        self.tracer.add_sink(sink)
        task = asyncio.create_task(self.run(query))
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
