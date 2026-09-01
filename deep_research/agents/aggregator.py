"""Aggregator（L4 归并 / reduce）：把多团队的研究发现归并、综合成统一的带引用报告。

L4 fan-out 中每个子团队在隔离黑板上各自研究；引擎把它们的发现合并进父黑板后，由本角色做
「reduce」——复用 Synthesizer 的素材整理与 [n] 引用编号逻辑，避免重复造轮子。
"""

from __future__ import annotations

from typing import cast

from ..config import Settings
from ..llm import LLM
from ..observability import Tracer
from ..registry import register
from .base import Blackboard, RunContext, effective_require_corroboration
from .synthesizer import Synthesizer

SYSTEM = (
    "你是研究总编。下面的素材来自多个并行子团队各自的检索发现（已带 [n] 角标）。"
    "请去重、整合、消解冲突，综合成一篇结构化、连贯、带 [n] 引用的中文研究报告。"
    "保留素材中的 [n] 角标；不得引入素材之外的新事实；不要自己编写参考来源列表（系统会自动追加）。"
)


@register("aggregator")
class Aggregator:
    name: str  # 由 @register 注入

    def __init__(
        self, llm: LLM | None = None, tracer: Tracer | None = None, settings: Settings | None = None
    ) -> None:
        self.llm = cast(LLM, llm)
        self.tracer = cast(Tracer, tracer)
        self.settings = cast(Settings, settings)
        self.system = SYSTEM  # 可被角色卡片覆盖

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        self.llm, self.tracer, self.settings = ctx.llm_for(self.name), ctx.tracer, ctx.settings
        self.tracer.emit("AGGREGATOR", "start", f"归并 {len(bb.results)} 组团队发现…")
        # 复用 Synthesizer 的素材整理 + 引用编号 + 综合（用聚合风格 system 与本角色的 LLM）
        synth = Synthesizer(self.llm, self.tracer, self.settings)
        synth.system = self.system
        bb.report = await synth.run(
            bb.query,
            bb.results,
            require_corroboration=effective_require_corroboration(bb, ctx.settings),
            system=ctx.system_prompt(self.system),
        )
        self.tracer.emit("AGGREGATOR", "info", f"归并完成，引用 {len(bb.report.citations)} 个来源")
        return bb
