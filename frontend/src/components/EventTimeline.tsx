import { useEffect, useRef } from 'react'
import { AppIcon } from './AppIcon'
import { getStageMeta } from '../lib/stageMeta'
import type { ResearchEvent } from '../types'

// Keep the live event feed pinned to the newest entry as events arrive.
export default function EventTimeline({
  events,
  streaming = false,
}: {
  events: ResearchEvent[]
  streaming?: boolean
}) {
  const timelineRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const node = timelineRef.current
    if (!node) return
    const reduceMotion =
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    node.scrollTo({ top: node.scrollHeight, behavior: reduceMotion ? 'auto' : 'smooth' })
  }, [events.length])

  if (events.length === 0) {
    return <p className="muted small">等待事件…开始研究后这里会实时显示各 Agent 的动作。</p>
  }

  return (
    <div className="timeline" ref={timelineRef}>
      {events.map((ev, i) => {
        const meta = getStageMeta(ev.stage)
        const live = streaming && i === events.length - 1
        return (
          <div
            className={`event-row${live ? ' live' : ''}`}
            key={`${ev.elapsed}-${ev.stage}-${ev.type}-${i}`}
          >
            <span className="event-dot" style={{ background: meta.color }} />
            <span className="event-time">{ev.elapsed.toFixed(1)}s</span>
            <div className="event-main">
              <span className="event-stage" style={{ color: meta.color }}>
                <AppIcon name={meta.icon} size={14} aria-hidden="true" />
                {meta.label} · {ev.stage}
              </span>
              <div className="event-msg">{ev.message}</div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
