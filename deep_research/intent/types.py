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

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

# --- 任务意图：决定走哪条工作流与给多少预算 ---
QueryIntent = Literal[
    # AI4S/HSI domain intents share the task-intent channel for auditable
    # routing and policy snapshots.
    "literature_review",
    "method_comparison",
    "benchmark_survey",
    "reproducibility_check",
    "dataset_discovery",
    "factual_lookup",  # 单点事实查询：某个定义/数字/时间，无需拆解
    "comparative",  # 对比取舍：A vs B、优劣、选型
    "exploratory",  # 开放调研：现状/综述/全景，需要多侧面覆盖
    "temporal_trend",  # 时序趋势：演进、预测、最新进展
    "causal_analysis",  # 因果机理：为什么、如何导致、影响链条
    "definition_explanation",  # 定义/解释：是什么、概念、原理概览
    "procedural_guidance",  # 方法/步骤：如何做、配置、实施指南
    "recommendation",  # 推荐决策：应该选什么、最佳方案
    "fact_check",  # 事实核查：验证说法、核实数据与来源
    "summarization",  # 摘要归纳：总结给定主题或材料
    "multi_hop_research",  # 多跳研究：跨实体、跨来源关联推理
    "monitoring",  # 持续/最新监测：关注变化并强调新鲜度
    "unknown",  # 级联全部弃权：交给默认流程，不猜
]
QUERY_INTENTS: tuple[QueryIntent, ...] = (
    "literature_review",
    "method_comparison",
    "benchmark_survey",
    "reproducibility_check",
    "dataset_discovery",
    "factual_lookup",
    "comparative",
    "exploratory",
    "temporal_trend",
    "causal_analysis",
    "definition_explanation",
    "procedural_guidance",
    "recommendation",
    "fact_check",
    "summarization",
    "multi_hop_research",
    "monitoring",
    "unknown",
)

# 旧模型、旧客户端和常见 LLM 输出使用过多套标签命名。规范化只在
# 已知别名上生效，未知值仍返回 ``unknown``（模型内部的任意 toy label
# 不会被误改，见 ``model.TextClassifier`` 的兼容逻辑）。
QUERY_INTENT_ALIASES: dict[str, QueryIntent] = {
    "literature": "literature_review",
    "lit_review": "literature_review",
    "literature-review": "literature_review",
    "method_compare": "method_comparison",
    "method-comparison": "method_comparison",
    "benchmark": "benchmark_survey",
    "benchmark-survey": "benchmark_survey",
    "reproducibility": "reproducibility_check",
    "reproducibility-check": "reproducibility_check",
    "dataset": "dataset_discovery",
    "dataset-discovery": "dataset_discovery",
    "fact": "factual_lookup",
    "factual": "factual_lookup",
    "lookup": "factual_lookup",
    "qa": "factual_lookup",
    "question_answering": "factual_lookup",
    "compare": "comparative",
    "comparison": "comparative",
    "contrastive": "comparative",
    "research": "exploratory",
    "overview": "exploratory",
    "survey": "exploratory",
    "trend": "temporal_trend",
    "forecast": "temporal_trend",
    "causal": "causal_analysis",
    "why": "causal_analysis",
    "definition": "definition_explanation",
    "explanation": "definition_explanation",
    "explain": "definition_explanation",
    "how_to": "procedural_guidance",
    "howto": "procedural_guidance",
    "procedure": "procedural_guidance",
    "tutorial": "procedural_guidance",
    "recommend": "recommendation",
    "decision": "recommendation",
    "advice": "recommendation",
    "verification": "fact_check",
    "factcheck": "fact_check",
    "fact-check": "fact_check",
    "summary": "summarization",
    "summarize": "summarization",
    "synthesis": "summarization",
    "multi-hop": "multi_hop_research",
    "multi_hop": "multi_hop_research",
    "multihop": "multi_hop_research",
    "multi_hop_researching": "multi_hop_research",
    "deep_research": "multi_hop_research",
    "watch": "monitoring",
    "tracking": "monitoring",
    "monitor": "monitoring",
    "none": "unknown",
    "other": "unknown",
}


