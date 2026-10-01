import type { ResearchEvent } from '../types'

/** Display only reasoning explicitly supplied by the model API. */
export default function ModelReasoningPanel({ events }: { events: ResearchEvent[] }) {
  const calls = new Map<string, { model: string; text: string }>()
  for (const event of events) {
    const data = event.data
    if (typeof data?.reasoning_delta !== 'string' || typeof data.call_id !== 'string') continue
    const call = calls.get(data.call_id) ?? { model: String(data.model ?? ''), text: '' }
    call.text += data.reasoning_delta
    calls.set(data.call_id, call)
  }
  if (!calls.size) return null
  return (
    <details className="model-usage-panel">
      <summary>模型返回的思考内容</summary>
      {[...calls].map(([id, call], index) => (
        <details key={id}>
          <summary>
            第 {index + 1} 次调用 · {call.model}
          </summary>
          <div className="model-reasoning-text">{call.text}</div>
        </details>
      ))}
    </details>
  )
}
