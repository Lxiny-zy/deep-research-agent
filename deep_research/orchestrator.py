"""编排器：把 Planner → Researcher(按依赖 DAG 调度) → Reflector(循环) → Synthesizer 串起来。

两种运行方式：
  - run(query)        ：跑完返回 Report（CLI / 评估用）
  - run_stream(query) ：以事件流方式产出，供 FastAPI 用 SSE 实时推送（Web Demo 用）

可选注入 repo（ResearchRepository）：注入后把运行全过程落库（计划/结果/报告/事件）；
repo=None 时行为与无持久化完全一致（CLI / eval / 离线单测）。可传入外部 run_id
（API 先 create_run 拿到 id 再后台执行），否则运行时自行创建。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from .agents import Planner, Reflector, Researcher, Synthesizer
from .config import Settings
from .dag import build_dag, detect_cycle, topo_layers
from .llm import LLM
from .models import Finding, Report, ResearchResult, SubQuestion
from .observability import Event, Tracer
from .persistence.repository import ResearchRepository
from .tools.base import SearchTool


class DeepResearchAgent:
    def __init__(
        self,
        settings: Settings,
        *,
        llm: LLM | None = None,
        search_tool: SearchTool | None = None,
        repo: ResearchRepository | None = None,
        run_id: str | None = None,
    ) -> None:
        self.settings = settings
        self.tracer = Tracer()
        self.repo = repo
        self._run_id = run_id

        # 依赖注入：测试时传入假的 LLM / 检索工具，无需真实密钥与网络
        if llm is None or search_tool is None:
            settings.validate()
        self._owns_llm = llm is None  # 自建的 client 由本实例负责关闭；注入的归调用方管
        self.llm = llm or LLM(settings, self.tracer)
        if search_tool is None:
            from .tools.tavily_search import TavilySearch  # 延迟导入，避免无谓依赖

            search_tool = TavilySearch(settings.tavily_api_key)
        self.search_tool = search_tool

        self.planner = Planner(self.llm, self.tracer, settings)
        self.researcher = Researcher(self.llm, self.search_tool, self.tracer, settings)
        self.reflector = Reflector(self.llm, self.tracer, settings)
        self.synthesizer = Synthesizer(self.llm, self.tracer, settings)
        self._sem = asyncio.Semaphore(settings.max_concurrency)

    async def aclose(self) -> None:
        """释放自建 LLM client 的底层 HTTP 连接池（每个 run 一个 agent，不关则只能靠 GC）。

        Tavily 的 AsyncTavilyClient 按请求创建连接，无持久资源需要释放。
        """
        if self._owns_llm:
            await self.llm.aclose()

    async def _research_one(
        self, question: str, context_findings: list[Finding] | None = None
    ) -> ResearchResult | None:
        async with self._sem:  # 限流，避免打爆检索 API
            return await self.researcher.run(question, context_findings=context_findings)

    async def _research_dag(self, sub_questions: list[SubQuestion]) -> list[ResearchResult]:
        """按依赖拓扑分层并行检索：同层并发，层间串行（后层可用前层发现作上下文）。

        无依赖时退化为单层全并行，行为与纯并行 fan-out 等价。
        """
        if not sub_questions:
            return []

        dag = build_dag([sq.depends_on for sq in sub_questions])
        cycle = detect_cycle(dag)
        if cycle:  # 破环降级为纯并行，保证永不死锁
            self.tracer.emit(
                "ORCHESTRATOR", "info", f"子问题依赖存在环（{cycle}），降级为纯并行检索"
            )
            dag = {i: [] for i in dag}

        layers = topo_layers(dag)
        if len(layers) > 1:
            self.tracer.emit(
                "ORCHESTRATOR",
                "info",
                f"按依赖分 {len(layers)} 层调度（每层 {[len(x) for x in layers]} 个）",
                data={"dag": {"layers": layers, "deps": {str(i): dag[i] for i in dag}}},
            )

        collected: dict[int, ResearchResult] = {}

        async def _run_idx(i: int) -> tuple[int, ResearchResult | None]:
            # 汇集前驱已得到的发现，作为本子问题的背景上下文
            ctx: list[Finding] = []
            for p in dag[i]:
                prev = collected.get(p)
                if prev:
                    ctx.extend(prev.findings)
            res = await self._research_one(sub_questions[i].question, ctx or None)
            return i, res

        for layer in layers:
            layer_out = await asyncio.gather(*[_run_idx(i) for i in layer], return_exceptions=True)
            for item in layer_out:
                if isinstance(item, asyncio.CancelledError):
                    raise item  # 取消必须向上传播，不可吞
                if isinstance(item, BaseException):
                    # Researcher 内部已兜底常规失败，落到这里的是未预期异常：
                    # 记录而非静默丢弃，否则该子问题会"无痕消失"，极难排查
                    self.tracer.emit("RESEARCHER", "error", f"子问题执行出现未预期异常：{item}")
                    continue
                idx, res = item
                if isinstance(res, ResearchResult) and res.findings:
                    collected[idx] = res

        return [collected[i] for i in sorted(collected)]

    async def run(self, query: str) -> Report:
        run_id = self._run_id
        try:
            if self.repo is not None:
                if run_id is None:
                    run_id = await self.repo.create_run(query)
                await self.repo.set_status(run_id, "running")

            self.tracer.emit("ORCHESTRATOR", "start", f"开始深度研究：{query}")

            plan = await self.planner.run(query)
            if self.repo is not None and run_id is not None:
                await self.repo.save_plan(run_id, plan)

            results = await self._research_dag(plan.sub_questions)

            for rnd in range(self.settings.max_rounds):
                reflection = await self.reflector.run(query, results)
                if reflection.is_sufficient or not reflection.new_sub_questions:
                    break
                self.tracer.emit("ORCHESTRATOR", "round", f"第 {rnd + 1} 轮补洞")
                new_subs = [SubQuestion(question=q) for q in reflection.new_sub_questions]
                if self.repo is not None and run_id is not None:
                    await self.repo.add_sub_questions(
                        run_id, new_subs, origin="reflection", round=rnd + 1
                    )
                results += await self._research_dag(new_subs)

            report = await self.synthesizer.run(query, results)
            self.tracer.emit("ORCHESTRATOR", "report", "报告生成完成", data=report.model_dump())

            # 先落库再发 done：客户端收到 done 后立刻读详情，必须能读到完整数据
            if self.repo is not None and run_id is not None:
                for r in results:
                    await self.repo.save_result(run_id, r)
                await self.repo.save_report(run_id, report)
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
