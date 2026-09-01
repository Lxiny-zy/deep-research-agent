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
from ..report.hsi_tables import hsi_table_schemas
from .base import Blackboard, RunContext, direct_system_prompt
from .intent_router import (
    INTENT_POLICY_KEY,
    INTENT_RESOLVED_QUERY_KEY,
    INTENT_ROUTE_KEY,
    INTENT_SCRATCH_KEY,
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

_HSI_WORKFLOW = "hsi_review"
_HSI_ANSWER_MODES = frozenset(
    {
        "literature_review",
        "method_comparison",
        "benchmark_survey",
        "reproducibility_check",
        "dataset_discovery",
    }
)


def _hsi_schema_contract() -> str:
    """Render the code-owned HSI report schema as a Planner-only contract.

    The Planner can use this to target evidence collection, but it never owns
    table values.  Keeping the text derived from ``hsi_table_schemas`` makes a
    schema change fail loudly in prompt-focused tests instead of silently
    leaving the planning instructions stale.
    """
    lines = [
        "HSI 结构化报告策略（表格由代码按固定 schema 渲染，不能由模型自由生成）：",
        "规划检索问题时覆盖下列四张表所需的证据；只记录来源明确报告的字段，缺失值保留为未报告，不补猜。",
    ]
    for table in hsi_table_schemas():
        columns = ", ".join(
            f"{column.key}（{column.label}）" + (f" [{column.unit}]" if column.unit else "")
            for column in table.columns
        )
        lines.append(f"- {table.id}（{table.title}）：{columns}")
    lines.extend(
        (
            "数值必须连同数据集、波段/光谱范围、场景、采集方式、划分和评测 protocol 等条件检索；",
            "区分同一 work 与独立 work、同组与非同组、预印本与同行评审，并显式保留冲突；"
            "不要把伪双源当作独立印证。",
            "Planner 只拆分可检索的证据问题，不输出表格、数字或未经来源支持的列值。",
        )
    )
    return "\n".join(lines)


def _is_hsi_context(bb: Blackboard) -> bool:
    """Return whether the checkpoint explicitly selects the HSI report path."""
    policy = bb.scratch.get(INTENT_POLICY_KEY)
    if isinstance(policy, dict):
        if policy.get("workflow") == _HSI_WORKFLOW:
            return True
        if policy.get("answer_mode") in _HSI_ANSWER_MODES:
            return True

    route = bb.scratch.get(INTENT_ROUTE_KEY)
    if isinstance(route, dict):
        if route.get("workflow") == _HSI_WORKFLOW:
            return True
        nested_policy = route.get("execution_policy")
        if isinstance(nested_policy, dict):
            if nested_policy.get("workflow") == _HSI_WORKFLOW:
                return True
            if nested_policy.get("answer_mode") in _HSI_ANSWER_MODES:
                return True

    # Explicit workflow selection intentionally suppresses inferred policy, so
    # this is the only HSI marker available for that path in old checkpoints.
    return bb.scratch.get("requested_workflow") == _HSI_WORKFLOW


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
        self.system = ctx.system_prompt(self.system)
        # 意图路由给出的子问题预算。它由 API 层的全局预路由写入 checkpoint；直接
        # 入口也会在运行前补判，确保所有工作流共享同一份策略快照。写入前已与用户
        # 配置取过 min。
        # 这里再做一次 `< limit` 校验：这个键来自 checkpoint，可能被旧版本或
        # 手工编辑写脏，预算只能收紧的不变量不该依赖写入方全都守规矩。
        limit = self.settings.max_sub_questions
        policy = bb.scratch.get(INTENT_POLICY_KEY)
        if isinstance(policy, dict):
            policy_budget = policy.get("max_sub_questions")
            if (
                isinstance(policy_budget, int)
                and not isinstance(policy_budget, bool)
                and policy_budget >= 1
            ):
                limit = min(limit, policy_budget)
        budget = bb.scratch.get(INTENT_SUB_QUESTION_KEY)
        if isinstance(budget, int) and not isinstance(budget, bool) and 1 <= budget < limit:
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
        pieces: list[str] = []
        raw_slots = bb.scratch.get(INTENT_SLOTS_KEY)
        if not isinstance(raw_slots, dict):
            raw_intent = bb.scratch.get(INTENT_SCRATCH_KEY)
            if isinstance(raw_intent, dict):
                candidate = raw_intent.get("slots")
                if isinstance(candidate, dict):
                    raw_slots = candidate
        if isinstance(raw_slots, dict):
            try:
                slots = IntentSlots.model_validate(raw_slots)
            except ValidationError:
                # 槽位来自 checkpoint，结构变更时不该让整次规划失败——降级为无槽位约束。
                pass
            else:
                if not slots.is_empty():
                    pieces.append(slots.describe())

        # API 预路由的工作流通常不包含 IntentRouter，因此执行策略直接来自
        # 初始 checkpoint，不能依赖独立的槽位键是否已经由角色展开。
        policy = bb.scratch.get(INTENT_POLICY_KEY)
        if isinstance(policy, dict):
            answer_mode = policy.get("answer_mode")
            source_strategy = policy.get("source_strategy")
            freshness = policy.get("freshness")
            if isinstance(answer_mode, str) and answer_mode:
                pieces.append(f"回答模式：{answer_mode}")
            if isinstance(source_strategy, str) and source_strategy:
                pieces.append(f"检索策略：{source_strategy}")
            if isinstance(freshness, str) and freshness and freshness != "any":
                pieces.append(f"时效要求：{freshness}")
        if _is_hsi_context(bb):
            pieces.append(_hsi_schema_contract())
        return "；".join(pieces)

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
            direct_system_prompt(self.system),
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
