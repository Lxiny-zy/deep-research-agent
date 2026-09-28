"""Reflector：评估证据是否充分，决定是否继续补洞。"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ..config import Settings
from ..guardrails import report_eligible
from ..llm import LLM
from ..models import Reflection, ResearchResult
from ..observability import Tracer
from ..registry import register
from ..workflow import ATTEMPTED_SCRATCH_KEY
from .base import Blackboard, RunContext, direct_system_prompt, effective_require_corroboration

if TYPE_CHECKING:
    from ..workbench.coverage import CoverageGaps

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
        attempted = bb.scratch.get(ATTEMPTED_SCRATCH_KEY)
        corroboration = effective_require_corroboration(bb, ctx.settings)
        from ..workbench.coverage import coverage_gaps

        gaps = coverage_gaps(
            bb.scratch,
            bb.results,
            bb.query,
            settings_quality=getattr(ctx.settings, "quality", None),
            require_corroboration=corroboration,
        )
        reflection = await self.run(
            bb.query,
            bb.results,
            require_corroboration=corroboration,
            attempted=attempted if isinstance(attempted, dict) else None,
            coverage=gaps,
        )
        bb.reflections.append(reflection)
        return bb

    async def run(
        self,
        query: str,
        results: list[ResearchResult],
        *,
        require_corroboration: bool | None = None,
        attempted: dict[str, int] | None = None,
        coverage: CoverageGaps | None = None,
    ) -> Reflection:
        self.tracer.emit("REFLECTOR", "start", "评估证据是否充分…")
        corroboration = (
            self.settings.require_corroboration
            if require_corroboration is None
            else require_corroboration
        )
        user = (
            f"原始问题：{query}\n\n现有发现：\n"
            f"{_digest(results, require_corroboration=corroboration)}"
        )
        fruitless = _fruitless(results, attempted, require_corroboration=corroboration)
        if fruitless:
            user += (
                "\n\n已研究但未得到合格发现的子问题（不要原样重提；如确有必要，"
                "请换一个检索角度或更具体的表述）：\n" + "\n".join(f"- {q}" for q in fruitless)
            )
        if coverage is not None and coverage.open:
            from ..workbench.coverage import gap_prompt

            user += gap_prompt(coverage)
        reflection = await self.llm.parse(direct_system_prompt(self.system), user, Reflection)
        if reflection.is_sufficient and coverage is not None and coverage.open:
            # 程序计算的交付缺口优先于模型的「已充分」判断：模型常在证据远未达到
            # 引用下限时就宣布充分。只有给出了新子问题才能继续补洞；否则保持原判，
            # 由写作与交付门如实报告缺口（不会因此陷入空转）。
            if reflection.new_sub_questions:
                reflection.is_sufficient = False
                reflection.gaps = [*coverage.gaps, *reflection.gaps]
                self.tracer.emit(
                    "REFLECTOR",
                    "info",
                    "交付要求尚未满足，继续补洞：" + "；".join(coverage.gaps),
                    data={"coverage": coverage.metrics},
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
    per_question: int = 5,
) -> str:
    """按子问题分组的发现摘要。

    旧实现把全部发现摊平后截断前 ``limit`` 条：发现一多，排在后面的子问题
    整组被截掉，Reflector 会误以为那些方向「还没有证据」而重复补洞。分组后
    每个子问题都保证出现，且带上合格发现总数，截断只发生在组内。
    """
    groups: dict[str, list[str]] = {}
    for result in results:
        statements = groups.setdefault(result.sub_question, [])
        statements.extend(
            f.statement
            for f in result.findings
            if report_eligible(f, require_corroboration=require_corroboration)
        )
    groups = {question: items for question, items in groups.items() if items}
    if not groups:
        return "（暂无发现）"
    # 组内配额：总量不超过 limit，但每组至少 1 条。
    quota = max(1, min(per_question, limit // len(groups)))
    blocks: list[str] = []
    for question, statements in groups.items():
        shown = statements[:quota]
        header = f"■ {question}（合格发现 {len(statements)} 条）"
        lines = [f"- {statement}" for statement in shown]
        if len(statements) > len(shown):
            lines.append(f"- …另有 {len(statements) - len(shown)} 条未列出")
        blocks.append("\n".join([header, *lines]))
    return "\n".join(blocks)


def _fruitless(
    results: list[ResearchResult],
    attempted: dict[str, int] | None,
    *,
    require_corroboration: bool = False,
) -> list[str]:
    """研究过但没有任何可进报告发现的子问题。"""
    if not attempted:
        return []
    productive = {
        result.sub_question
        for result in results
        if any(
            report_eligible(f, require_corroboration=require_corroboration) for f in result.findings
        )
    }
    return [question for question in attempted if question not in productive]
