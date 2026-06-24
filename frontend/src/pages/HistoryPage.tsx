import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
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
      {/* 筛选面板 */}
      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">筛选条件</div>
          {filtersActive && (
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => {
                setStatus('')
                setQInput('')
                setTag('')
              }}
            >
              清除筛选
            </button>
          )}
        </div>

        <div className="panel-body">
          <div className="history-filters-grid">
            <div>
              <label className="field-label" htmlFor="search-input">
                搜索关键词
              </label>
              <input
                id="search-input"
                className="input"
                placeholder="搜索问题关键词…"
                value={qInput}
                onChange={(e) => setQInput(e.target.value)}
              />
            </div>
            <div>
              <label className="field-label" htmlFor="status-select">
                状态筛选
              </label>
              <select
                id="status-select"
                className="input"
                value={status}
                onChange={(e) => setStatus(e.target.value)}
              >
                {STATUS_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {tags.data && tags.data.length > 0 && (
            <div style={{ marginTop: '20px' }}>
              <div className="field-label" style={{ marginBottom: '12px' }}>标签筛选</div>
              <div className="chips">
                {tags.data.map((t) => (
                  <button
                    type="button"
                    key={t.tag}
                    className={`chip${tag === t.tag ? ' active' : ''}`}
                    onClick={() => setTag(tag === t.tag ? '' : t.tag)}
                    style={{
                      borderColor: tag === t.tag ? 'var(--accent-primary)' : 'var(--border-primary)',
                      background: tag === t.tag ? 'var(--surface-3)' : 'var(--surface-2)',
                    }}
                  >
                    {t.tag} <span style={{ color: 'var(--text-muted)' }}>· {t.count}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 列表面板 */}
      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">
            研究历史 {!isLoading && rows.length > 0 && (
              <span style={{ color: 'var(--text-muted)', fontSize: '0.9rem', fontWeight: 400 }}>
                ({rows.length} 条)
              </span>
            )}
          </div>
          <div className="history-toolbar-actions">
            <label className="history-select-all">
              <input
                type="checkbox"
                checked={allSelected}
                onChange={toggleAll}
                disabled={rows.length === 0}
              />
              <span style={{ color: 'var(--text-secondary)' }}>全选</span>
            </label>
            <button
              className="btn btn-secondary btn-sm"
              disabled={selected.size === 0 || batchDel.isPending}
              onClick={removeSelected}
              style={{ display: 'flex', alignItems: 'center', gap: '6px' }}
            >
              {batchDel.isPending ? (
                <>
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" className="spinner">
                    <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeDasharray="10 30" fill="none"/>
                  </svg>
                  删除中…
                </>
              ) : (
                <>
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <path d="M3 4H13M5 4V3C5 2.44772 5.44772 2 6 2H10C10.5523 2 11 2.44772 11 3V4M6 7V12M10 7V12M4 4L5 13C5 13.5523 5.44772 14 6 14H10C10.5523 14 11 13.5523 11 13L12 4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
                  </svg>
                  删除所选 ({selected.size})
                </>
              )}
            </button>
          </div>
        </div>

        <div className="panel-body">
          {isLoading && (
            <div className="spinner-container">
              <div className="spinner"></div>
            </div>
          )}

          {isError && (
            <div className="badge error" style={{ width: '100%', justifyContent: 'center', padding: '16px' }}>
              ✗ {error instanceof Error ? error.message : '加载失败'}
            </div>
          )}

          {!isLoading && rows.length === 0 && (
            <div className="empty-state">
              <div className="empty-state-icon">⬢</div>
              <div className="empty-state-title">
                {filtersActive ? '没有符合条件的记录' : '还没有研究记录'}
              </div>
              <p style={{ marginTop: '8px' }}>
                {filtersActive ? '尝试调整筛选条件' : '前往「新建研究」开始第一次深度研究'}
              </p>
            </div>
          )}

          {rows.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              {rows.map((r) => (
                <div
                  className="card history-run-card"
                  key={r.id}
                >
                  <input
                    type="checkbox"
                    checked={selected.has(r.id)}
                    onChange={() => toggle(r.id)}
                    style={{ width: '18px', height: '18px', cursor: 'pointer' }}
                  />

                  <Link
                    to={`/runs/${r.id}`}
                    style={{
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '8px',
                      minWidth: 0,
                      textDecoration: 'none',
                    }}
                  >
                    <div style={{
                      fontSize: '1rem',
                      fontWeight: 500,
                      color: 'var(--text-primary)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap'
                    }}>
                      {r.query}
                    </div>
                    <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
                      <StatusBadge status={r.status as RunStatus} />
                      <span style={{
                        fontSize: '0.8rem',
                        color: 'var(--text-tertiary)',
                        fontFamily: 'var(--font-mono)'
                      }}>
                        {r.total_tokens} tokens · {r.elapsed.toFixed(1)}s
                      </span>
                      {r.tags.length > 0 && (
                        <div style={{ display: 'flex', gap: '6px' }}>
                          {r.tags.map((t) => (
                            <span
                              key={t}
                              style={{
                                fontSize: '0.75rem',
                                padding: '2px 8px',
                                background: 'var(--surface-3)',
                                border: '1px solid var(--border-primary)',
                                color: 'var(--text-secondary)',
                              }}
                            >
                              {t}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </Link>

                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    title="删除"
                    onClick={() => removeOne(r.id, r.query)}
                    style={{ minWidth: '40px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                  >
                    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                      <path d="M3 4H13M5 4V3C5 2.44772 5.44772 2 6 2H10C10.5523 2 11 2.44772 11 3V4M6 7V12M10 7V12M4 4L5 13C5 13.5523 5.44772 14 6 14H10C10.5523 14 11 13.5523 11 13L12 4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
                    </svg>
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 分页 */}
      {(offset > 0 || hasNext) && (
        <div style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '16px',
          background: 'var(--surface-1)',
          border: '1px solid var(--border-primary)',
        }}>
          <button
            className="btn btn-secondary"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE))}
          >
            ← 上一页
          </button>
          <span style={{
            color: 'var(--text-secondary)',
            fontSize: '0.9rem',
            fontFamily: 'var(--font-mono)'
          }}>
            第 {Math.floor(offset / PAGE) + 1} 页
          </span>
          <button
            className="btn btn-secondary"
            disabled={!hasNext}
            onClick={() => setOffset(offset + PAGE)}
          >
            下一页 →
          </button>
        </div>
      )}
    </div>
  )
}
