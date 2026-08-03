// 意图判定面板：把「识别到什么意图 / 哪一级给出的 / 依据是什么 / 是否被拦截」
// 摊开给用户看。这是安全审计主线的一部分——判定必须可解释，而不是一个黑箱标签。
import type { IntentDecision, IntentSlots, IntentTier } from '../types'
import { AppIcon } from './AppIcon'

const INTENT_LABELS: Record<string, string> = {
  factual_lookup: '事实查询',
  comparative: '对比取舍',
  exploratory: '开放调研',
  temporal_trend: '时序趋势',
  causal_analysis: '因果分析',
  unknown: '未定',
}

const RISK_LABELS: Record<string, string> = {
  none: '无风险',
  prompt_injection: '提示词注入',
  system_prompt_probe: '系统提示词探测',
  off_task_instruction: '越权指令',
  unsafe_content: '危害内容',
}

// 每一级的成本注记是这个面板的重点：它把「级联为什么值得」直接展示给用户。
const TIER_META: Record<IntentTier, { label: string; note: string }> = {
  rule: { label: 'L1 规则', note: '确定性匹配 · 0 token' },
  model: { label: 'L2 本地模型', note: 'TF-IDF + 逻辑回归 · 0 token' },
  llm: { label: 'L3 大模型', note: '仅低置信样本升级 · 有成本' },
  fallback: { label: '弃权', note: '各级均未定夺 · 走默认流程' },
}

// 与后端 IntentDecision.blocked 保持一致：只有这三类真的会拦截请求。
// off_task_instruction 会被记录并展示，但流程照常执行——它更可能是表述不当的
// 正常请求，不值得为此拒绝服务。这份名单与 types.py 的 blocked 属性是一对
// 必须同步的定义：早期版本这里用的是 risk !== 'none'，于是一条正常跑完的
// off_task 请求会在 UI 上被写成「已在研究开始前拦截」，与事实相反。
const BLOCKING_RISKS = new Set(['prompt_injection', 'system_prompt_probe', 'unsafe_content'])

const SLOT_LABELS: Array<[keyof IntentSlots, string]> = [
  ['entities', '研究对象'],
  ['time_range', '时间范围'],
  ['domain', '领域'],
  ['language', '语料语言'],
  ['aspects', '关注侧面'],
]

/** 把槽位对象摊平成 [中文标签, 值] 列表，跳过空槽位。 */
function slotRows(slots: IntentSlots | undefined): Array<[string, string]> {
  if (!slots) return []
  const rows: Array<[string, string]> = []
  for (const [key, label] of SLOT_LABELS) {
    const raw = slots[key]
    const value = Array.isArray(raw) ? raw.join('、') : raw
    if (value) rows.push([label, value])
  }
  return rows
}

function intentLabel(intent: string): string {
  return INTENT_LABELS[intent] ?? intent
}

export default function IntentPanel({ intent }: { intent: IntentDecision | null }) {
  if (!intent) return null

  const tier = TIER_META[intent.tier] ?? TIER_META.fallback
  const blocked = BLOCKING_RISKS.has(intent.risk)
  // 有风险信号但未达拦截门槛：仍要显眼地告知，只是措辞不能说「已拦截」。
  const flagged = intent.risk !== 'none' && !blocked
  const ranked = Object.entries(intent.scores)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4)
  const slotEntries = slotRows(intent.slots)

  return (
    <section className={`panel intent-panel${blocked ? ' is-blocked' : ''}`} aria-label="意图识别">
      <div className="row between panel-head">
        <h3 className="panel-title">
          <AppIcon name={blocked ? 'shield-alert' : 'target'} size={16} aria-hidden="true" />
          意图识别
        </h3>
        <span className={`intent-tier-chip tier-${intent.tier}`} title={tier.note}>
          {tier.label}
        </span>
      </div>

      <div className="intent-verdicts">
        <div className="intent-verdict">
          <span className="intent-verdict-label">任务意图</span>
          <strong className="intent-verdict-value">{intentLabel(intent.intent)}</strong>
          <span className="muted small">置信度 {(intent.confidence * 100).toFixed(0)}%</span>
        </div>
        <div className={`intent-verdict${blocked || flagged ? ' is-risk' : ''}`}>
          <span className="intent-verdict-label">风险意图</span>
          <strong className="intent-verdict-value">
            {RISK_LABELS[intent.risk] ?? intent.risk}
          </strong>
          <span className="muted small">
            {blocked
              ? '请求已在研究开始前拦截'
              : flagged
                ? '已记录风险信号，未达拦截门槛，研究照常执行'
                : '未检出风险信号'}
          </span>
        </div>
      </div>

      <p className="muted small intent-cost-note">
        {tier.note}
        {intent.escalated ? ' · 本次判定调用了大模型' : ' · 本次判定未调用大模型'}
      </p>

      {intent.clarification && (
        <div className="intent-clarify">
          <span className="intent-section-label">需要澄清</span>
          <p className="intent-clarify-question">{intent.clarification.question}</p>
          {intent.clarification.options.length > 0 && (
            <ol className="intent-clarify-options">
              {intent.clarification.options.map((option) => (
                <li key={option}>{option}</li>
              ))}
            </ol>
          )}
        </div>
      )}

      {intent.context_resolved && intent.resolved_query && (
        <div className="intent-resolved">
          <span className="intent-section-label">多轮消解</span>
          {/* 展示消解结果是可解释性的一部分：用户要能看到系统「以为」他在问什么，
              否则一次错误的指代消解会表现为「答非所问」且无从追溯。 */}
          <p className="intent-resolved-query">{intent.resolved_query}</p>
        </div>
      )}

      {slotEntries.length > 0 && (
        <div className="intent-slots">
          <span className="intent-section-label">识别到的约束</span>
          <ul>
            {slotEntries.map(([label, value]) => (
              <li key={label}>
                <span className="intent-slot-name">{label}</span>
                <span className="intent-slot-value">{value}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {ranked.length > 0 && (
        <div className="intent-scores">
          <span className="intent-section-label">类别概率分布</span>
          {ranked.map(([label, score]) => (
            <div className="intent-score-row" key={label}>
              <span className="intent-score-name">{intentLabel(label)}</span>
              <span className="intent-score-track" aria-hidden>
                <i style={{ transform: `scaleX(${Math.max(0, Math.min(1, score))})` }} />
              </span>
              <span className="intent-score-value">{(score * 100).toFixed(0)}%</span>
            </div>
          ))}
        </div>
      )}

      {intent.signals.length > 0 && (
        <div className="intent-signals">
          <span className="intent-section-label">判定依据</span>
          <ul>
            {intent.signals.map((signal, index) => (
              <li key={`${signal.code}-${index}`}>
                <code>{signal.code}</code>
                <span className={`intent-signal-tier tier-${signal.tier}`}>{signal.tier}</span>
                {signal.detail && <span className="muted small">{signal.detail}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {intent.reason && <p className="muted small intent-reason">{intent.reason}</p>}
    </section>
  )
}
