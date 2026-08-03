"""意图路由：把「识别到的意图」翻译成「怎么跑这次研究」。

这一层刻意与分类器分离。分类器回答「这是什么」，路由回答「那要怎么办」——
二者的变更频率与评测方式完全不同（分类器看准确率，路由看成本/质量收益），
混在一起会让「换个路由策略」变成「重训模型」。

路由是**建议而非命令**：用户显式指定了工作流时，意图路由必须让位
（见 ``resolve_workflow``）。自动化不能覆盖显式意图，否则用户会失去控制感，
也让线上问题难以排查。
"""

from __future__ import annotations

from dataclasses import dataclass

from .cascade import IntentCascade
from .types import ConversationTurn, IntentDecision

# 任务意图 → 建议工作流。映射依据是「该意图是否需要多侧面覆盖与补洞」：
#   factual_lookup 单点事实，规划与反思纯属浪费 → quick
#   comparative / causal_analysis 需要多侧面且易漏证据 → deep（含反思补洞）
#   exploratory 面最广，适合多团队并行分头覆盖 → teams
#   temporal_trend 需要多时间切片的交叉印证 → deep
_INTENT_WORKFLOW: dict[str, str] = {
    "factual_lookup": "quick",
    "comparative": "deep",
    "exploratory": "teams",
    "temporal_trend": "deep",
    "causal_analysis": "deep",
}

# 建议的子问题数：单点查询不需要铺开，开放调研需要更宽的覆盖面。
_INTENT_SUB_QUESTIONS: dict[str, int] = {
    "factual_lookup": 2,
    "comparative": 4,
    "exploratory": 6,
    "temporal_trend": 5,
    "causal_analysis": 4,
}

# 低于此置信度不采纳路由建议：路由错误的代价（跑错流程、烧掉预算）
# 高于「走默认流程」的代价，因此宁可保守。
MIN_ROUTING_CONFIDENCE = 0.6


@dataclass(frozen=True)
class RoutingPlan:
    """一次意图路由的结论。``applied=False`` 表示保持调用方原有选择。"""

    workflow: str | None = None
    max_sub_questions: int | None = None
    applied: bool = False
    reason: str = ""

    def describe(self) -> str:
        if not self.applied:
            return self.reason or "未应用意图路由"
        parts = [f"工作流→{self.workflow}"]
        if self.max_sub_questions is not None:
            parts.append(f"子问题上限→{self.max_sub_questions}")
        return "，".join(parts)


def plan_route(
    decision: IntentDecision,
    *,
    requested_workflow: str | None = None,
    available_workflows: set[str] | None = None,
) -> RoutingPlan:
    """按意图给出路由建议。

    三种情况不路由（都返回 applied=False）：
      1. 用户已显式指定工作流——显式优先于推断；
      2. 意图为 unknown 或置信度不足——不猜；
      3. 建议的工作流在本部署里不可用——不能路由到不存在的流程。
    """
    if requested_workflow:
        return RoutingPlan(reason=f"用户已显式指定工作流「{requested_workflow}」，不覆盖")
    if decision.blocked:
        return RoutingPlan(reason="请求被风险门禁拦截，不进行路由")
    if decision.intent == "unknown":
        return RoutingPlan(reason="意图未定，走默认工作流")
    if decision.confidence < MIN_ROUTING_CONFIDENCE:
        return RoutingPlan(
            reason=(
                f"意图置信度 {decision.confidence:.2f} 低于 "
                f"{MIN_ROUTING_CONFIDENCE}，走默认工作流"
            )
        )

    workflow = _INTENT_WORKFLOW.get(decision.intent)
    if workflow is None:
        return RoutingPlan(reason=f"意图「{decision.intent}」无对应路由规则")
    if available_workflows is not None and workflow not in available_workflows:
        return RoutingPlan(reason=f"建议工作流「{workflow}」在当前部署不可用，走默认")

    return RoutingPlan(
        workflow=workflow,
        max_sub_questions=_INTENT_SUB_QUESTIONS.get(decision.intent),
        applied=True,
        reason=f"意图「{decision.intent}」（{decision.tier} 层，p={decision.confidence:.2f}）",
    )


async def preroute_workflow(
    query: str,
    *,
    requested_workflow: str | None,
    available_workflows: set[str],
    enabled: bool = True,
    llm: object | None = None,
    enable_llm: bool = False,
    history: list[ConversationTurn] | None = None,
) -> tuple[str | None, IntentDecision | None, RoutingPlan]:
    """在运行开始前决定这次要跑哪条工作流。

    路由必须发生在**创建 run 之前**：工作流定义会被写进初始 checkpoint，
    崩溃恢复直接读它。若等到 IntentRouter 在流程内部才路由，那时工作流已经
    定死，路由结论就只能是一条日志而无法真正改变执行路径。

    ``enable_llm`` 默认关闭：这条路径在 HTTP 请求的同步段上，升级到 LLM 会把
    创建研究的响应时间从毫秒级拉到秒级。规则 + 本地模型足以覆盖绝大多数
    可路由的样本，剩下的走默认流程也不会出错。

    **但多轮消解是例外**：带 history 的请求若不消解，分类器看到的是「那第二个呢」
    这种零信息量的残句，判定必然弃权、路由必然失效——多轮功能就等于没做。
    因此有 history 时允许消解调用 LLM，代价是这类请求的创建响应变慢。
    这是个有意识的取舍：多轮追问本来就少于首轮提问，且用户对追问的等待容忍度更高。

    返回 (最终工作流名, 意图判定, 路由计划)。工作流名为 None 表示保持调用方原值。
    """
    if not enabled:
        return requested_workflow, None, RoutingPlan(reason="意图识别已关闭")

    cascade = IntentCascade(llm=llm, enable_llm=enable_llm or bool(history))
    decision = await cascade.classify(query, history=history or [])
    if decision.blocked:
        # 拒识不在这里终止请求：仍然创建 run，交给流程内的 IntentRouter 产出
        # 说明性报告。这样拒识与正常运行共用同一套落库 / SSE / 回放语义，
        # 用户也能在历史里看到「这条请求为什么被拒」。
        # guarded 缺失时保留调用方选择——IntentRouter 一旦被编排进流程就会拦截；
        # 若该流程根本没有意图角色，拦截由下游护栏兜底，总之不能路由到不存在的流程。
        target = "guarded" if "guarded" in available_workflows else requested_workflow
        return target, decision, RoutingPlan(reason=f"风险意图 {decision.risk}，转入门禁流程")

    if decision.needs_clarification:
        # 需要澄清同样走 guarded：那条流程里的 IntentRouter 会产出澄清报告并终止。
        # 与拒识复用同一条路径，是因为二者的产品语义一致——都是「不执行研究，
        # 但要给用户一份可读的说明」。
        target = "guarded" if "guarded" in available_workflows else requested_workflow
        return target, decision, RoutingPlan(reason="意图存在歧义，转入澄清流程")

    plan = plan_route(
        decision,
        requested_workflow=requested_workflow,
        available_workflows=available_workflows,
    )
    return (plan.workflow if plan.applied else requested_workflow), decision, plan
