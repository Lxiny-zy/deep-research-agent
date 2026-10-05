import { useLayoutEffect, useRef } from 'react'
import type { ResearchEvent } from '../types'
import { AppIcon } from './AppIcon'

function ReasoningCall({
  model,
  text,
  index,
  active,
  completed,
}: {
  model: string
  text: string
  index: number
  active: boolean
  completed: boolean
}) {
  const scroller = useRef<HTMLDivElement>(null)
  const following = useRef(true)

  const followLatest = () => {
    const element = scroller.current
    if (active && following.current && element && element.clientHeight > 0) {
      element.scrollTop = Math.max(0, element.scrollHeight - element.clientHeight)
    }
  }

  useLayoutEffect(followLatest, [text, active])

  return (
    <details className="model-reasoning-call" open={active} onToggle={followLatest}>
      <summary>
        <AppIcon name="chevron-right" size={13} className="reasoning-chevron" aria-hidden="true" />
        <span>模型调用 {index + 1}</span>
        <span className="model-reasoning-model">{model}</span>
        {active && <span className="model-reasoning-state is-active">思考中</span>}
        {completed && <span className="model-reasoning-state">已结束</span>}
      </summary>
      <div
        ref={scroller}
        className="model-reasoning-text"
        onScroll={(event) => {
          const element = event.currentTarget
          following.current = element.scrollHeight - element.clientHeight - element.scrollTop <= 24
        }}
      >
        {text}
      </div>
    </details>
  )
}

/** Display only reasoning explicitly supplied by the model API. */
export default function ModelReasoningPanel({
  events,
  live = false,
}: {
  events: ResearchEvent[]
  live?: boolean
}) {
  const calls = new Map<string, { model: string; text: string }>()
  const completed = new Set<string>()
  for (const event of events) {
    const data = event.data
    const usage = data?.llm_usage
    if (
      usage &&
      typeof usage === 'object' &&
      'call_id' in usage &&
      typeof usage.call_id === 'string'
    )
      completed.add(usage.call_id)
    if (typeof data?.reasoning_delta !== 'string' || typeof data.call_id !== 'string') continue
    const call = calls.get(data.call_id) ?? {
      model: String(data.model ?? ''),
      text: '',
    }
    call.text += data.reasoning_delta
    calls.set(data.call_id, call)
  }
  if (!calls.size) return null
  return (
    <details className="model-reasoning-panel" open={live}>
      <summary className="model-reasoning-heading">
        <AppIcon name="chevron-right" size={14} className="reasoning-chevron" aria-hidden="true" />
        <span>思考过程</span>
        <span
          className="model-reasoning-count"
          title="仅统计返回思考内容的模型调用，包含规划、写作和核验；不代表搜索工具调用次数。"
        >
          {calls.size} 次模型调用
        </span>
      </summary>
      <div className="model-reasoning-calls">
        {[...calls].map(([id, call], index) => (
          <ReasoningCall
            key={id}
            index={index}
            model={call.model}
            text={call.text}
            active={live && !completed.has(id) && index === calls.size - 1}
            completed={!live || completed.has(id)}
          />
        ))}
      </div>
    </details>
  )
}
