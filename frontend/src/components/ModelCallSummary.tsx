import { memo, useMemo } from 'react'
import type { ResearchEvent } from '../types'
import { modelCallLabel, modelCallRecord, modelCalls, modelCallUsage, type ModelCallRecord } from '../lib/modelCalls'
import { AppIcon } from './AppIcon'

function ModelCallSummary({ events, live }: { events: ResearchEvent[]; live: boolean }) {
  const calls = useMemo(() => modelCalls(events), [events])
  const queues = new Map<string, { call: ModelCallRecord; waiting: boolean }>()
  for (const event of events) {
    const queue = event.data?.model_queue
    if (!queue || typeof queue !== 'object' || !('id' in queue) || typeof queue.id !== 'string'
      || !('status' in queue) || !['waiting', 'acquired', 'failed', 'cancelled'].includes(String(queue.status))) continue
    if (queues.get(queue.id)?.waiting === false && queue.status === 'waiting') continue
    const call = modelCallRecord({ ...queue, call_id: queue.id, status: 'started' })
    if (call) queues.set(queue.id, { call, waiting: queue.status === 'waiting' })
  }
  const waiting = live ? [...queues.values()].filter((entry) => entry.waiting) : []
  if (!calls.length && !waiting.length) return null
  const retries = calls.filter((call) => call.retry_reason || (call.attempt ?? 1) > 1).length
  const active = live ? calls.filter((call) => call.status === 'started') : []
  const reasons: Record<string, string> = {
    transport_retry: '连接重试', schema_retry: '输出格式修正',
    stream_options_unsupported: '兼容用量参数', message_roles_unsupported: '兼容消息格式',
  }
  const status = { succeeded: '已结束', failed: '请求失败', cancelled: '已停止' }
  return (
    <>
    {waiting.length > 0 && <p className="model-call-note" role="status">
      模型排队中：{[...new Set(waiting.map((entry) => modelCallLabel(entry.call)))].join('、')}，取得执行名额后继续。
    </p>}
    {active.length > 0 && (
      <p className="model-call-note" role="status">
        正在{[...new Set(active.map(modelCallLabel))].join('、')}
        {active.length > 1 ? `（${active.length} 个请求）` : ''}，等待模型响应…
      </p>
    )}
    {calls.length > 0 && <details className="model-reasoning-panel model-call-summary">
      <summary className="model-reasoning-heading">
        <AppIcon name="chevron-right" size={14} className="reasoning-chevron" aria-hidden="true" />
        <span>模型请求记录</span>
        <span className="model-reasoning-count">
          {calls.length} 次请求{retries > 0 ? ` · ${retries} 次重试` : ''}
        </span>
      </summary>
      <p className="model-call-note">统计已加载的实际请求记录，包含失败与重试。未返回的用量保持未知。</p>
      <ol className="model-call-list">
        {calls.map((call, index) => (
          <li key={call.call_id}>
            <div className="model-call-row">
              <strong>{index + 1}. {modelCallLabel(call)}</strong>
              <span>{call.status === 'started' ? (live ? '等待响应' : '状态未确认') : status[call.status]}</span>
            </div>
            <div className="model-call-row hint">
              <span>{call.model}</span>
              <span>{modelCallUsage(call)}</span>
              {typeof call.duration_ms === 'number' && <span>{(call.duration_ms / 1000).toFixed(1)} 秒</span>}
              {typeof call.provider_wait_ms === 'number' && call.provider_wait_ms >= 1000 &&
                <span>排队 {(call.provider_wait_ms / 1000).toFixed(1)} 秒</span>}
              {call.usage?.input_tokens != null && <span>输入 {call.usage.input_tokens.toLocaleString()} token</span>}
              {call.usage?.output_tokens != null && <span>输出 {call.usage.output_tokens.toLocaleString()} token</span>}
              {call.usage?.reasoning_tokens != null && <span>思考 {call.usage.reasoning_tokens.toLocaleString()} token</span>}
              {call.reasoning_effort && <span>推理强度：{({ low: '低', medium: '中', high: '高' } as Record<string, string>)[call.reasoning_effort] ?? call.reasoning_effort}</span>}
              {call.reasoning_source === 'operation_policy' && <span>按操作类型选择</span>}
              {call.retry_reason && <span>{reasons[call.retry_reason] ?? '请求重试'}</span>}
              {call.error_type === 'ModelOutputTruncated' && <span>输出达到单次上限</span>}
            </div>
          </li>
        ))}
      </ol>
    </details>}
    </>
  )
}

export default memo(ModelCallSummary)
