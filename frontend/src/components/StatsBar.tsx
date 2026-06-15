import type { RunDetail, RunStats } from '../types'

// 优先用流式 done 事件的统计；进行中或回放时回退到已落库的 detail。卡片化展示。
export default function StatsBar({
  stats,
  detail,
}: {
  stats: RunStats | null
  detail: RunDetail | null
}) {
  const elapsed = stats?.elapsed ?? detail?.elapsed
  const tokens = stats?.total_tokens ?? detail?.total_tokens
  const sources = stats?.sources ?? detail?.report?.citations.length

  if (elapsed == null && tokens == null && sources == null) return null

  const cards: { ico: string; num: string; label: string }[] = []
  if (elapsed != null) cards.push({ ico: '◷', num: `${Number(elapsed).toFixed(1)}s`, label: '耗时' })
  if (tokens != null) cards.push({ ico: '∑', num: `${tokens}`, label: 'Tokens' })
  if (sources != null) cards.push({ ico: '❖', num: `${sources}`, label: '引用来源' })

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