def normalize_query_intent(value: Any, *, default: QueryIntent = "unknown") -> QueryIntent:
    """Return a canonical task label while accepting historical spellings.

    The classifier boundary is intentionally forgiving: old JSON bundles and LLM
    adapters can continue returning labels such as ``fact`` or ``summary``. Values
    outside the public vocabulary fail closed to ``default`` instead of leaking an
    arbitrary string into workflow routing.
    """

    if not isinstance(value, str):
        return default
    normalized = value.strip().casefold().replace(" ", "_")
    if normalized in QUERY_INTENTS:
        return normalized  # type: ignore[return-value]
    return QUERY_INTENT_ALIASES.get(normalized, default)


def is_query_intent(value: Any) -> bool:
    """Whether ``value`` is a canonical or legacy task intent label."""

    if not isinstance(value, str):
        return False
    return value.strip().casefold().replace(" ", "_") in QUERY_INTENTS or (
        value.strip().casefold().replace(" ", "_") in QUERY_INTENT_ALIASES
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

RISK_INTENT_ALIASES: dict[str, RiskIntent] = {
    "safe": "none",
    "clean": "none",
    "injection": "prompt_injection",
    "jailbreak": "prompt_injection",
    "prompt-injection": "prompt_injection",
    "system_prompt": "system_prompt_probe",
    "system-prompt": "system_prompt_probe",
    "prompt_probe": "system_prompt_probe",
    "off_task": "off_task_instruction",
    "off-task": "off_task_instruction",
    "unsafe": "unsafe_content",
    "harmful": "unsafe_content",
}


def normalize_risk_intent(value: Any, *, default: RiskIntent = "none") -> RiskIntent:
    if not isinstance(value, str):
        return default
    normalized = value.strip().casefold().replace(" ", "_")
    if normalized in RISK_INTENTS:
        return normalized  # type: ignore[return-value]
    return RISK_INTENT_ALIASES.get(normalized, default)


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

SOURCE_INTENT_ALIASES: dict[str, SourceIntent] = {
    "info": "informational",
    "content": "informational",
    "instruction": "instructional_override",
    "override": "instructional_override",
    "prompt_injection": "instructional_override",
    "credential": "credential_harvest",
    "secrets": "credential_harvest",
    "none": "unknown",
}


def normalize_source_intent(value: Any, *, default: SourceIntent = "unknown") -> SourceIntent:
    if not isinstance(value, str):
        return default
    normalized = value.strip().casefold().replace(" ", "_")
    if normalized in SOURCE_INTENTS:
        return normalized  # type: ignore[return-value]
    return SOURCE_INTENT_ALIASES.get(normalized, default)


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
    "output_format",  # 输出格式：表格、步骤、摘要、引用清单
    "audience",  # 读者/使用者：开发者、管理者、研究人员
    "geography",  # 地域/市场：国内、海外、某个国家或地区
    "source_types",  # 来源偏好：官方、论文、新闻、社区
    "freshness",  # 新鲜度要求：最新、近一年、截至某日
    "evidence_level",  # 证据要求：一般、严格、双源核验
]


