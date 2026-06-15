"""子问题的 DAG 并行调度器（从 orchestrator 抽出，供编排器与工作流引擎共用）。

只负责「按依赖拓扑分层、同层并发、层间串行、前层发现作后层上下文」这一调度策略，
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

# 研究单个子问题：给定问题与（可选）前驱发现作上下文，返回结果或 None
ResearchOne = Callable[[str, "list[Finding] | None"], Awaitable["ResearchResult | None"]]


async def research_dag(
    sub_questions: list[SubQuestion],
    research_one: ResearchOne,
    tracer: Tracer,
) -> list[ResearchResult]:
    """按依赖拓扑分层并行检索：同层并发，层间串行（后层可用前层发现作上下文）。

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
        res = await research_one(sub_questions[i].question, ctx or None)
        return i, res

    for layer in layers:
        layer_out = await asyncio.gather(*[_run_idx(i) for i in layer], return_exceptions=True)
        for item in layer_out:
            if isinstance(item, asyncio.CancelledError):
                raise item  # 取消必须向上传播，不可吞
            if isinstance(item, BaseException):
                # research_one 内部已兜底常规失败，落到这里的是未预期异常：
                # 记录而非静默丢弃，否则该子问题会"无痕消失"，极难排查
                tracer.emit("RESEARCHER", "error", f"子问题执行出现未预期异常：{item}")
                continue
            idx, res = item
            if isinstance(res, ResearchResult) and res.findings:
                collected[idx] = res

    return [collected[i] for i in sorted(collected)]
