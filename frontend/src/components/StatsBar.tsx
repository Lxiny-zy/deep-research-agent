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

  useEffect(() => {
    anchor.current = { base: liveElapsed, at: Date.now() }
  }, [liveElapsed])

  useEffect(() => {
    if (!streaming) return
    const id = setInterval(() => setTick((t) => t + 1), 500)
    return () => clearInterval(id)
  }, [streaming])

  const finalElapsed = stats?.elapsed ?? detail?.elapsed
  const elapsed = streaming
    ? anchor.current.base + (Date.now() - anchor.current.at) / 1000
    : finalElapsed
  const tokens = stats?.total_tokens ?? (streaming ? live?.tokens : detail?.total_tokens)
  const sources = stats?.sources ?? detail?.report?.citations.length
  const findings = streaming ? live?.findings : undefined

  const cards: { ico: string; num: string; label: string }[] = []
  if (elapsed != null) cards.push({ ico: '◷', num: `${Number(elapsed).toFixed(1)}s`, label: '耗时' })
  if (tokens != null) cards.push({ ico: '∑', num: `${tokens}`, label: 'Tokens' })
  if (sources != null) cards.push({ ico: '❖', num: `${sources}`, label: '引用来源' })
  else if (findings != null && findings > 0)
    cards.push({ ico: '❖', num: `${findings}`, label: '发现' })

  if (cards.length === 0) return null

  return (
    <div className="statsbar">
      {cards.map((c) => (
        <div className="stat-card" key={c.label}>
          <span className="stat-ico" aria-hidden>
            {c.ico}
          </span>
          <span className="stat-body">
            <span className="stat-num">{c.num}</span>
            <span className="stat-label">{c.label}</span>
          </span>
        </div>
      ))}
    </div>
  )
}
