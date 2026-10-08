"""子问题的 DAG 并行调度器（从 orchestrator 抽出，供编排器与工作流引擎共用）。

只负责「依赖就绪即并发启动、前驱发现作后继上下文」这一调度策略，
不关心「研究一个子问题」具体怎么做——后者由调用方以 research_one 回调注入。
这样 orchestrator._research_dag 与引擎的 fanout 原语共享同一份调度逻辑，
且测试替换 researcher 时仍然生效。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from .dag import build_dag, detect_cycle, topo_layers
from .models import Finding, ResearchResult, SubQuestion
from .observability import Tracer
from .persistence.repository import LeaseLostError
from .research_progress import ResearchProgressError

# 研究单个子问题：给定问题与（可选）前驱发现作上下文，返回结果或 None
ResearchOne = Callable[[str, "list[Finding] | None"], Awaitable["ResearchResult | None"]]


def planned_search_queries(sub_questions: list[SubQuestion], question: str) -> list[str]:
    return list(
        dict.fromkeys(
            query
            for item in sub_questions
            if item.question == question
            for query in item.search_queries
        )
    )


async def research_dag(
    sub_questions: list[SubQuestion],
    research_one: ResearchOne,
    tracer: Tracer,
) -> list[ResearchResult]:
    """依赖就绪即检索，不等待同一拓扑层中的无关慢分支。

    无依赖时退化为单层全并行，行为与纯并行 fan-out 等价。
    """
    if not sub_questions:
        return []

    dag = build_dag([sq.depends_on for sq in sub_questions])
    cycle = detect_cycle(dag)
    if cycle:  # 破环降级为纯并行，保证永不死锁
        tracer.emit("ORCHESTRATOR", "info", f"子问题依赖存在环（{cycle}），降级为纯并行检索")
        dag = {i: [] for i in dag}

    layers = topo_layers(dag)
    if len(layers) > 1:
        tracer.emit(
            "ORCHESTRATOR",
            "info",
            f"按依赖就绪调度 {len(dag)} 个子问题（依赖深度 {len(layers)}）",
            data={"dag": {"layers": layers, "deps": {str(i): dag[i] for i in dag},
                          "strategy": "ready"}},
        )

    collected: dict[int, ResearchResult] = {}

    async def _run_idx(i: int) -> tuple[int, ResearchResult | None]:
        # 汇集前驱已得到的发现，作为本子问题的背景上下文
        ctx: list[Finding] = []
        for p in dag[i]:
            prev = collected.get(p)
            if prev:
                ctx.extend(prev.findings)
        res = await research_one(sub_questions[i].question, ctx or None)
        return i, res

    pending = set(dag)
    finished: set[int] = set()
    running: dict[asyncio.Task[tuple[int, ResearchResult | None]], int] = {}
    try:
        while pending or running:
            for index in sorted(pending):
                if all(parent in finished for parent in dag[index]):
                    running[asyncio.create_task(_run_idx(index))] = index
                    pending.remove(index)
            if not running:
                raise RuntimeError("子问题依赖无法继续调度")
            completed, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            # Process this completion set before admitting successors, so a
            # lost lease never starts additional work in the same iteration.
            for task in sorted(completed, key=lambda item: running[item]):
                index = running.pop(task)
                finished.add(index)
                try:
                    idx, res = task.result()
                except (LeaseLostError, ResearchProgressError):
                    raise
                except Exception as exc:
                    tracer.emit("RESEARCHER", "error", f"子问题执行出现未预期异常：{exc}")
                    continue
                if isinstance(res, ResearchResult) and (res.findings or res.extraction_audit):
                    collected[idx] = res
    except BaseException:
        for task in running:
            if not task.done():
                task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        raise

    return [collected[i] for i in sorted(collected)]
