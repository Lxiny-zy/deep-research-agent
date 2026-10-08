import type { ResearchEvent } from '../types'

export interface ModelCallRecord {
  call_id: string
  operation_id?: string
  attempt?: number
  operation?: string
  role?: string | null
  schema?: string
  generation_task?: string
  reasoning_effort?: string
  reasoning_source?: string
  provider_wait_ms?: number
  model?: string
  status: 'started' | 'succeeded' | 'failed' | 'cancelled'
  retry_reason?: string | null
  error_type?: string | null
  duration_ms?: number
  usage_state?: string
  usage?: Record<string, number | null> | null
}

export function modelCallRecord(value: unknown): ModelCallRecord | null {
  if (!value || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  if (typeof record.call_id !== 'string' || typeof record.status !== 'string' ||
    !['started', 'succeeded', 'failed', 'cancelled'].includes(record.status)) return null
  const clean: ModelCallRecord = {
    call_id: record.call_id, status: record.status as ModelCallRecord['status'],
  }
  for (const key of ['operation_id', 'operation', 'role', 'schema', 'model', 'retry_reason', 'error_type', 'usage_state', 'generation_task', 'reasoning_effort', 'reasoning_source']) {
    if (typeof record[key] === 'string') Object.assign(clean, { [key]: record[key] })
  }
  for (const key of ['attempt', 'duration_ms', 'provider_wait_ms']) {
    const number = record[key]
    if (typeof number === 'number' && Number.isFinite(number) && number >= 0)
      Object.assign(clean, { [key]: number })
  }
  if (record.usage && typeof record.usage === 'object') {
    clean.usage = {}
    for (const key of ['input_tokens', 'output_tokens', 'total_tokens', 'reasoning_tokens']) {
      const number = (record.usage as Record<string, unknown>)[key]
      clean.usage[key] = typeof number === 'number' && Number.isFinite(number) && number >= 0 ? number : null
    }
  }
  return clean
}

export function modelCalls(events: ResearchEvent[]): ModelCallRecord[] {
  const calls = new Map<string, ModelCallRecord>()
  for (const event of events) {
    const next = modelCallRecord(event.data?.model_call)
    if (!next) continue
    const previous = calls.get(next.call_id)
    // A replayed start must never turn a terminal request back into a running one.
    if (previous && previous.status !== 'started' && next.status === 'started') continue
    calls.set(next.call_id, { ...previous, ...next })
  }
  return [...calls.values()]
}

export function modelCallLabel(call: ModelCallRecord): string {
  const checks: Record<string, string> = {
    SemanticEvidenceDecisionList: '核对摘录与结论',
    ClaimConsistencyReport: '检查来源之间的矛盾与印证',
    SupportDecisions: '核对正文引用',
    CoverageDecisions: '检查研究覆盖范围',
    FormulaDecisions: '核对公式',
    FullTextTarget: '定位全文核查内容',
    FullTextChecks: '回查原文',
  }
  const roles: Record<string, string> = {
    planner: '规划', researcher: '资料研究', synthesizer: '生成回答',
    evidence_verifier: '证据核验', reflector: '检查研究缺口',
    survey_writer: '撰写文献综述', paper_reader: '撰写论文分析',
    research_writer: '撰写调研报告', peer_reviewer: '论文审稿',
    slide_writer: '制作汇报幻灯片', mindmap_writer: '整理研究导图',
  }
  if (call.operation === 'search') return '模型检索'
  if (call.role === 'evidence_verifier' && call.schema && checks[call.schema]) return checks[call.schema]
  if (call.role && roles[call.role]) return roles[call.role]
  if (call.role) return call.role
  return call.operation === 'structured' ? '结构化处理' : '内容生成'
}

export function modelCallUsage(call: ModelCallRecord): string {
  const count = (key: string) => {
    const value = call.usage?.[key]
    return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null
  }
  const total = count('total_tokens')
  if (total !== null) return `${total.toLocaleString()} token`
  const input = count('input_tokens')
  const output = count('output_tokens')
  if (input !== null || output !== null)
    return `输入 ${input?.toLocaleString() ?? '未知'} / 输出 ${output?.toLocaleString() ?? '未知'} token`
  return call.status === 'started' ? '等待用量' : '用量未返回'
}
