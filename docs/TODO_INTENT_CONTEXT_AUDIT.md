# 上下文消解结果可回放（已完成 2026-08-20）

> **状态：已落地。** `ContextResolution` 已挂在 `IntentDecision.context_resolution` 上，
> 由 `intent/context.py` 的 `resolve_followup_detailed()` 直接产出（而不是在 cascade 里
> 反推），因此 `reason` 是改写器自己给的理由、`history_used` 是 `render_history()` 实际
> 使用的窗口（共用 `recent_history()`，不再硬编码 `[-3:]`）。
> 回归测试见 `tests/test_intent_context_replay.py`——四类错误各有一条断言。
>
> 下面保留原始设计记录。

## 背景

线上用户可能投诉“系统把刚才那个数据库理解错了”。当前意图识别流程能够得到
`resolved_query`、`context_resolved` 和 `IntentSignal`，但不足以完整回放这次消解。

## 当前缺口

- `deep_research/intent/context.py` 的 `resolve_followup()` 只返回
  `(resolved_query, signal, did_resolve)`。
- 没有持久化本轮原始问题和消解器实际使用的历史快照。
- `ResolvedQuery.reason` 没有向上层审计结果传递。
- `IntentDecision.tier` 表示最终意图分类使用的 L1/L2/L3，不能单独表示上下文消解使用的层级。

## 建议的最小结构

后续在 `IntentDecision` 中增加 `context_resolution`：

```python
class ContextResolution(BaseModel):
    raw_query: str = Field(max_length=2000)
    history_used: list[ConversationTurn] = Field(default_factory=list, max_length=3)
    dependency_signal: IntentSignal | None = None
    resolved_query: str = Field("", max_length=2000)
    context_resolved: bool = False
    resolver_tier: Literal["none", "llm", "fallback"] = "none"
    resolver_version: str = "context-v1"
    reason: str = Field("", max_length=200)
```

其中 `history_used` 应保存 `render_history()` 实际使用的最近 3 轮，而不是只保存客户端传来的历史数量。
没有成功消解时，`resolved_query` 保持空字符串，下游继续使用原始 query，和当前
`IntentDecision.effective_query()` 语义一致。

## 回放判断

1. `raw_query` 或 `history_used` 错：输入上下文或客户端会话问题。
2. `dependency_signal` 错：L1 上下文依赖检测问题。
3. 信号正确但 `resolved_query` 错：L3 上下文改写问题。
4. `resolved_query` 正确但最终意图错：查看现有 `IntentDecision.tier`、`scores`、`signals` 和 `escalated`，定位后续 L1/L2/L3 分类问题。

## 后续实现

- 将 `resolve_followup()` 的元组返回值改为 `ContextResolution`。
- 在 `IntentDecision` 和运行详情持久化 `context_resolution`。
- 记录 `run_id/request_id`、模型或提示词版本，并补充“数据库指代错误”的回归样例。
