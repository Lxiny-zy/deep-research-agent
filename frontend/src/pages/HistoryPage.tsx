import ResearchMotif from '../components/ResearchMotif'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useOutletContext } from 'react-router-dom'
import { AppIcon } from '../components/AppIcon'
import StatusBadge from '../components/StatusBadge'
import EmptyState from '../components/EmptyState'
import { useRevealOnScroll } from '../hooks/useRevealOnScroll'
import { useBatchDeleteRuns, useDeleteRun, useRunsList, useTags } from '../hooks/useRuns'
import type { RunStatus } from '../types'

const PAGE = 20

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: '全部状态' },
  { value: 'done', label: '已完成' },
  { value: 'running', label: '进行中' },
  { value: 'cancelling', label: '取消中' },
  { value: 'cancelled', label: '已取消' },
  { value: 'pending', label: '排队中' },
  { value: 'error', label: '出错' },
]

function formatCreatedAt(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

export default function HistoryPage() {
  const access = useOutletContext<{ role: string } | undefined>()
  const pageRef = useRef<HTMLDivElement>(null)
  useRevealOnScroll(pageRef)
  const [offset, setOffset] = useState(0)
  const [status, setStatus] = useState('')
  const [qInput, setQInput] = useState('')
  const [q, setQ] = useState('')
  const [tag, setTag] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [deleteError, setDeleteError] = useState('')

  useEffect(() => {
    const id = setTimeout(() => setQ(qInput.trim()), 350)
    return () => clearTimeout(id)
  }, [qInput])

  useEffect(() => {
    setOffset(0)
    setSelected(new Set())
  }, [status, q, tag])

  const { data, isLoading, isError, error, refetch } = useRunsList({
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
  const allSelected = rows.length > 0 && rows.every((row) => selected.has(row.id))
  const filtersActive = Boolean(status || q || tag)

  useEffect(() => {
    if (data?.length === 0 && offset > 0) setOffset((value) => Math.max(0, value - PAGE))
  }, [data, offset])

  function toggle(id: string) {
    setSelected((previous) => {
      const next = new Set(previous)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(rows.map((row) => row.id)))
  }

  async function removeOne(id: string, query: string) {
    if (!window.confirm(`删除这条研究记录？\n\n「${query}」\n\n此操作不可撤销。`)) return
    setDeleteError('')
    try {
      await del.mutateAsync(id)
    } catch (cause) {
      setDeleteError(cause instanceof Error ? cause.message : '删除失败，请重试。')
      return
    }
    setSelected((previous) => {
      const next = new Set(previous)
      next.delete(id)
      return next
    })
  }

  async function removeSelected() {
    const ids = [...selected]
    if (!ids.length) return
    if (!window.confirm(`删除选中的 ${ids.length} 条研究记录？此操作不可撤销。`)) return
    setDeleteError('')
    try {
      const result = await batchDel.mutateAsync(ids)
      const removed = new Set(result.deleted_ids ?? (result.skipped === 0 ? ids : []))
      setSelected((previous) => new Set([...previous].filter((id) => !removed.has(id))))
      if (result.skipped > 0)
        setDeleteError(
          `已删除 ${result.deleted} 条；${result.skipped} 条未删除，已保留选择。进行中的研究需先取消。`,
        )
    } catch (cause) {
      setDeleteError(cause instanceof Error ? cause.message : '删除失败，请重试。')
    }
  }

  return (
    <div className="stack page-stack" ref={pageRef}>
      <header className="page-intro history-intro page-intro-compact intro-unveil">
        <div>
          <span className="eyebrow">
            <AppIcon name="history" size={14} aria-hidden="true" /> ARCHIVE / RUN LOG
          </span>
          <h1>
            研究记录，<em>保持可回放。</em>
          </h1>
          <p>按问题、状态或标签检索每一次运行。完整链路、引用与产出都在这里留下痕迹。</p>
        </div>
        <ResearchMotif kind="archive" className="page-motif" />
      </header>

      <div className="history-workbench">
        <section className="panel filter-panel history-filter-module" data-reveal="1">
          <div className="panel-header">
            <div>
              <span className="panel-kicker">FILTER / 01</span>
              <h2 className="panel-title">筛选条件</h2>
            </div>
            {filtersActive && (
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => {
                  setStatus('')
                  setQInput('')
                  setTag('')
                }}
                type="button"
              >
                <AppIcon name="x" size={14} aria-hidden="true" /> 清除筛选
              </button>
            )}
          </div>
          <div className="panel-body history-filter-body">
            <div className="history-filters-grid">
              <label className="field-label" htmlFor="search-input">
                搜索关键词
                <span className="input-with-icon">
                  <AppIcon name="search" size={16} aria-hidden="true" />
                  <input
                    id="search-input"
                    className="input"
                    placeholder="搜索问题关键词…"
                    value={qInput}
                    onChange={(event) => setQInput(event.target.value)}
                  />
                </span>
              </label>
              <label className="field-label" htmlFor="status-select">
                状态筛选
                <span className="select-with-icon">
                  <AppIcon name="sliders" size={15} aria-hidden="true" />
                  <select
                    id="status-select"
                    className="input"
                    value={status}
                    onChange={(event) => setStatus(event.target.value)}
                  >
                    {STATUS_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </span>
              </label>
            </div>

            {tags.data && tags.data.length > 0 && (
              <div className="history-tags-filter">
                <div className="field-label">标签筛选</div>
                <div className="chips">
                  {tags.data.map((item) => (
                    <button
                      type="button"
                      key={item.tag}
                      className={`chip${tag === item.tag ? ' active' : ''}`}
                      onClick={() => setTag(tag === item.tag ? '' : item.tag)}
                    >
                      {item.tag}
                      <span>{item.count}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </section>

        <section className="panel history-list-panel history-results-module" data-reveal="2">
          <div className="panel-header history-list-header">
            <div>
              <span className="panel-kicker">RUNS / {String(rows.length).padStart(2, '0')}</span>
              <h2 className="panel-title">
                研究历史 {!isLoading && rows.length > 0 && <small>({rows.length} 条)</small>}
              </h2>
            </div>
            <div className="history-toolbar-actions">
              <label className="history-select-all">
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={toggleAll}
                  disabled={rows.length === 0}
                />
                <span>全选</span>
              </label>
              <button
                className="btn btn-secondary btn-sm"
                disabled={selected.size === 0 || batchDel.isPending}
                onClick={removeSelected}
                type="button"
              >
                <AppIcon
                  name={batchDel.isPending ? 'loader' : 'trash'}
                  size={14}
                  aria-hidden="true"
                  className={batchDel.isPending ? 'spin' : ''}
                />
                {batchDel.isPending ? '删除中…' : `删除所选 (${selected.size})`}
              </button>
            </div>
          </div>

          <div className="panel-body">
            {deleteError && (
              <p className="error-text" role="alert">
                {deleteError}
              </p>
            )}
            {isLoading && (
              <div className="spinner-container">
                <AppIcon name="loader" size={24} className="spin" aria-label="正在加载" />
              </div>
            )}
            {isError && (
              <div className="workspace-load-error" role="alert">
                <p>{error instanceof Error ? error.message : '加载失败'}</p>
                <button className="btn btn-secondary small" onClick={() => void refetch()}>
                  重新加载记录
                </button>
              </div>
            )}

            {!isLoading && !isError && rows.length === 0 && (
              <EmptyState
                icon={filtersActive ? 'search' : 'history'}
                title={filtersActive ? '没有符合条件的记录' : '还没有研究记录'}
                description={
                  filtersActive
                    ? '换一个关键词，或清除筛选查看全部研究。'
                    : '每一次探索的过程、证据与报告，都会保存在这里。'
                }
              >
                {filtersActive ? (
                  <button
                    className="btn btn-secondary"
                    onClick={() => {
                      setStatus('')
                      setQInput('')
                      setTag('')
                    }}
                  >
                    清除全部筛选
                  </button>
                ) : (
                  access?.role !== 'reader' && (
                    <Link className="btn btn-primary" to="/">
                      <AppIcon name="plus" size={15} aria-hidden="true" /> 开始第一次研究
                    </Link>
                  )
                )}
              </EmptyState>
            )}

            {rows.length > 0 && (
              <div className="history-run-list">
                {rows.map((run, index) => (
                  <article
                    className="history-run-row stagger-item"
                    key={run.id}
                    style={{ '--i': index } as React.CSSProperties}
                  >
                    <input
                      type="checkbox"
                      checked={selected.has(run.id)}
                      onChange={() => toggle(run.id)}
                      aria-label={`选择研究：${run.query}`}
                    />
                    <Link to={`/runs/${run.id}`} className="history-run-link">
                      <div className="history-run-query" title={run.query}>
                        {run.query}
                      </div>
                      <div className="history-run-meta">
                        <StatusBadge status={run.status as RunStatus} />
                        {run.created_at && (
                          <time dateTime={run.created_at}>{formatCreatedAt(run.created_at)}</time>
                        )}
                        <span>{run.total_tokens} tokens</span>
                        <span>{run.elapsed.toFixed(1)}s</span>
                        {run.tags.length > 0 && (
                          <div className="history-run-tags">
                            {run.tags.map((item) => (
                              <span key={item}>{item}</span>
                            ))}
                          </div>
                        )}
                      </div>
                    </Link>
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm icon-button"
                      title={
                        ['pending', 'running', 'cancelling'].includes(run.status)
                          ? '研究进行中，请先取消后再删除'
                          : '删除'
                      }
                      disabled={
                        del.isPending || ['pending', 'running', 'cancelling'].includes(run.status)
                      }
                      aria-label={`删除研究：${run.query}`}
                      onClick={() => void removeOne(run.id, run.query)}
                    >
                      <AppIcon name="trash" size={15} aria-hidden="true" />
                    </button>
                  </article>
                ))}
              </div>
            )}
          </div>
        </section>
      </div>

      {(offset > 0 || hasNext) && (
        <nav className="pagination" aria-label="研究记录分页">
          <button
            className="btn btn-secondary"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE))}
            type="button"
          >
            <AppIcon name="arrow-left" size={15} aria-hidden="true" />
            上一页
          </button>
          <span>第 {Math.floor(offset / PAGE) + 1} 页</span>
          <button
            className="btn btn-secondary"
            disabled={!hasNext}
            onClick={() => setOffset(offset + PAGE)}
            type="button"
          >
            下一页
            <AppIcon name="arrow-right" size={15} aria-hidden="true" />
          </button>
        </nav>
      )}
    </div>
  )
}
