"""Planner：把研究问题拆解为若干可独立检索的子问题（可带依赖关系）。"""

from __future__ import annotations

from typing import cast

from pydantic import ValidationError

from ..config import Settings
from ..intent.types import IntentSlots
from ..llm import LLM
from ..models import ResearchPlan
from ..observability import Tracer
from ..registry import register
from .base import Blackboard, RunContext
from .intent_router import (
    INTENT_RESOLVED_QUERY_KEY,
    INTENT_SLOTS_KEY,
    INTENT_SUB_QUESTION_KEY,
)

SYSTEM = (
    "你是一名资深研究规划师。把用户的研究问题拆解为若干互补、可独立检索的子问题，"
    "覆盖问题的不同侧面（现状、关键玩家、技术/方法、争议或风险、趋势等），子问题之间尽量不重叠。"
    "若某子问题必须先得到另一子问题的检索结果才能展开（例如先确定主流框架，再逐一比较其取舍），"
    "在其 depends_on 中填写所依赖子问题的序号（基于本次输出列表的 0 起始下标）；无依赖则留空。"
    "依赖关系应尽量简单且无环。"
)


@register("planner")
class Planner:
    name: str  # 由 @register 注入

    def __init__(
        self, llm: LLM | None = None, tracer: Tracer | None = None, settings: Settings | None = None
    ) -> None:
        # 依赖可选：经注册表无参构造时为 None，step() 时从 RunContext 绑定；经 orchestrator
        # 直接构造时照旧传入。属性按非 Optional 标注——使用前必经 step/构造绑定真实依赖。
        self.llm = cast(LLM, llm)
        self.tracer = cast(Tracer, tracer)
        self.settings = cast(Settings, settings)
        self.system = SYSTEM  # 可被角色卡片的 system_prompt 覆盖（数据驱动角色）

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        self.llm, self.tracer, self.settings = ctx.llm_for(self.name), ctx.tracer, ctx.settings
        # 意图路由给出的子问题预算。两个可能的写入方：API 层的预路由（正常流量走
        # 这条——路由结果多是 deep/quick/teams，那些流程里没有 intent_router 角色）
        # 与 IntentRouter 角色本身（guarded 流程）。两者写入前都已与用户配置取过 min。
        # 这里再做一次 `< limit` 校验：这个键来自 checkpoint，可能被旧版本或
        # 手工编辑写脏，预算只能收紧的不变量不该依赖写入方全都守规矩。
        budget = bb.scratch.get(INTENT_SUB_QUESTION_KEY)
        limit = self.settings.max_sub_questions
        if isinstance(budget, int) and 1 <= budget < limit:
            limit = budget
        # 多轮追问的原文（「那第二个呢」）无法独立检索，必须用消解后的完整问题。
        query = bb.scratch.get(INTENT_RESOLVED_QUERY_KEY) or bb.query
        bb.plan = await self.run(
            str(query),
            max_sub_questions=limit,
            constraints=self._constraints(bb),
        )
        return bb

    @staticmethod
    def _constraints(bb: Blackboard) -> str:
        """把意图槽位渲染成给 LLM 的约束描述。

        槽位是**用户明确说过的约束**（抽取层不猜、抽不到就留空），因此可以直接
        进 prompt 要求遵守。为空时返回空串——不注入任何伪造的默认约束。
        """
        raw = bb.scratch.get(INTENT_SLOTS_KEY)
        if not isinstance(raw, dict):
            return ""
        try:
            return IntentSlots.model_validate(raw).describe()
        except ValidationError:
            # 槽位来自 checkpoint，结构变更时不该让整次规划失败——降级为无约束。
            return ""

    async def run(
        self,
        query: str,
        *,
        max_sub_questions: int | None = None,
        constraints: str = "",
    ) -> ResearchPlan:
        limit = max_sub_questions or self.settings.max_sub_questions
        self.tracer.emit("PLANNER", "start", "拆解研究问题…")
        constraint_block = (
            f"\n\n用户明确给出的约束（拆解时必须遵守，不要扩大范围）：\n{constraints}"
            if constraints
            else ""
        )
        plan = await self.llm.parse(
            self.system,
            f"研究问题：{query}{constraint_block}\n\n请给出不超过 {limit} 个子问题。",
            ResearchPlan,
        )
        plan.sub_questions = plan.sub_questions[:limit]
        # 清洗依赖：剔除越界 / 自环 / 重复（基于截断后的子问题数量）
        n = len(plan.sub_questions)
        for i, sq in enumerate(plan.sub_questions):
            sq.depends_on = [d for d in dict.fromkeys(sq.depends_on) if 0 <= d < n and d != i]
        self.tracer.emit(
            "PLANNER",
            "info",
            f"拆解出 {len(plan.sub_questions)} 个子问题",
            data={"sub_questions": [s.question for s in plan.sub_questions]},
        )
        return plan
