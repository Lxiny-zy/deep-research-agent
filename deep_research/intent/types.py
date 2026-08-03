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

    @property
    def blocked(self) -> bool:
        """是否应当拒绝执行。仅高危风险意图触发，任务意图永不导致拒绝。"""
        return self.risk in ("prompt_injection", "system_prompt_probe", "unsafe_content")
