import { useState } from 'react'
import { Link } from 'react-router-dom'
import Skeleton from '../components/Skeleton'
import StatusBadge from '../components/StatusBadge'
import { useRunsList } from '../hooks/useRuns'

const PAGE = 20

export default function HistoryPage() {
  const [offset, setOffset] = useState(0)
  // 多取一条用于判断是否还有下一页，渲染时只显示前 PAGE 条
  const { data, isLoading, isError, error } = useRunsList({ limit: PAGE + 1, offset })
  const rows = data?.slice(0, PAGE)
  const hasNext = (data?.length ?? 0) > PAGE

  return (
    <div className="stack">
      <div className="panel">
        <h3 className="panel-title">研究历史</h3>
        {isLoading && <Skeleton rows={5} />}
        {isError && (
          <p className="error-text">✗ {error instanceof Error ? error.message : '加载失败'}</p>
        )}
        {rows && rows.length === 0 && (
          <div className="empty">
            <div className="ico">✦</div>
            还没有研究记录，去「新建研究」开始第一条。
          </div>
        )}
        {rows && rows.length > 0 && (
          <div className="run-list">
            {rows.map((r) => (
              <Link to={`/runs/${r.id}`} className="run-item" key={r.id}>
                <span className="run-item-q">{r.query}</span>
                <span className="run-item-meta">
                  <span className="muted small mono">
                    {r.total_tokens} tokens · {r.elapsed.toFixed(1)}s
                  </span>
                  <StatusBadge status={r.status} />
                </span>
              </Link>
            ))}
          </div>
        )}
      </div>
      <div className="row between">
        <button
          className="btn ghost"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - PAGE))}
        >
          ← 上一页
        </button>
        <span className="muted small">第 {Math.floor(offset / PAGE) + 1} 页</span>
        <button
          className="btn ghost"
          disabled={!hasNext}
          onClick={() => setOffset(offset + PAGE)}
        >
          下一页 →
        </button>
      </div>
    </div>
  )
}
