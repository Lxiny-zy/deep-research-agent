"""意图识别：把「用户想干什么」与「这段外部文本想让模型干什么」变成可判定的结构。

两条通道共用同一套级联骨架（规则 → 本地统计模型 → LLM 兜底），但判定对象不同：

- **输入侧**（``classify_query``）：用户 query → 任务意图（决定走哪条工作流、给多少预算）
  + 风险意图（越狱 / 诱导泄露系统提示 / 与研究无关的指令劫持 → 拒识或降级）。
- **来源侧**（``classify_source_intent``）：检索到的网页文本 → 这段内容的意图是
  「提供信息」还是「指挥模型」。它只用于**收紧** ``SourcePolicy`` 的判定，
  永远不能把规则already拒绝的来源放行（见 ``deep_research/guardrails.py``）。

级联的意义是成本阶梯而非准确率堆叠：L1 规则零成本高精度但覆盖窄，L3 LLM 覆盖宽
但每次都要 token 与网络往返，L2 本地统计模型（TF-IDF + 逻辑回归，纯 Python 推理）
吃掉中间的大部分常规流量，只把低置信样本让给 L3。
"""

from .cascade import (
    IntentCascade,
    classify_query,
    classify_source_intent,
)
from .clarify import plan_clarification
from .context import (
    FollowupResolution,
    detect_context_dependency,
    resolve_followup,
    resolve_followup_detailed,
)
from .model import IntentModelBundle, TextClassifier, load_bundled_model
from .slots import extract_slots, extract_slots_by_rule
from .types import (
    QUERY_INTENT_ALIASES,
    QUERY_INTENTS,
    RISK_INTENT_ALIASES,
    RISK_INTENTS,
    SOURCE_INTENT_ALIASES,
    SOURCE_INTENTS,
    ClarificationRequest,
    ContextResolution,
    ConversationTurn,
    ExecutionPolicy,
    IntentDecision,
    IntentExecutionPolicy,
    IntentSignal,
    IntentSlots,
    IntentTier,
    QueryIntent,
    RiskIntent,
    SourceIntent,
    execution_policy_for,
    normalize_query_intent,
    normalize_risk_intent,
    normalize_source_intent,
)

__all__ = [
    "ClarificationRequest",
    "ContextResolution",
    "ConversationTurn",
    "ExecutionPolicy",
    "IntentCascade",
    "IntentDecision",
    "IntentExecutionPolicy",
    "IntentSignal",
    "IntentModelBundle",
    "IntentSlots",
    "IntentTier",
    "QUERY_INTENTS",
    "QUERY_INTENT_ALIASES",
    "QueryIntent",
    "RISK_INTENTS",
    "RISK_INTENT_ALIASES",
    "RiskIntent",
    "SOURCE_INTENTS",
    "SOURCE_INTENT_ALIASES",
    "SourceIntent",
    "TextClassifier",
    "classify_query",
    "classify_source_intent",
    "detect_context_dependency",
    "extract_slots",
    "extract_slots_by_rule",
    "load_bundled_model",
    "execution_policy_for",
    "normalize_query_intent",
    "normalize_risk_intent",
    "normalize_source_intent",
    "plan_clarification",
    "FollowupResolution",
    "resolve_followup",
    "resolve_followup_detailed",
]
