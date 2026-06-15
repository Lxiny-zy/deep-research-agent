import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import Skeleton from '../components/Skeleton'
import StatusBadge from '../components/StatusBadge'
import {
  useBatchDeleteRuns,
  useDeleteRun,
  useRunsList,
  useTags,
} from '../hooks/useRuns'
import type { RunStatus } from '../types'

const PAGE = 20

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: '全部状态' },
  { value: 'done', label: '已完成' },
  { value: 'running', label: '进行中' },
  { value: 'pending', label: '排队中' },
  { value: 'error', label: '出错' },
]

export default function HistoryPage() {
  const [offset, setOffset] = useState(0)
  const [status, setStatus] = useState('')
  const [qInput, setQInput] = useState('')
  const [q, setQ] = useState('')
  const [tag, setTag] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())

  // 搜索防抖：输入停顿 350ms 后才打到后端
  useEffect(() => {
    const id = setTimeout(() => setQ(qInput.trim()), 350)
    return () => clearTimeout(id)
  }, [qInput])

  // 任一筛选变化：回到第一页并清空选择
  useEffect(() => {
    setOffset(0)
    setSelected(new Set())
  }, [status, q, tag])

  // 多取一条用于判断是否还有下一页，渲染时只显示前 PAGE 条
  const { data, isLoading, isError, error } = useRunsList({
    limit: PAGE + 1,
    offset,
    status: status || undefined,
    q: q || undefined,
    tag: tag || undefined,
  })
  const tags = useTags()
  const del = useDeleteRun()
  const batchDel = useBatchDeleteRuns()

  const rows = useMemo(() => data?.slice(0, PAGE) ?? [], [data])
  const hasNext = (data?.length ?? 0) > PAGE
  const allSelected = rows.length > 0 && rows.every((r) => selected.has(r.id))
  const filtersActive = Boolean(status || q || tag)

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(rows.map((r) => r.id)))
  }

  async function removeOne(id: string, query: string) {
    if (!window.confirm(`删除这条研究记录？\n\n「${query}」\n\n此操作不可撤销。`)) return
    await del.mutateAsync(id).catch(() => {})
    setSelected((prev) => {
      const next = new Set(prev)
      next.delete(id)
      return next
    })
  }

  async function removeSelected() {
    const ids = [...selected]
    if (ids.length === 0) return
    if (!window.confirm(`删除选中的 ${ids.length} 条研究记录？此操作不可撤销。`)) return
    const res = await batchDel.mutateAsync(ids).catch(() => null)
    setSelected(new Set())
    if (res && res.skipped > 0) {
      window.alert(`已删除 ${res.deleted} 条；${res.skipped} 条进行中已跳过。`)
    }
  }

  return (
    <div className="stack">
      <div className="panel">
        <h3 className="panel-title">研究历史</h3>

        <div className="filter-bar">
          <input
            className="input"
            placeholder="搜索问题关键词…"
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
          />
          <select className="input select" value={status} onChange={(e) => setStatus(e.target.value)}>
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>

        {tags.data && tags.data.length > 0 && (
          <div className="chips" style={{ marginTop: 10 }}>
            {tag && (
              <button type="button" className="chip active" onClick={() => setTag('')}>
                ✕ 清除标签
              </button>
            )}
            {tags.data.map((t) => (
              <button
                type="button"
                key={t.tag}
                className={`chip${tag === t.tag ? ' active' : ''}`}
                onClick={() => setTag(tag === t.tag ? '' : t.tag)}
              >
                {t.tag} · {t.count}
              </button>
            ))}
          </div>
        )}

        <div className="run-toolbar">
          <label className="check-inline">
            <input type="checkbox" checked={allSelected} onChange={toggleAll} disabled={rows.length === 0} />
            <span className="muted small">全选本页</span>
          </label>
          <button
            className="btn ghost sm"
            disabled={selected.size === 0 || batchDel.isPending}
            onClick={removeSelected}
          >
            {batchDel.isPending ? '删除中…' : `删除所选（${selected.size}）`}
          </button>
        </div>

        {isLoading && <Skeleton rows={5} />}
        {isError && (
          <p className="error-text">✗ {error instanceof Error ? error.message : '加载失败'}</p>
        )}
        {!isLoading && rows.length === 0 && (
          <div className="empty">
            <div className="ico">✦</div>
            {filtersActive ? '没有符合条件的记录，换个筛选试试。' : '还没有研究记录，去「新建研究」开始第一条。'}
          </div>
        )}
        {rows.length > 0 && (
          <div className="run-list">
            {rows.map((r) => (
              <div className="run-item" key={r.id}>
                <input
                  type="checkbox"
                  className="run-check"
                  checked={selected.has(r.id)}
                  onChange={() => toggle(r.id)}
                  aria-label="选择"
                />
                <Link to={`/runs/${r.id}`} className="run-item-main">
                  <span className="run-item-q">{r.query}</span>
                  {r.tags.length > 0 && (
                    <span className="run-tags">
                      {r.tags.map((t) => (
                        <span className="tag-chip" key={t}>
                          {t}
                        </span>
                      ))}
                    </span>
                  )}
                </Link>
                <span className="run-item-meta">
                  <span className="muted small mono">
                    {r.total_tokens} tokens · {r.elapsed.toFixed(1)}s
                  </span>
                  <StatusBadge status={r.status as RunStatus} />
                  <button
                    type="button"
                    className="icon-btn"
                    title="删除"
                    onClick={() => removeOne(r.id, r.query)}
                  >
                    ✕
                  </button>
                </span>
              </div>
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
        <button className="btn ghost" disabled={!hasNext} onClick={() => setOffset(offset + PAGE)}>
          下一页 →
        </button>
      </div>
    </div>
  )
}
