import { memo, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { ResearchEvent } from '../types'
import { AppIcon } from './AppIcon'
import ModelCallSummary from './ModelCallSummary'
import { modelCalls } from '../lib/modelCalls'
import { useReasoningFrames } from '../hooks/useReasoningFrames'

const RECENT_CHARS = 12_000
const CALLS_PER_PAGE = 20

const ReasoningCall = memo(function ReasoningCall({
  model, text, index, active, completed,
}: {
  model: string
  text: string
  index: number
  active: boolean
  completed: boolean
}) {
  const scroller = useRef<HTMLDivElement>(null)
  const following = useRef(true)
  // Undefined follows lifecycle defaults; explicit user choice always wins.
  const [expanded, setExpanded] = useState<boolean>()
  const [fullText, setFullText] = useState(false)
  const [paused, setPaused] = useState<string | null>(null)
  const open = expanded ?? active
  const displayText = paused ?? text
  let start = fullText ? 0 : Math.max(0, displayText.length - RECENT_CHARS)
  if (start > 0 && /[\uDC00-\uDFFF]/.test(displayText[start])) start -= 1
  const visibleText = displayText.slice(start)

  useLayoutEffect(() => {
    const element = scroller.current
    if (open && following.current && element && element.clientHeight > 0) {
      element.scrollTop = Math.max(0, element.scrollHeight - element.clientHeight)
    }
  }, [visibleText, open])

  const resume = () => {
    following.current = true
    setPaused(null)
    const element = scroller.current
    if (element) element.scrollTop = element.scrollHeight
  }

  return (
    <details className="model-reasoning-call" open={open}>
      <summary onClick={(event) => { event.preventDefault(); setExpanded(!open) }}>
        <AppIcon name="chevron-right" size={13} className="reasoning-chevron" aria-hidden="true" />
        <span>模型调用 {index + 1}</span>
        <span className="model-reasoning-model">{model}</span>
        {active && <span className="model-reasoning-state is-active">思考中</span>}
        {completed && <span className="model-reasoning-state">已结束</span>}
      </summary>
      {open && <>
        <div className="model-reasoning-toolbar">
          {start > 0 && <span>显示最近片段，前面 {start.toLocaleString()} 个字符已折叠</span>}
          {(text.length > RECENT_CHARS || fullText) && (
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => {
              following.current = false
              setPaused(text)
              setFullText(value => !value)
              if (scroller.current) scroller.current.scrollTop = 0
            }}>{fullText ? '仅查看最近片段' : '查看全部内容'}</button>
          )}
          {paused !== null && <button type="button" className="btn btn-ghost btn-sm" onClick={resume}>
            {active ? '跟随最新内容' : '回到最新内容'}
          </button>}
          {paused !== null && paused !== text && <span>有新内容</span>}
        </div>
        <div ref={scroller} className="model-reasoning-text" tabIndex={0}
          aria-label={`模型调用 ${index + 1} 的思考内容`}
          onScroll={(event) => {
            const element = event.currentTarget
            const atBottom = element.scrollHeight - element.clientHeight - element.scrollTop <= 24
            following.current = atBottom
            // Freeze the view while reading earlier text. A sliding tail must
            // not remove the very sentence the user is reading.
            if (!atBottom) setPaused(current => current ?? text)
            else setPaused(null)
          }}>
          {visibleText}
          {active && paused === null && <span className="reasoning-stream-caret" aria-hidden="true" />}
        </div>
      </>}
    </details>
  )
})

/** Only display reasoning supplied by the provider; never synthesize a trace. */
function ModelReasoningPanel({ events, live = false }: { events: ResearchEvent[]; live?: boolean }) {
  const frames = useReasoningFrames(events, live)
  const [expanded, setExpanded] = useState<boolean>()
  const [visibleCount, setVisibleCount] = useState(CALLS_PER_PAGE)
  const open = expanded ?? live
  const { calls, completed, summaryEvents, lifecycle } = useMemo(() => {
    const calls = new Map<string, { model: string; parts: string[] }>()
    const completed = new Set<string>()
    const summaryEvents = frames.filter(event =>
      event.data?.model_call || event.data?.model_queue || event.data?.llm_usage,
    )
    const lifecycle = modelCalls(summaryEvents)
    for (const call of lifecycle) {
      if (call.status !== 'started') completed.add(call.call_id)
    }
    for (const event of frames) {
      const data = event.data
      const usage = data?.llm_usage
      if (usage && typeof usage === 'object' && 'call_id' in usage && typeof usage.call_id === 'string') {
        completed.add(usage.call_id)
      }
      if (typeof data?.reasoning_delta !== 'string' || typeof data.call_id !== 'string') continue
      const call = calls.get(data.call_id) ?? { model: String(data.model ?? ''), parts: [] }
      call.parts.push(data.reasoning_delta)
      calls.set(data.call_id, call)
    }
    return {
      calls: [...calls].map(([id, call], index) => ({ id, model: call.model, text: call.parts.join(''), index })),
      completed, summaryEvents, lifecycle,
    }
  }, [frames])
  const known = new Map(lifecycle.map(call => [call.call_id, call.status]))
  const isActive = (id: string, index: number) => live && !completed.has(id) &&
    (known.get(id) === 'started' || (!known.has(id) && index === calls.length - 1))
  const visible = calls.filter(call => call.index >= calls.length - visibleCount || isActive(call.id, call.index))
  const hiddenCount = calls.length - visible.length

  return (
    <>
      <ModelCallSummary events={summaryEvents} live={live} />
      {calls.length > 0 && <details className="model-reasoning-panel reasoning-surface" open={open}
        data-live={live}>
        <summary className="model-reasoning-heading"
          onClick={(event) => { event.preventDefault(); setExpanded(!open) }}>
          <AppIcon name="chevron-right" size={14} className="reasoning-chevron" aria-hidden="true" />
          <span>思考过程</span>
          <span className="reasoning-kind">过程记录</span>
          <span className="model-reasoning-count"
            title="仅统计已收到思考内容的模型调用，不代表搜索工具调用次数。">
            {calls.length} 次模型调用
          </span>
        </summary>
        {open && <div className="model-reasoning-calls">
          {hiddenCount > 0 && <button type="button" className="btn btn-ghost btn-sm"
            onClick={() => setVisibleCount(count => count + CALLS_PER_PAGE)}>
            查看更早调用（还有 {hiddenCount} 次）
          </button>}
          {visible.map(call => <ReasoningCall key={call.id} index={call.index} model={call.model}
            text={call.text} active={isActive(call.id, call.index)}
            completed={!live || completed.has(call.id)} />)}
        </div>}
      </details>}
    </>
  )
}

export default memo(ModelReasoningPanel)
