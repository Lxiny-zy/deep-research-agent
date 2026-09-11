import { useCallback, useEffect, useRef, useState } from 'react'
import { AppIcon } from './AppIcon'
import { getStageMeta } from '../lib/stageMeta'
import type { ResearchEvent } from '../types'

// Follow the live event feed by default, while allowing readers to pause and inspect older entries.
export default function EventTimeline({
  events,
  streaming = false,
}: {
  events: ResearchEvent[]
  streaming?: boolean
}) {
  const timelineRef = useRef<HTMLDivElement>(null)
  const previousStreaming = useRef(streaming)
  const [followLatest, setFollowLatest] = useState(true)
  const [page, setPage] = useState<number | null>(null)
  const lastPage = Math.max(0, Math.ceil(events.length / 100) - 1)
  const currentPage = Math.min(page ?? lastPage, lastPage)
  const visibleEvents = events.slice(currentPage * 100, (currentPage + 1) * 100)

  const scrollToLatest = useCallback((behavior: ScrollBehavior = 'smooth') => {
    const node = timelineRef.current
    if (!node) return
    const reduceMotion =
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    node.scrollTo({ top: node.scrollHeight, behavior: reduceMotion ? 'auto' : behavior })
  }, [])

  useEffect(() => {
    if (followLatest) scrollToLatest()
  }, [events.length, followLatest, scrollToLatest])

  useEffect(() => {
    if (events.length === 0) setFollowLatest(true)
  }, [events.length])

  useEffect(() => {
    if (streaming && !previousStreaming.current) setFollowLatest(true)
    previousStreaming.current = streaming
  }, [streaming])

  function handleScroll() {
    const node = timelineRef.current
    if (!node) return
    const distanceFromLatest = node.scrollHeight - node.scrollTop - node.clientHeight
    setFollowLatest(distanceFromLatest < 32)
  }

  if (events.length === 0) {
    return <p className="muted small">等待事件…开始研究后这里会实时显示各 Agent 的动作。</p>
  }

  return (
    <div className="timeline-shell">
      {lastPage > 0 && (
        <nav className="row gap" aria-label="事件分页">
          <button
            type="button"
            disabled={currentPage === 0}
            onClick={() => {
              setPage(currentPage - 1)
              setFollowLatest(false)
            }}
          >
            较早事件
          </button>
          <span>
            第 {currentPage + 1} / {lastPage + 1} 页
            {events.length >= 5000 ? '（最近 5000 条）' : ''}
          </span>
          <button
            type="button"
            disabled={currentPage === lastPage}
            onClick={() => setPage(currentPage + 1)}
          >
            较新事件
          </button>
          <button
            type="button"
            onClick={() => {
              setPage(null)
              setFollowLatest(true)
            }}
          >
            最新事件
          </button>
        </nav>
      )}
      {streaming && events.length > 0 && (
        <div className="timeline-toolbar">
          <span className="timeline-follow-state" aria-live="polite">
            {followLatest ? '正在跟随最新事件' : '已暂停自动跟随'}
          </span>
          <button
            type="button"
            className="timeline-follow-toggle"
            onClick={() => {
              if (followLatest) {
                setFollowLatest(false)
              } else {
                setFollowLatest(true)
                setPage(null)
                scrollToLatest()
              }
            }}
            aria-label={followLatest ? '暂停自动跟随事件' : '回到最新事件并恢复跟随'}
            aria-pressed={followLatest}
            title={followLatest ? '暂停自动跟随事件' : '回到最新事件并恢复跟随'}
          >
            <AppIcon name={followLatest ? 'pause' : 'play'} size={13} aria-hidden="true" />
            {followLatest ? '暂停跟随' : '回到最新'}
          </button>
        </div>
      )}
      <div className="timeline" ref={timelineRef} onScroll={handleScroll}>
        {visibleEvents.map((ev, i) => {
          const meta = getStageMeta(ev.stage)
          const live = streaming && currentPage === lastPage && i === visibleEvents.length - 1
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
    </div>
  )
}
