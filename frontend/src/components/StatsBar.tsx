import { useEffect, useRef, useState } from 'react'
import type { RunDetail, RunStats } from '../types'

interface LiveStats {
  elapsed: number
  tokens: number
  findings: number
}

// 统计条：流式中实时显示（耗时秒级跳动 + token / 发现数随阶段累加）；
// 结束用 done 事件的精确统计；回放回退到已落库 detail。
export default function StatsBar({
  stats,
  detail,
  live = null,
  streaming = false,
}: {
  stats: RunStats | null
  detail: RunDetail | null
  live?: LiveStats | null
  streaming?: boolean
}) {
  // 耗时秒级跳动：锚定在最新事件的 elapsed 上，两次事件之间用墙钟补齐。
  const liveElapsed = live?.elapsed ?? 0
  const anchor = useRef({ base: liveElapsed, at: Date.now() })
  const [, setTick] = useState(0)

  // 追踪数字变化，触发动画
  const prevTokens = useRef(live?.tokens ?? 0)
  const prevFindings = useRef(live?.findings ?? 0)
  const [tokensUpdating, setTokensUpdating] = useState(false)
  const [findingsUpdating, setFindingsUpdating] = useState(false)

  useEffect(() => {
    anchor.current = { base: liveElapsed, at: Date.now() }
  }, [liveElapsed])

  useEffect(() => {
    if (!streaming) return
    const id = setInterval(() => setTick((t) => t + 1), 500)
    return () => clearInterval(id)
  }, [streaming])

  // Token 更新动画
  useEffect(() => {
    const currentTokens = live?.tokens ?? 0
    if (streaming && currentTokens !== prevTokens.current && currentTokens > 0) {
      setTokensUpdating(true)
      prevTokens.current = currentTokens
      const timer = setTimeout(() => setTokensUpdating(false), 300)
      return () => clearTimeout(timer)
    }
  }, [live?.tokens, streaming])

  // Findings 更新动画
  useEffect(() => {
    const currentFindings = live?.findings ?? 0
    if (streaming && currentFindings !== prevFindings.current && currentFindings > 0) {
      setFindingsUpdating(true)
      prevFindings.current = currentFindings
      const timer = setTimeout(() => setFindingsUpdating(false), 300)
      return () => clearTimeout(timer)
    }
  }, [live?.findings, streaming])

  const finalElapsed = stats?.elapsed ?? detail?.elapsed
  const elapsed = streaming
    ? anchor.current.base + (Date.now() - anchor.current.at) / 1000
    : finalElapsed
  const tokens = stats?.total_tokens ?? (streaming ? live?.tokens : detail?.total_tokens)
  const sources = stats?.sources ?? detail?.report?.citations.length
  const findings = streaming ? live?.findings : undefined

  const cards: { ico: React.ReactNode; num: string; label: string; updating?: boolean }[] = []

  if (elapsed != null) cards.push({
    ico: (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="1.5" fill="none"/>
        <path d="M12 6V12L16 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
      </svg>
    ),
    num: `${Number(elapsed).toFixed(1)}s`,
    label: '耗时'
  })

  if (tokens != null) cards.push({
    ico: (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M4 8H20M4 16H20M8 4V20M16 4V20" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
      </svg>
    ),
    num: `${tokens}`,
    label: 'Tokens',
    updating: tokensUpdating
  })

  if (sources != null) cards.push({
    ico: (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M12 2L15 8L22 9L17 14L18 21L12 18L6 21L7 14L2 9L9 8L12 2Z" stroke="currentColor" strokeWidth="1.5" fill="none"/>
      </svg>
    ),
    num: `${sources}`,
    label: '引用来源'
  })
  else if (findings != null && findings > 0)
    cards.push({
      ico: (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <circle cx="11" cy="11" r="8" stroke="currentColor" strokeWidth="1.5" fill="none"/>
          <path d="M21 21L16.5 16.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          <circle cx="11" cy="11" r="3" fill="currentColor"/>
        </svg>
      ),
      num: `${findings}`,
      label: '发现',
      updating: findingsUpdating
    })

  if (cards.length === 0) return null

  return (
    <div className="statsbar">
      {cards.map((c) => (
        <div className="stat-card" key={c.label}>
          <span className="stat-ico" aria-hidden>
            {c.ico}
          </span>
          <span className="stat-body">
            <span className={`stat-num${c.updating ? ' updating' : ''}`}>{c.num}</span>
            <span className="stat-label">{c.label}</span>
          </span>
        </div>
      ))}
    </div>
  )
}
