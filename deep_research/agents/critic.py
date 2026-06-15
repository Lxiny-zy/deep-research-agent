"""Critic：对已综合的报告做批判性复核，给出改进意见（演示「加角色不改引擎」）。

这个角色是重构后可扩展性的活样例：它只做两件事——
  1) 用 @register("critic") 注册；
  2) 实现统一的 step(bb, ctx) 协议，读 bb.report、把评审意见写进 bb.scratch。
就能被任意工作流按名 "critic" 调度，无需改动 workflow.py / orchestrator.py 一行。

它消费报告、产出结构化评审，并发出自己的 CRITIC 事件（得益于 Stage 已开放为 str）。
"""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, Field

from ..config import Settings
from ..llm import LLM
from ..observability import Tracer
from ..registry import register
from .base import Blackboard, RunContext

SYSTEM = (
    "你是严谨的研究评审专家。基于给定的研究报告，指出其中证据薄弱、逻辑跳跃、"
    "覆盖不足或表述含糊之处，给出可执行的改进建议。只评审，不重写报告。"
)


class Critique(BaseModel):
    overall: str = Field("", description="总体评价")
    issues: list[str] = Field(default_factory=list, description="具体问题点")
    suggestions: list[str] = Field(default_factory=list, description="改进建议")


@register("critic")
class Critic:
    def __init__(
        self, llm: LLM | None = None, tracer: Tracer | None = None, settings: Settings | None = None
    ) -> None:
        self.llm = cast(LLM, llm)
        self.tracer = cast(Tracer, tracer)
        self.settings = cast(Settings, settings)

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        self.llm, self.tracer, self.settings = ctx.llm, ctx.tracer, ctx.settings
        self.tracer.emit("CRITIC", "start", "复核研究报告…")
        if bb.report is None:
            self.tracer.emit("CRITIC", "info", "无报告可复核，跳过")
            return bb
        critique = await self.llm.parse(
            SYSTEM, f"研究报告：\n{bb.report.markdown}", Critique
        )
        bb.scratch["critique"] = critique.model_dump()
        self.tracer.emit(
            "CRITIC",
            "info",
            f"复核完成：{len(critique.issues)} 个问题，{len(critique.suggestions)} 条建议",
            data=critique.model_dump(),
        )
        return bb
