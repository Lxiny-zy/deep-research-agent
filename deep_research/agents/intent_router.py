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
from ..intent.slots import ENTITY_INTENTS, extract_slots
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
# 抽到的槽位（约束），供 Planner 注入到拆解 prompt。
INTENT_SLOTS_KEY = "intent_slots"
# 多轮消解后的完整问题。非空时下游必须用它而不是 bb.query——
# 原文可能是「那第二个呢」，拿去检索必然打空。
INTENT_RESOLVED_QUERY_KEY = "intent_resolved_query"
# 澄清请求。存在即表示本次运行没有执行研究，而是在等用户补充信息。
INTENT_CLARIFY_KEY = "intent_clarification"

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


def clarification_report(query: str, decision: IntentDecision) -> Report:
    """为需要澄清的请求产出追问报告。

    与拒识共用「不执行研究但给一份可读说明」的产品语义，因此走同一条 halt 路径。
    区别在措辞与可操作性：拒识是终局，澄清是**邀请用户补充信息后重来**，
    所以必须给出具体的候选解读，而不是一句「请说清楚点」——
    开放式追问把认知负担全推回用户，正是澄清体验最差的做法。
    """
    request = decision.clarification
    assert request is not None  # 调用方已用 needs_clarification 判过
    options = "\n".join(f"{i + 1}. {option}" for i, option in enumerate(request.options))
    slots = decision.slots.describe()
    markdown = (
        "# 需要你补充一点信息\n\n"
        "## 我的疑问\n"
        f"{request.question}\n\n"
        "## 可能的理解\n"
        f"{options or '（暂无候选，请直接补充描述）'}\n\n"
        "## 我已经读到的\n"
        f"- 原始提问：{query}\n"
        f"{f'- 已识别的约束：{slots}' if slots else '- 暂未识别出明确的约束条件'}\n\n"
        "## 怎么继续\n"
        "选一个方向、或直接把问题说得更具体一些，重新提交即可。"
        "研究没有开始，因此没有消耗检索与生成成本。\n"
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
                    fresh = await self._classify(bb.query)
                    # bb.query 已是消解后的完整问题（API 建 run 时写入），复判不必
                    # 重做消解；但消解痕迹必须搬过来——run 详情与下一轮追问的历史
                    # 都从这份判定读 resolved_query，复判只该刷新分类结论，
                    # 不该把「这次研究的到底是哪句话」洗掉。
                    fresh.resolved_query = decision.resolved_query
                    fresh.context_resolved = decision.context_resolved
                    decision = fresh
                elif self._needs_entities(decision):
                    # 预路由同样跳过了实体抽取（延迟敏感）。这里补上——Planner
                    # 马上就要用它拆子问题，而这一段不在用户的等待路径上。
                    self.tracer.emit("INTENT", "start", "补充抽取对比实体…")
                    decision = await self._complete_entities(decision, bb.query)
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

        clarification = decision.clarification if decision.needs_clarification else None
        if clarification is not None:
            bb.scratch[INTENT_CLARIFY_KEY] = clarification.model_dump(mode="json")
            bb.report = clarification_report(bb.query, decision)
            # 与拒识同一条 halt 路径：先写报告再请求终止，否则 require_report
            # 会把「需要澄清」变成一次运行失败。
            bb.scratch[HALT_SCRATCH_KEY] = True
            bb.scratch[HALT_REASON_KEY] = "intent gate: needs clarification"
            self.tracer.emit(
                "INTENT",
                "info",
                f"意图存在歧义，请求澄清：{clarification.question}",
                data={
                    "category": "intent_gate",
                    "blocked": False,
                    "needs_clarification": True,
                    "clarification": bb.scratch[INTENT_CLARIFY_KEY],
                    "tier": decision.tier,
                },
            )
            return bb

        # 槽位写进黑板供 Planner 使用（见 planner.step 的约束注入）。
        if not decision.slots.is_empty():
            bb.scratch[INTENT_SLOTS_KEY] = decision.slots.model_dump(mode="json")
        # 消解后的完整问题：多轮追问的原文（「那第二个呢」）无法独立检索，
        # 下游必须拿消解结果去研究。
        if decision.resolved_query:
            bb.scratch[INTENT_RESOLVED_QUERY_KEY] = decision.resolved_query

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
                "slots": decision.slots.model_dump(mode="json"),
                "context_resolved": decision.context_resolved,
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

    def _needs_entities(self, decision: IntentDecision) -> bool:
        """该判定是否还缺 Planner 要用的对比实体？

        只补 comparative——其余意图的下游不需要实体（见 cascade 的同名判断）。
        已经抽到实体的不重抽：预路由若跑过（比如调用方显式要求），这里就没必要
        再花一次。
        """
        if not self.settings.intent_llm_fallback:
            return False
        return (
            decision.intent in ENTITY_INTENTS
            and not decision.slots.entities
            and not decision.blocked
        )

    async def _complete_entities(self, decision: IntentDecision, query: str) -> IntentDecision:
        """在异步段补抽实体，保留其余判定字段不变。

        只覆盖 slots：意图、风险、置信度、层级都是预路由已经定好的，重抽实体
        不该动它们——否则恢复一致性（同一个 run 两次执行得到同样结论）就没了。
        """
        target = decision.resolved_query or query
        decision.slots = await extract_slots(target, llm=self.llm, use_llm=True)
        decision.escalated = True
        return decision

    async def _classify(self, query: str) -> IntentDecision:
        cascade = self._cascade or IntentCascade(
            llm=self.llm, enable_llm=self.settings.intent_llm_fallback
        )
        # 走完整的 classify（含槽位与澄清判定），而不是只跑意图级联。
        # history 不在这里传：多轮消解发生在 API 预路由（那时才有会话上下文），
        # 判定结果连同消解后的问题一起进 checkpoint，本角色只是复用或补判。
        return await cascade.classify(query)
