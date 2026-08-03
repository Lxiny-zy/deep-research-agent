"""IntentRouter：把意图识别接进多智能体框架的一等角色。

它是「加角色不改引擎」的又一个实例——只做两件事：``@register("intent_router")``
注册 + 实现统一的 ``step(bb, ctx)``。放在工作流首位即可获得意图门禁与路由。

**为什么做成 Agent 而不是 API 层的一个函数**：
  - 它需要 ctx 里的 LLM（L3 兜底）、tracer（事件审计）、settings（阈值），
    这些正是 RunContext 提供的东西；
  - 意图判定要进 checkpoint 才能在崩溃恢复后保持一致（不能恢复时重判成别的意图）；
  - 它因此可以被任意工作流按名编排，也能被角色卡片替换实现。

**拒识如何终止流程**：本角色不抛异常——抛异常会走引擎的「单步失败隔离」，
流程照跑不误。它改为把拒识结论写进黑板并直接产出一份说明性 Report，
后续 planner/researcher 见到 ``intent_blocked`` 标记会跳过实际工作。
这样拒识路径与正常路径共用同一套落库/流式/回放语义。
"""

from __future__ import annotations

from typing import cast

from pydantic import ValidationError

from ..config import Settings
from ..intent.cascade import IntentCascade
from ..intent.routing import plan_route
from ..intent.types import IntentDecision
from ..llm import LLM
from ..models import Report
from ..observability import Tracer
from ..registry import register
from ..workflow import HALT_REASON_KEY, HALT_SCRATCH_KEY
from .base import Blackboard, RunContext

# 黑板 scratch 上的键：进 checkpoint，因此恢复后意图判定保持一致。
INTENT_SCRATCH_KEY = "intent"
INTENT_BLOCKED_KEY = "intent_blocked"
INTENT_ROUTE_KEY = "intent_route"
# 子问题预算。由 API 层预路由或本角色写入，Planner 读取。两个写入方都必须
# 先与 settings.max_sub_questions 取 min——这个键的语义是「已收紧过的上限」。
INTENT_SUB_QUESTION_KEY = "intent_max_sub_questions"

_BLOCK_MESSAGES = {
    "prompt_injection": "该请求试图覆盖系统指令或绕过安全设定",
    "system_prompt_probe": "该请求试图获取系统提示词、密钥或内部工具定义",
    "unsafe_content": "该请求索取可能造成实质危害的操作指导",
}


def blocked_report(query: str, decision: IntentDecision) -> Report:
    """为被拒识的请求产出说明性报告（而非空报告或异常）。

    仍然产出 Report 的理由：整条链路（落库 / SSE / 历史回放 / 前端渲染）都以
    「一定有报告」为前提。给拒识一份结构化说明，既不破坏这个不变量，
    也让用户看得到为什么被拒——而不是一个无解释的错误。
    """
    why = _BLOCK_MESSAGES.get(decision.risk, "该请求触发了安全门禁")
    evidence = "\n".join(
        f"- `{signal.code}`（{signal.tier} 层）：{signal.detail}"
        for signal in decision.signals
        if signal.detail
    )
    markdown = (
        "# 请求未被执行\n\n"
        "## 结论\n"
        f"意图识别判定本次请求为 **{decision.risk}**，已在研究开始前拦截。\n\n"
        "## 判定依据\n"
        f"{why}。\n\n"
        f"{evidence or '- 无可展示的匹配片段'}\n\n"
        "## 说明\n"
        "本系统只处理研究类请求。若这是一次正常的研究提问，"
        "请换一种表述方式重新提交；针对提示词注入、越狱等主题的**研究性问题**"
        "是被允许的，被拦截的是试图直接对系统下达指令的请求。\n"
    )
    return Report(query=query, markdown=markdown, citations=[])


