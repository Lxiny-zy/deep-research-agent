import type { ResearchEvent } from '../types'

export interface ModelCallRecord {
  call_id: string
  operation_id?: string
  attempt?: number
  operation?: string
  role?: string | null
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
  for (const key of ['operation_id', 'operation', 'role', 'model', 'retry_reason', 'error_type', 'usage_state']) {
    if (typeof record[key] === 'string') Object.assign(clean, { [key]: record[key] })
  }
  for (const key of ['attempt', 'duration_ms']) {
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
  const roles: Record<string, string> = {
    planner: '规划', researcher: '资料研究', synthesizer: '生成回答',
    evidence_verifier: '证据核验', reflector: '检查研究缺口',
  }
  if (call.operation === 'search') return '模型检索'
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