class IntentSlots(BaseModel):
    """从 query 中抽出的结构化约束。

    全部可选：抽不到就是抽不到，绝不编造——槽位为空时下游退回原始 query 的
    自由文本表达，这比一个幻觉出来的「2024 年」安全得多。
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    entities: list[str] = Field(
        default_factory=list,
        max_length=8,
        validation_alias=AliasChoices("entities", "subjects", "objects"),
    )
    time_range: str = Field("", validation_alias=AliasChoices("time_range", "time", "date_range"))
    domain: str = Field("", validation_alias=AliasChoices("domain", "topic_domain"))
    language: str = Field("", validation_alias=AliasChoices("language", "source_language"))
    aspects: list[str] = Field(default_factory=list, max_length=6)
    output_format: str = Field(
        "", validation_alias=AliasChoices("output_format", "format", "response_format")
    )
    audience: str = ""
    geography: str = Field("", validation_alias=AliasChoices("geography", "region", "market"))
    source_types: list[str] = Field(
        default_factory=list, max_length=6, validation_alias=AliasChoices("source_types", "sources")
    )
    freshness: str = Field("", validation_alias=AliasChoices("freshness", "recency"))
    evidence_level: str = Field(
        "", validation_alias=AliasChoices("evidence_level", "evidence", "verification_level")
    )

    def is_empty(self) -> bool:
        return not (
            self.entities
            or self.time_range
            or self.domain
            or self.language
            or self.aspects
            or self.output_format
            or self.audience
            or self.geography
            or self.source_types
            or self.freshness
            or self.evidence_level
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
        if self.output_format:
            parts.append(f"输出格式：{self.output_format}")
        if self.audience:
            parts.append(f"面向读者：{self.audience}")
        if self.geography:
            parts.append(f"地域范围：{self.geography}")
        if self.source_types:
            parts.append(f"来源类型：{'、'.join(self.source_types)}")
        if self.freshness:
            parts.append(f"新鲜度：{self.freshness}")
        if self.evidence_level:
            parts.append(f"证据要求：{self.evidence_level}")
        return "；".join(parts)


class ExecutionPolicy(BaseModel):
    """由任务意图推导出的、可审计的执行建议。

    这是建议元数据而非权限：显式 workflow、部署上限和安全门禁始终优先。
    字段全部有保守默认值，因此旧客户端只提交 ``intent`` 时仍可正常运行。
    """

    model_config = ConfigDict(extra="ignore")

    workflow: str | None = None
    max_sub_questions: int | None = Field(default=None, ge=1, le=64)
    max_rounds: int | None = Field(default=None, ge=0, le=16)
    parallelism: int = Field(default=1, ge=1, le=32)
    requires_reflection: bool = True
    requires_corroboration: bool = False
    freshness: Literal["any", "recent", "latest"] = "any"
    answer_mode: str = "report"
    source_strategy: str = "broad"
    rationale: str = ""


# Descriptive alias for callers that prefer the more explicit name.
IntentExecutionPolicy = ExecutionPolicy


_DEFAULT_EXECUTION_POLICY = ExecutionPolicy()


_EXECUTION_POLICIES: dict[str, ExecutionPolicy] = {
    "literature_review": ExecutionPolicy(
        workflow="hsi_review",
        max_sub_questions=8,
        max_rounds=2,
        parallelism=3,
        requires_reflection=True,
        requires_corroboration=True,
        answer_mode="literature_review",
        source_strategy="scholarly_multi_source",
        rationale="scholarly literature review with evidence corroboration",
    ),
    "method_comparison": ExecutionPolicy(
        workflow="hsi_review",
        max_sub_questions=6,
        max_rounds=2,
        parallelism=3,
        requires_reflection=True,
        requires_corroboration=True,
        answer_mode="method_comparison",
        source_strategy="scholarly_multi_source",
        rationale="compare research methods with multi-source evidence",
    ),
    "benchmark_survey": ExecutionPolicy(
        workflow="hsi_review",
        max_sub_questions=8,
        max_rounds=2,
        parallelism=3,
        requires_reflection=True,
        requires_corroboration=True,
        answer_mode="benchmark_survey",
        source_strategy="scholarly_multi_source",
        rationale="benchmark metrics require source and condition checks",
    ),
    "reproducibility_check": ExecutionPolicy(
        workflow="hsi_review",
        max_sub_questions=6,
        max_rounds=2,
        parallelism=2,
        requires_reflection=True,
        requires_corroboration=True,
        answer_mode="reproducibility_check",
        source_strategy="scholarly_multi_source",
        rationale="reproducibility checks require method and experiment evidence",
    ),
    "dataset_discovery": ExecutionPolicy(
        workflow="hsi_review",
        max_sub_questions=6,
        max_rounds=2,
        parallelism=3,
        requires_reflection=True,
        requires_corroboration=True,
        answer_mode="dataset_discovery",
        source_strategy="scholarly_multi_source",
        rationale="dataset discovery requires provenance and reproducibility details",
    ),
    "factual_lookup": ExecutionPolicy(
        workflow="quick",
        max_sub_questions=2,
        max_rounds=0,
        parallelism=1,
        requires_reflection=False,
        answer_mode="brief",
        source_strategy="targeted",
        rationale="单点事实优先快速、定向检索",
    ),
    "definition_explanation": ExecutionPolicy(
        workflow="brief",
        max_sub_questions=2,
        max_rounds=0,
        parallelism=1,
        requires_reflection=False,
        answer_mode="brief",
        source_strategy="official_first",
        rationale="定义和概念解释无需完整深度研究",
    ),
    "procedural_guidance": ExecutionPolicy(
        workflow="brief",
        max_sub_questions=3,
        max_rounds=0,
        parallelism=1,
        requires_reflection=False,
        answer_mode="procedure",
        source_strategy="official_first",
        rationale="实施指导优先给出可执行步骤并采用官方资料",
    ),
    "summarization": ExecutionPolicy(
        workflow="brief",
        max_sub_questions=2,
        max_rounds=0,
        parallelism=1,
        requires_reflection=False,
        answer_mode="summary",
        source_strategy="targeted",
        rationale="摘要归纳优先保留主线和引用",
    ),
    "comparative": ExecutionPolicy(
        workflow="deep",
        max_sub_questions=4,
        max_rounds=2,
        parallelism=2,
        requires_reflection=True,
        requires_corroboration=True,
        answer_mode="comparison",
        source_strategy="multi_source",
        rationale="对比需要逐项证据和补洞",
    ),
    "recommendation": ExecutionPolicy(
        workflow="deep",
        max_sub_questions=5,
        max_rounds=2,
        parallelism=2,
        requires_reflection=True,
        requires_corroboration=True,
        answer_mode="recommendation",
        source_strategy="multi_source",
        rationale="推荐需要显式权衡和证据核验",
    ),
    "fact_check": ExecutionPolicy(
        workflow="fact_check",
        max_sub_questions=4,
        max_rounds=1,
        parallelism=2,
        requires_reflection=True,
        requires_corroboration=True,
        freshness="recent",
        answer_mode="verification",
        source_strategy="multi_source",
        rationale="事实核查优先独立来源和反证",
    ),
    "exploratory": ExecutionPolicy(
        workflow="teams",
        max_sub_questions=6,
        max_rounds=0,
        parallelism=4,
        requires_reflection=False,
        answer_mode="report",
        source_strategy="broad",
        rationale="开放调研以多团队并行覆盖替代串行反思补洞",
    ),
    "multi_hop_research": ExecutionPolicy(
        workflow="teams",
        max_sub_questions=8,
        max_rounds=0,
        parallelism=4,
        requires_reflection=False,
        requires_corroboration=True,
        answer_mode="report",
        source_strategy="multi_source",
        rationale="跨实体关联采用多跳并行和交叉验证，不声明模板不存在的反思阶段",
    ),
    "temporal_trend": ExecutionPolicy(
        workflow="deep",
        max_sub_questions=5,
        max_rounds=2,
        parallelism=3,
        requires_reflection=True,
        requires_corroboration=True,
        freshness="recent",
        answer_mode="trend",
        source_strategy="multi_source",
        rationale="时序问题按时间切片并交叉印证",
    ),
    "monitoring": ExecutionPolicy(
        workflow="monitoring",
        max_sub_questions=5,
        max_rounds=0,
        parallelism=3,
        requires_reflection=False,
        requires_corroboration=True,
        freshness="latest",
        answer_mode="monitoring",
        source_strategy="multi_source",
        rationale="监测类问题以有界 fan-out 强调最新状态和变化告警",
    ),
    "causal_analysis": ExecutionPolicy(
        workflow="deep",
        max_sub_questions=4,
        max_rounds=2,
        parallelism=2,
        requires_reflection=True,
        requires_corroboration=True,
        answer_mode="causal",
        source_strategy="multi_source",
        rationale="因果分析需要机制链和反例检查",
    ),
}


def execution_policy_for(intent: Any) -> ExecutionPolicy:
    """Return a detached policy for a canonical or legacy intent label."""

    canonical = normalize_query_intent(intent)
    policy = _EXECUTION_POLICIES.get(canonical, _DEFAULT_EXECUTION_POLICY)
    return policy.model_copy(deep=True)


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


class ContextResolution(BaseModel):
    """可回放的多轮上下文消解元数据。

    旧调用方仍可只使用 ``context_resolved`` / ``resolved_query``；该结构是
    增量审计信息，不改变原有消解 API。
    """

    raw_query: str = Field("", max_length=2000)
    history_used: list[ConversationTurn] = Field(default_factory=list, max_length=3)
    dependency_signal: IntentSignal | None = None
    resolved_query: str = Field("", max_length=2000)
    context_resolved: bool = False
    resolver_tier: Literal["none", "llm", "fallback"] = "none"
    resolver_version: str = "context-v1"
    reason: str = Field("", max_length=200)


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

    model_config = ConfigDict(extra="ignore")

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
    # 可序列化的执行建议。旧 checkpoint 没有该字段时使用保守默认值，
    # 新分类器会按 canonical intent 填充对应策略。
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    context_resolution: ContextResolution | None = None
    intent_version: str = "intent-v2"

    @model_validator(mode="before")
    @classmethod
    def _default_execution_policy(cls, data: Any) -> Any:
        """Fill the policy only when the serialized payload omits it.

        Comparing a materialized ``default_factory`` value in ``model_post_init``
        cannot distinguish an omitted field from a caller's explicit policy that
        happens to equal the conservative default.  The before-validator keeps
        explicit checkpoint/API values untouched while still upgrading old
        payloads that have no policy snapshot.
        """
        if not isinstance(data, dict) or "execution_policy" in data:
            return data
        raw_intent = data.get("intent", "unknown")
        normalized_intent = (
            raw_intent.strip().casefold().replace(" ", "_") if isinstance(raw_intent, str) else ""
        )
        if normalized_intent in SOURCE_INTENTS:
            canonical: QueryIntent = "unknown"
        else:
            canonical = normalize_query_intent(raw_intent)
        payload = dict(data)
        payload["execution_policy"] = execution_policy_for(canonical)
        return payload

    def model_post_init(self, __context: Any) -> None:
        """Normalize known legacy labels and derive a policy without breaking unknowns."""

        raw = self.intent.strip().casefold().replace(" ", "_")
        # ``IntentDecision`` is also used by the source-side classifier. Keep
        # source labels intact; they are intentionally a separate vocabulary.
        if raw in SOURCE_INTENTS:
            self.intent = raw
            canonical = "unknown"
        else:
            canonical = normalize_query_intent(self.intent)
            # Fail closed for arbitrary legacy/model strings. Custom workflow
            # names belong in ``requested_workflow``, never in the intent namespace.
            self.intent = canonical

    @property
    def policy(self) -> ExecutionPolicy:
        """Short alias retained for callers that prefer ``decision.policy``."""

        return self.execution_policy

    @property
    def canonical_intent(self) -> QueryIntent:
        return normalize_query_intent(self.intent)

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
