"""意图级联的运行期遥测：让本地模型的分布漂移**可见**。

L2 是一份随包分发的静态 TF-IDF + 逻辑回归权重。线上 query 分布一旦偏离训练集，
它不会报错——只会安静地降低置信度，把流量推给 L3（涨钱）或直接弃权走默认流程
（降质）。两种退化都不产生任何异常，因此不加指标就等于没有信号。

这里只暴露三样东西，都能从既有判定结果里零成本读出：

* **级联分流占比**：`deep_research_intent_decisions_total{tier}`。与
  ``eval/intent_eval.py`` 的离线基线对比即可发现「L3 兜底占比翻倍」这类漂移；
* **L2 置信度均值**：sum/count 一对计数器（Prometheus 的惯例做法，avg 由查询端算）。
  均值持续下滑说明输入分布已经偏离训练集；
* **弃权率**：`intent="unknown"` 的判定占比。它直接对应「用户拿到的是默认流程
  而不是被识别出的流程」。

标签词汇表刻意保持有界（tier 4 种 × intent 十余种），避免高基数把注册表撑爆。
"""

from __future__ import annotations

from ..metrics import metrics
from .types import IntentDecision

DECISIONS = "deep_research_intent_decisions_total"
L2_CONFIDENCE_SUM = "deep_research_intent_model_confidence_sum"
L2_CONFIDENCE_COUNT = "deep_research_intent_model_confidence_count"
ESCALATIONS = "deep_research_intent_llm_escalations_total"


def record_decision(decision: IntentDecision) -> None:
    """把一次判定计入指标。失败绝不能影响判定本身。"""
    try:
        metrics.inc(DECISIONS, {"tier": decision.tier, "intent": decision.intent})
        if decision.tier == "model":
            # 只统计 L2 真正拍板的那些：低于阈值的样本会继续下沉到 L3，
            # 把它们的低置信度混进来会让均值同时反映「模型没把握」和「模型判错」。
            metrics.inc(L2_CONFIDENCE_SUM, {}, decision.confidence)
            metrics.inc(L2_CONFIDENCE_COUNT, {})
        if decision.escalated:
            metrics.inc(ESCALATIONS, {"intent": decision.intent})
    except Exception:  # pragma: no cover - 指标永远不应让主链路失败
        pass


def snapshot() -> dict[str, float]:
    """当前累计值（供测试与离线对照读取）。"""
    return metrics.snapshot_counters(
        (DECISIONS, L2_CONFIDENCE_SUM, L2_CONFIDENCE_COUNT, ESCALATIONS)
    )
