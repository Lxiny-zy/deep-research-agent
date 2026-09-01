"""Reflector：评估证据是否充分，决定是否继续补洞。"""

from __future__ import annotations

from typing import cast

from ..config import Settings
from ..guardrails import report_eligible
from ..llm import LLM
from ..models import Reflection, ResearchResult
from ..observability import Tracer
from ..registry import register
from .base import Blackboard, RunContext, direct_system_prompt, effective_require_corroboration

SYSTEM = (
    "你是研究质检员。评估现有发现是否足以全面、可靠地回答原始问题。"
    "若不足，指出缺口并提出最多 3 个新的子问题以补足；若已充分，明确标记 is_sufficient=true。"
)


@register("reflector")
class Reflector:
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
        self.system = ctx.system_prompt(self.system)
        reflection = await self.run(
            bb.query,
            bb.results,
            require_corroboration=effective_require_corroboration(bb, ctx.settings),
        )
        bb.reflections.append(reflection)
        return bb

    async def run(
        self,
        query: str,
        results: list[ResearchResult],
        *,
        require_corroboration: bool | None = None,
    ) -> Reflection:
        self.tracer.emit("REFLECTOR", "start", "评估证据是否充分…")
        corroboration = (
            self.settings.require_corroboration
            if require_corroboration is None
            else require_corroboration
        )
        reflection = await self.llm.parse(
            direct_system_prompt(self.system),
            (
                f"原始问题：{query}\n\n现有发现：\n"
                f"{_digest(results, require_corroboration=corroboration)}"
            ),
            Reflection,
        )
        if reflection.is_sufficient:
            self.tracer.emit("REFLECTOR", "info", "证据充分，进入综合")
        else:
            reflection.new_sub_questions = reflection.new_sub_questions[:3]
            self.tracer.emit(
                "REFLECTOR",
                "info",
                f"仍有缺口，新增 {len(reflection.new_sub_questions)} 个子问题",
                data={"gaps": reflection.gaps, "new_sub_questions": reflection.new_sub_questions},
            )
        return reflection


def _digest(
    results: list[ResearchResult],
    limit: int = 40,
    *,
    require_corroboration: bool = False,
) -> str:
    lines = [
        f"- ({r.sub_question}) {f.statement}"
        for r in results
        for f in r.findings
        if report_eligible(f, require_corroboration=require_corroboration)
    ]
    return "\n".join(lines[:limit]) or "（暂无发现）"
