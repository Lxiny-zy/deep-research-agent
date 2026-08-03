"""意图标签体系与判定结果模型。

标签设计原则（面试常问「为什么是这几个类」）：
  1. **每个标签都要有下游动作**。``comparative`` 对应「必须多侧面 + 反思补洞」，
     ``factual_lookup`` 对应「省掉规划与反思」——不能落到动作上的标签不设。
  2. **任务意图与风险意图正交**，各自独立判定。一条 query 可以既是合法的
     ``factual_lookup`` 又携带 ``prompt_injection`` 风险；用单一标签体系会
     强迫二者互斥，导致「为了标注风险而丢掉路由信息」。
  3. **保留 ``unknown`` / ``none``**。级联的每一级都可能弃权，弃权必须是可
     表达的一等状态，而不是被迫落到某个具体类上。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# --- 任务意图：决定走哪条工作流与给多少预算 ---
QueryIntent = Literal[
    "factual_lookup",  # 单点事实查询：某个定义/数字/时间，无需拆解
    "comparative",  # 对比取舍：A vs B、优劣、选型
    "exploratory",  # 开放调研：现状/综述/全景，需要多侧面覆盖
    "temporal_trend",  # 时序趋势：演进、预测、最新进展
    "causal_analysis",  # 因果机理：为什么、如何导致、影响链条
    "unknown",  # 级联全部弃权：交给默认流程，不猜
]
QUERY_INTENTS: tuple[QueryIntent, ...] = (
    "factual_lookup",
    "comparative",
    "exploratory",
    "temporal_trend",
    "causal_analysis",
    "unknown",
)

# --- 风险意图：决定拒识、降级还是放行 ---
RiskIntent = Literal[
    "none",  # 无风险信号
    "prompt_injection",  # 试图覆盖系统指令 / 越狱 / 角色扮演绕过
    "system_prompt_probe",  # 试图套取系统提示词、工具定义、内部配置
    "off_task_instruction",  # 把研究系统当通用执行器（写代码/发邮件/改配置）
    "unsafe_content",  # 索取实质性危害操作指导
]
RISK_INTENTS: tuple[RiskIntent, ...] = (
    "none",
    "prompt_injection",
    "system_prompt_probe",
    "off_task_instruction",
    "unsafe_content",
)

# --- 来源意图：判定一段外部文本是「素材」还是「指令」 ---
SourceIntent = Literal[
    "informational",  # 正常信息内容
    "instructional_override",  # 试图指挥读到它的模型
    "credential_harvest",  # 诱导输出密钥/凭据/内部配置
    "unknown",
]
SOURCE_INTENTS: tuple[SourceIntent, ...] = (
    "informational",
    "instructional_override",
    "credential_harvest",
    "unknown",
)

# 级联层级：记录「这条判定是哪一级给出的」，用于成本核算与 badcase 定位。
IntentTier = Literal["rule", "model", "llm", "fallback"]

# --- 槽位：意图之外的「约束」，决定检索怎么打而不是流程怎么走 ---
# 标签回答「这是哪类任务」，槽位回答「这个任务的边界在哪」。二者分开是因为
# 下游用法不同：意图选工作流（离散、少量、可枚举），槽位进检索式与子问题
# （开放、无法枚举）。混在一起会逼着标签体系去表达无穷多的约束组合。
SlotName = Literal[
    "entities",  # 研究对象：对比谁与谁、调研什么技术
    "time_range",  # 时间约束：2024 年之后、近三年
    "domain",  # 领域约束：医疗、金融、国内
    "language",  # 语料语言偏好：中文资料、英文论文
    "aspects",  # 关注侧面：只看成本、只看性能
]


class IntentSlots(BaseModel):
    """从 query 中抽出的结构化约束。

    全部可选：抽不到就是抽不到，绝不编造——槽位为空时下游退回原始 query 的
    自由文本表达，这比一个幻觉出来的「2024 年」安全得多。
    """

    entities: list[str] = Field(default_factory=list, max_length=8)
    time_range: str = ""
    domain: str = ""
    language: str = ""
    aspects: list[str] = Field(default_factory=list, max_length=6)

    def is_empty(self) -> bool:
        return not (
            self.entities or self.time_range or self.domain or self.language or self.aspects
        )

    def describe(self) -> str:
        """人类可读的约束摘要；给 Planner 的 prompt 与前端展示共用。"""
        parts: list[str] = []
        if self.entities:
            parts.append(f"研究对象：{'、'.join(self.entities)}")
        if self.time_range:
            parts.append(f"时间范围：{self.time_range}")
        if self.domain:
            parts.append(f"领域：{self.domain}")
        if self.language:
            parts.append(f"语料语言：{self.language}")
        if self.aspects:
            parts.append(f"关注侧面：{'、'.join(self.aspects)}")
        return "；".join(parts)


class ClarificationRequest(BaseModel):
    """需要向用户澄清时的结构化产物。

    ``question`` 是要问的话，``options`` 是候选解读。给选项而不只给问题，
    是因为开放式追问（「你想问什么？」）把认知负担全推给用户，而候选解读
    让用户一次点选就能消歧。
    """

    question: str
    options: list[str] = Field(default_factory=list, max_length=4)
    reason: str = ""


class ConversationTurn(BaseModel):
    """一轮历史对话。只保留判定意图所必需的字段。

    刻意不存完整报告：多轮消解需要的是「上一轮问了什么、被判成什么意图」，
    把整份报告塞进上下文既昂贵又会稀释信号。
    """

    query: str = Field(max_length=2000)
    intent: str = "unknown"
    # 上一轮抽到的槽位：指代消解的主要依据（"那第二个呢" 要回填 entities）
    slots: IntentSlots = Field(default_factory=IntentSlots)


class IntentSignal(BaseModel):
    """一条命中的证据，便于把判定讲清楚（而不是只给一个标签）。"""

    tier: IntentTier
    code: str  # 规则 code / 模型特征名 / llm
    detail: str = ""


class IntentDecision(BaseModel):
    """一次意图判定的完整结果。

    同时携带任务意图与风险意图：二者正交，互不覆盖（见模块 docstring）。
    ``tier`` 记录最终由哪一级决定，``escalated`` 记录是否真的调用了 LLM——
    这两个字段直接支撑「X% 请求 0 token 解决」这类成本结论。
    """

    intent: str = "unknown"
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    tier: IntentTier = "fallback"
    risk: RiskIntent = "none"
    risk_confidence: float = Field(0.0, ge=0.0, le=1.0)
    signals: list[IntentSignal] = Field(default_factory=list)
    escalated: bool = False  # 是否升级到了 LLM 层（用于成本统计）
    scores: dict[str, float] = Field(default_factory=dict)  # 本地模型的类别分布（可解释性）
    reason: str = ""
    # --- 多轮与槽位 ---
    slots: IntentSlots = Field(default_factory=IntentSlots)
    # 本轮是否依赖上下文才判定出来（省略/指代消解）。用于可解释性与评测分档。
    context_resolved: bool = False
    # 消解后的完整问题：「那第二个呢」→「Qdrant 在 RAG 场景的表现如何」。
    # 为空表示无需消解，下游直接用原 query。
    resolved_query: str = ""
    # 需要澄清时非空；此时不应直接执行研究。
    clarification: ClarificationRequest | None = None

    @property
    def blocked(self) -> bool:
        """是否应当拒绝执行。仅高危风险意图触发，任务意图永不导致拒绝。"""
        return self.risk in ("prompt_injection", "system_prompt_probe", "unsafe_content")

    @property
    def needs_clarification(self) -> bool:
        """是否应当先澄清再研究。拒识优先于澄清——被拒的请求没有澄清的必要。"""
        return self.clarification is not None and not self.blocked

    def effective_query(self, original: str) -> str:
        """下游真正该研究的问题：消解过就用消解结果，否则用原文。"""
        return self.resolved_query or original
