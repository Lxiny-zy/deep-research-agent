import { useLayoutEffect, useRef } from 'react'
import type { ResearchEvent } from '../types'

function ReasoningCall({
  model,
  text,
  index,
  active,
}: {
  model: string
  text: string
  index: number
  active: boolean
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
    <details open={active} onToggle={followLatest}>
      <summary>
        第 {index + 1} 次调用 · {model}
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
  for (const event of events) {
    const data = event.data
    if (typeof data?.reasoning_delta !== 'string' || typeof data.call_id !== 'string') continue
    const call = calls.get(data.call_id) ?? { model: String(data.model ?? ''), text: '' }
    call.text += data.reasoning_delta
    calls.set(data.call_id, call)
  }
  if (!calls.size) return null
  return (
    <details className="model-reasoning-panel" open={live}>
      <summary>模型返回的思考内容</summary>
      {[...calls].map(([id, call], index) => (
        <ReasoningCall
          key={id}
          index={index}
          model={call.model}
          text={call.text}
          active={live && index === calls.size - 1}
        />
      ))}
    </details>
  )
}
