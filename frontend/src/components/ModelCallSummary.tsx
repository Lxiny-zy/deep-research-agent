import type { ResearchEvent } from '../types'
import { modelCallLabel, modelCalls, modelCallUsage } from '../lib/modelCalls'
import { AppIcon } from './AppIcon'

export default function ModelCallSummary({ events, live }: { events: ResearchEvent[]; live: boolean }) {
  const calls = modelCalls(events)
  if (!calls.length) return null
  const retries = calls.filter((call) => call.retry_reason || (call.attempt ?? 1) > 1).length
  const reasons: Record<string, string> = {
    transport_retry: '连接重试', schema_retry: '输出格式修正',
    stream_options_unsupported: '兼容用量参数', message_roles_unsupported: '兼容消息格式',
  }
  const status = { succeeded: '已结束', failed: '请求失败', cancelled: '已停止' }
  return (
    <details className="model-reasoning-panel model-call-summary">
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
              {call.retry_reason && <span>{reasons[call.retry_reason] ?? '请求重试'}</span>}
              {call.error_type === 'ModelOutputTruncated' && <span>输出达到单次上限</span>}
            </div>
          </li>
        ))}
      </ol>
    </details>
  )
}