@register("intent_router")
class IntentRouter:
    name: str  # 由 @register 注入

    def __init__(
        self,
        llm: LLM | None = None,
        tracer: Tracer | None = None,
        settings: Settings | None = None,
        cascade: IntentCascade | None = None,
    ) -> None:
        self.llm = cast(LLM, llm)
        self.tracer = cast(Tracer, tracer)
        self.settings = cast(Settings, settings)
        self._cascade = cascade

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        self.llm, self.tracer, self.settings = ctx.llm_for(self.name), ctx.tracer, ctx.settings

        if not self.settings.intent_enabled:
            self.tracer.emit("INTENT", "info", "意图识别已关闭，直接放行")
            return bb

        # 已有判定则复用，不重判。两种来源：API 层的预路由（创建 run 前就判过
        # 一次以决定工作流），以及崩溃恢复的 checkpoint。重判既浪费成本，又可能
        # 因模型/阈值变化得到不同结论，让恢复后的运行与原运行不一致。
        cached = bb.scratch.get(INTENT_SCRATCH_KEY)
        if cached is not None:
            try:
                decision = IntentDecision.model_validate(cached)
            except ValidationError:
                # 判定结构损坏时必须重判而不是放行——直接 return 会让一条本该被
                # 拦截的请求因为「缓存读不出来」而畅通无阻。
                self.tracer.emit("INTENT", "info", "已有判定无法解析，重新识别")
                decision = await self._classify(bb.query)
            else:
                if self._should_complete(decision):
                    # 预路由跑在 HTTP 同步段上，刻意用 enable_llm=False 换响应时间，
                    # 因此它的弃权只代表「便宜的两级没定夺」，不代表 L3 也定不了。
                    # 这里是异步执行段，补跑 L3 既不影响创建研究的延迟，也让
                    # INTENT_LLM_FALLBACK 这个开关在 HTTP 路径上真正有意义。
                    self.tracer.emit("INTENT", "start", "前序判定弃权，升级到 LLM 复判…")
                    decision = await self._classify(bb.query)
                else:
                    self.tracer.emit("INTENT", "info", "复用已有的意图判定")
        else:
            self.tracer.emit("INTENT", "start", "识别请求意图…")
            decision = await self._classify(bb.query)
        bb.scratch[INTENT_SCRATCH_KEY] = decision.model_dump(mode="json")

        if decision.blocked:
            bb.scratch[INTENT_BLOCKED_KEY] = True
            bb.report = blocked_report(bb.query, decision)
            # 先写 report 再请求终止：引擎的 halt 会跳过包括 synthesizer 在内的
            # 全部剩余步骤，此时若没有报告，require_report 会把这次拒识变成一次
            # 「运行失败」，用户看到的是错误而不是拒识说明。
            bb.scratch[HALT_SCRATCH_KEY] = True
            bb.scratch[HALT_REASON_KEY] = f"intent gate: {decision.risk}"
            self.tracer.emit(
                "INTENT",
                "info",
                f"风险意图拦截：{decision.risk}（{decision.tier} 层）",
                data={
                    "category": "intent_gate",
                    "blocked": True,
                    "risk": decision.risk,
                    "tier": decision.tier,
                    "signals": [signal.model_dump() for signal in decision.signals],
                },
            )
            return bb

        route = plan_route(
            decision,
            requested_workflow=bb.scratch.get("requested_workflow") or None,
        )
        if route.applied and route.max_sub_questions is not None:
            # 只收紧不放宽：意图建议 6 个子问题但用户配置上限是 3 时，以用户配置为准。
            bb.scratch[INTENT_SUB_QUESTION_KEY] = min(
                route.max_sub_questions, self.settings.max_sub_questions
            )
        bb.scratch[INTENT_ROUTE_KEY] = {
            "applied": route.applied,
            "workflow": route.workflow,
            "max_sub_questions": route.max_sub_questions,
            "reason": route.reason,
        }
        self.tracer.emit(
            "INTENT",
            "info",
            f"意图「{decision.intent}」p={decision.confidence:.2f}（{decision.tier} 层）"
            f"｜{route.describe()}",
            data={
                "category": "intent_gate",
                "blocked": False,
                "intent": decision.intent,
                "confidence": decision.confidence,
                "tier": decision.tier,
                "escalated": decision.escalated,
                "scores": decision.scores,
                "route": bb.scratch[INTENT_ROUTE_KEY],
            },
        )
        return bb

    def _should_complete(self, decision: IntentDecision) -> bool:
        """既有判定是否值得补跑 L3？

        只补「便宜的两级弃权了」这一种情况（tier=fallback 且未拦截）。已有明确
        结论的不补——那既浪费成本，又会让崩溃恢复后的运行与原运行结论不一致
        （恢复一致性是这个缓存最初存在的理由）。已拦截的更不补：拒识是终局，
        再判一次只可能被 LLM 洗白，违反「风险只能收紧」。
        """
        if not self.settings.intent_llm_fallback:
            return False
        return decision.tier == "fallback" and not decision.blocked and not decision.escalated

    async def _classify(self, query: str) -> IntentDecision:
        cascade = self._cascade or IntentCascade(
            llm=self.llm, enable_llm=self.settings.intent_llm_fallback
        )
        return await cascade.classify_query(query)
