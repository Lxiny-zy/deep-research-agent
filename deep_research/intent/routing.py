"""意图路由：把「识别到的意图」翻译成「怎么跑这次研究」。

这一层刻意与分类器分离。分类器回答「这是什么」，路由回答「那要怎么办」——
二者的变更频率与评测方式完全不同（分类器看准确率，路由看成本/质量收益），
混在一起会让「换个路由策略」变成「重训模型」。

路由是**建议而非命令**：用户显式指定了工作流时，意图路由必须让位
（见 ``resolve_workflow``）。自动化不能覆盖显式意图，否则用户会失去控制感，
也让线上问题难以排查。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ..prompting import load_global_rules
from .cascade import IntentCascade
from .types import (
    ConversationTurn,
    ExecutionPolicy,
    IntentDecision,
    execution_policy_for,
    normalize_query_intent,
)

# 任务意图 → 建议工作流。映射依据是「该意图是否需要多侧面覆盖与补洞」：
#   factual_lookup 单点事实，规划与反思纯属浪费 → quick
#   comparative / causal_analysis 需要多侧面且易漏证据 → deep（含反思补洞）
#   exploratory 面最广，适合多团队并行分头覆盖 → teams
#   temporal_trend 需要多时间切片的交叉印证 → deep
_INTENT_WORKFLOW: dict[str, str] = {
    "literature_review": "hsi_review",
    "method_comparison": "hsi_review",
    "benchmark_survey": "hsi_review",
    "reproducibility_check": "hsi_review",
    "dataset_discovery": "hsi_review",
    "factual_lookup": "quick",
    "comparative": "deep",
    "exploratory": "teams",
    "temporal_trend": "deep",
    "causal_analysis": "deep",
    "definition_explanation": "brief",
    "procedural_guidance": "brief",
    "recommendation": "deep",
    "fact_check": "fact_check",
    "summarization": "brief",
    "multi_hop_research": "teams",
    "monitoring": "monitoring",
}

# 建议的子问题数：单点查询不需要铺开，开放调研需要更宽的覆盖面。
_INTENT_SUB_QUESTIONS: dict[str, int] = {
    "literature_review": 8,
    "method_comparison": 6,
    "benchmark_survey": 8,
    "reproducibility_check": 6,
    "dataset_discovery": 6,
    "factual_lookup": 2,
    "comparative": 4,
    "exploratory": 6,
    "temporal_trend": 5,
    "causal_analysis": 4,
    "definition_explanation": 2,
    "procedural_guidance": 3,
    "recommendation": 5,
    "fact_check": 4,
    "summarization": 2,
    "multi_hop_research": 8,
    "monitoring": 5,
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
    execution_policy: ExecutionPolicy | None = None
    strategy: str = ""
    # Metadata fields are additive; old callers can continue reading the four
    # original fields above.
    intent: str = "unknown"
    confidence: float = 0.0
    reason_code: str = ""
    fallback_workflow: str | None = None

    @property
    def policy(self) -> ExecutionPolicy | None:
        return self.execution_policy

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.execution_policy is not None:
            payload["execution_policy"] = self.execution_policy.model_dump(mode="json")
        return payload

    def describe(self) -> str:
        if not self.applied:
            return self.reason or "未应用意图路由"
        parts = [f"工作流→{self.workflow}"]
        if self.max_sub_questions is not None:
            parts.append(f"子问题上限→{self.max_sub_questions}")
        if self.strategy:
            parts.append(f"策略→{self.strategy}")
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
    canonical_intent = normalize_query_intent(decision.intent)
    policy = decision.execution_policy or execution_policy_for(canonical_intent)
    if requested_workflow:
        return RoutingPlan(
            reason=f"用户已显式指定工作流「{requested_workflow}」，不覆盖",
            reason_code="explicit_workflow",
            intent=canonical_intent,
            confidence=decision.confidence,
            execution_policy=policy,
        )
    if decision.blocked:
        return RoutingPlan(
            reason="请求被风险门禁拦截，不进行路由",
            reason_code="blocked",
            intent=canonical_intent,
            confidence=decision.confidence,
            execution_policy=policy,
        )
    if canonical_intent == "unknown":
        return RoutingPlan(
            reason="意图未定，走默认工作流",
            reason_code="unknown_intent",
            intent=canonical_intent,
            confidence=decision.confidence,
            execution_policy=policy,
        )
    if decision.confidence < MIN_ROUTING_CONFIDENCE:
        return RoutingPlan(
            reason=(
                f"意图置信度 {decision.confidence:.2f} 低于 {MIN_ROUTING_CONFIDENCE}，走默认工作流"
            ),
            reason_code="low_confidence",
            intent=canonical_intent,
            confidence=decision.confidence,
            execution_policy=policy,
        )

    workflow = policy.workflow or _INTENT_WORKFLOW.get(canonical_intent)
    if workflow is None:
        return RoutingPlan(
            reason=f"意图「{canonical_intent}」无对应路由规则",
            reason_code="no_mapping",
            intent=canonical_intent,
            confidence=decision.confidence,
            execution_policy=policy,
        )
    if available_workflows is not None and workflow not in available_workflows:
        fallback = "deep" if "deep" in available_workflows else None
        return RoutingPlan(
            reason=f"建议工作流「{workflow}」在当前部署不可用，走默认",
            reason_code="workflow_unavailable",
            fallback_workflow=fallback,
            intent=canonical_intent,
            confidence=decision.confidence,
            execution_policy=policy,
        )

    policy = decision.execution_policy or execution_policy_for(decision.intent)
    # The route's legacy fields remain authoritative for compatibility, while
    # policy metadata is persisted as an auditable snapshot for the runtime.
    if policy.workflow and policy.workflow != workflow:
        policy = policy.model_copy(update={"workflow": workflow})
    return RoutingPlan(
        workflow=workflow,
        max_sub_questions=policy.max_sub_questions or _INTENT_SUB_QUESTIONS.get(canonical_intent),
        applied=True,
        reason=f"意图「{canonical_intent}」（{decision.tier} 层，p={decision.confidence:.2f}）",
        execution_policy=policy,
        strategy=policy.source_strategy,
        intent=canonical_intent,
        confidence=decision.confidence,
        reason_code="intent_policy",
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
    allow_clarification: bool = True,
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

    ``allow_clarification=False`` 供「用户已明确表态跳过澄清」的调用方使用
    （前端澄清循环里点了「直接研究」）：跳过的是**澄清**，不是风险门禁——
    拒识判定照常进行。没有这个口子，跳过澄清的请求会被本函数再判一次
    需要澄清，422 打回，「直接研究」按钮就是个死胡同。

    返回 (最终工作流名, 意图判定, 路由计划)。工作流名为 None 表示保持调用方原值。
    """
    if not enabled:
        return requested_workflow, None, RoutingPlan(reason="意图识别已关闭")

    cascade = IntentCascade(
        llm=llm,
        enable_llm=enable_llm or bool(history),
        global_rules=load_global_rules(),
    )
    # 不在这里抽实体：它只有 Planner 拆子问题时才用得上，而 Planner 跑在异步
    # 执行段。在 HTTP 同步段抽，等于让用户为一件本可以稍后做的事多等一次网络
    # 往返——对多轮追问尤其明显（消解已经花了一次，抽实体会让它翻倍）。
    # IntentRouter 会在异步段补上（见 `_needs_entities`）。
    decision = await cascade.classify(
        query,
        history=history or [],
        extract_entities=False,
        allow_clarification=allow_clarification,
    )
    if decision.blocked:
        # 拒识不在这里终止请求：仍然创建 run，交给全局 IntentRouter 产出
        # 说明性报告。安全门禁在 orchestrator 的所有入口交汇点执行，
        # 因而不需要、也不应该通过一个名为 ``guarded`` 的工作流表达。
        # 保留调用方明确选择的流程（含历史上的 guarded 别名）只影响
        # checkpoint 展示，不会绕过门禁；自动路由则保持默认流程。
        return (
            requested_workflow,
            decision,
            RoutingPlan(
                workflow=requested_workflow,
                reason=f"风险意图 {decision.risk}，由全局门禁拦截",
                reason_code="blocked",
                intent=decision.intent,
                confidence=decision.confidence,
                execution_policy=decision.execution_policy,
            ),
        )

    if decision.needs_clarification:
        # 澄清由 API 在创建 run 前处理；直接/CLI 入口则由同一个全局
        # IntentRouter 写入说明报告并 halt。这里不再伪造一条 guarded 流程。
        return (
            requested_workflow,
            decision,
            RoutingPlan(
                workflow=requested_workflow,
                reason="意图存在歧义，由全局门禁请求澄清",
                intent=decision.intent,
                confidence=decision.confidence,
                reason_code="needs_clarification",
                execution_policy=decision.execution_policy,
            ),
        )

    plan = plan_route(
        decision,
        requested_workflow=requested_workflow,
        available_workflows=available_workflows,
    )
    # An unavailable intent-specific workflow may have an explicitly computed
    # safe fallback (currently ``deep`` when it is deployed).  Preserve the
    # ``applied=False`` audit bit so callers can distinguish a fallback from a
    # confident intent route, while still executing the available workflow.
    if plan.reason_code == "workflow_unavailable" and plan.fallback_workflow:
        return plan.fallback_workflow, decision, plan
    return (plan.workflow if plan.applied else requested_workflow), decision, plan
