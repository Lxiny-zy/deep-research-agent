import { useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useNavigate, useOutletContext } from 'react-router-dom'
import { AppIcon } from '../components/AppIcon'
import StatusBadge from '../components/StatusBadge'
import EmptyState from '../components/EmptyState'
import Skeleton from '../components/Skeleton'
import { useBatchDeleteRuns, useDeleteRun, useRunsList, useTags } from '../hooks/useRuns'
import type { RunStatus } from '../types'

const PAGE = 20

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: '全部状态' },
  { value: 'done', label: '已完成' },
  { value: 'needs_review', label: '待复核' },
  { value: 'running', label: '进行中' },
  { value: 'cancelling', label: '取消中' },
  { value: 'cancelled', label: '已取消' },
  { value: 'pending', label: '排队中' },
  { value: 'error', label: '出错' },
]

const ACTIVE_STATUSES = ['pending', 'running', 'cancelling']

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

function formatElapsed(seconds: number): string {
  if (!Number.isFinite(seconds)) return '—'
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes}m ${Math.round(seconds % 60)}s`
}

export default function HistoryPage() {
  const location = useLocation()
  // 顶栏搜索按钮带着 focusSearch 跳转过来：直接把光标放进搜索框
  const focusSearch = Boolean((location.state as { focusSearch?: boolean } | null)?.focusSearch)
  useEffect(() => {
    if (focusSearch) document.getElementById('search-input')?.focus()
  }, [focusSearch, location.key])
  const access = useOutletContext<{ role: string } | undefined>()
  const navigate = useNavigate()
  const [offset, setOffset] = useState(0)
  const [status, setStatus] = useState('')
  const [qInput, setQInput] = useState('')
  const [q, setQ] = useState('')
  const [tag, setTag] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [deleteError, setDeleteError] = useState('')
  const canCreate = access?.role !== 'reader'

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
  const someSelected = !allSelected && rows.some((row) => selected.has(row.id))
  const filtersActive = Boolean(status || q || tag)

  useEffect(() => {
    if (data?.length === 0 && offset > 0) setOffset((value) => Math.max(0, value - PAGE))
  }, [data, offset])

  function clearFilters() {
    setStatus('')
    setQInput('')
    setTag('')
  }

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
    if (!window.confirm(`删除这条任务记录？\n\n「${query}」\n\n此操作不可撤销。`)) return
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
    if (!window.confirm(`删除选中的 ${ids.length} 条任务记录？此操作不可撤销。`)) return
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
    <div className="stack page-stack history-page">
      <header className="page-header">
        <div>
          <h1>任务记录</h1>
          <p className="page-verse">重翻旧页，或见新意。</p>
          <p>查看任务进度，回看研究报告与引用来源。</p>
        </div>
        {canCreate && (
          <div className="page-header-actions">
            <Link className="btn btn-primary" to="/">
              <AppIcon name="plus" size={15} aria-hidden="true" />
              新建任务
            </Link>
          </div>
        )}
      </header>

      <section className="panel history-panel" aria-label="任务列表">
        <div className="history-toolbar">
          <label className="input-with-icon history-search" htmlFor="search-input">
            <AppIcon name="search" size={15} aria-hidden="true" />
            <span className="visually-hidden">搜索关键词</span>
            <input
              id="search-input"
              className="input"
              type="search"
              placeholder="搜索任务问题…"
              value={qInput}
              onChange={(event) => setQInput(event.target.value)}
            />
          </label>
          <label className="select-with-icon history-status" htmlFor="status-select">
            <AppIcon name="filter" size={15} aria-hidden="true" />
            <span className="visually-hidden">状态筛选</span>
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
          </label>
          {filtersActive && (
            <button className="btn btn-ghost btn-sm" onClick={clearFilters} type="button">
              <AppIcon name="x" size={14} aria-hidden="true" /> 清除筛选
            </button>
          )}
          <div className="history-toolbar-spacer" />
          {selected.size > 0 && <span className="hint">已选 {selected.size} 条</span>}
          <button
            className="btn btn-sm history-bulk-delete"
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

        {tags.data && tags.data.length > 0 && (
          <div className="history-tags" role="group" aria-label="标签筛选">
            <span className="hint">标签</span>
            {tags.data.map((item) => (
              <button
                type="button"
                key={item.tag}
                className={`history-tag${tag === item.tag ? ' is-active' : ''}`}
                aria-pressed={tag === item.tag}
                onClick={() => setTag(tag === item.tag ? '' : item.tag)}
              >
                {item.tag}
                <span className="history-tag-count">{item.count}</span>
              </button>
            ))}
          </div>
        )}

        {deleteError && (
          <p className="alert error history-alert" role="alert">
            <AppIcon name="alert" size={15} aria-hidden="true" />
            {deleteError}
          </p>
        )}

        {isLoading && (
          <div className="history-loading" role="status" aria-label="正在加载">
            <Skeleton rows={6} />
          </div>
        )}

        {isError && (
          <div className="history-error" role="alert">
            <p>{error instanceof Error ? error.message : '加载失败'}</p>
            <button className="btn btn-secondary btn-sm" onClick={() => void refetch()}>
              <AppIcon name="refresh" size={14} aria-hidden="true" />
              重新加载记录
            </button>
          </div>
        )}

        {!isLoading && !isError && rows.length === 0 && (
          <EmptyState
            icon={filtersActive ? 'search' : 'history'}
            title={
              filtersActive
                ? '没有符合条件的记录'
                : canCreate
                  ? '此间尚留白，待你落笔。'
                  : '还没有任务记录'
            }
            description={
              filtersActive
                ? '换一个关键词，或清除筛选查看全部任务。'
                : canCreate
                  ? '创建第一个研究任务，或回到工作台选一个示例问题。'
                  : '当前账号暂无可见任务。'
            }
          >
            {filtersActive ? (
              <button className="btn btn-secondary" onClick={clearFilters}>
                清除全部筛选
              </button>
            ) : (
              canCreate && (
                <Link className="btn btn-primary" to="/">
                  <AppIcon name="plus" size={15} aria-hidden="true" /> 开始第一个任务
                </Link>
              )
            )}
          </EmptyState>
        )}

        {rows.length > 0 && (
          <div className="history-table" role="table" aria-label="任务记录">
            <div className="history-table-head" role="row">
              <span role="columnheader" className="history-col-check">
                <input
                  type="checkbox"
                  checked={allSelected}
                  ref={(element) => {
                    if (element) element.indeterminate = someSelected
                  }}
                  onChange={toggleAll}
                  aria-label="全选"
                />
              </span>
              <span role="columnheader">任务</span>
              <span role="columnheader">状态</span>
              <span role="columnheader" className="history-col-time">
                创建时间
              </span>
              <span role="columnheader" className="history-col-num">
                Token
              </span>
              <span role="columnheader" className="history-col-num">
                耗时
              </span>
              <span role="columnheader" className="history-col-action">
                <span className="visually-hidden">操作</span>
              </span>
            </div>
            <div className="history-run-list" role="rowgroup">
              {rows.map((run) => {
                const active = ACTIVE_STATUSES.includes(run.status)
                const isSelected = selected.has(run.id)
                return (
                  <div
                    className={`history-run-row${isSelected ? ' is-selected' : ''}`}
                    key={run.id}
                    role="row"
                    onClick={(event) => {
                      // 整行可点击进入详情；勾选框、链接与按钮保留各自的行为
                      const target = event.target as HTMLElement
                      if (target.closest('a, button, input, label')) return
                      navigate(`/runs/${run.id}`)
                    }}
                  >
                    <span role="cell" className="history-col-check">
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => toggle(run.id)}
                        aria-label={`选择研究：${run.query}`}
                      />
                    </span>
                    <span role="cell" className="history-col-main">
                      <Link to={`/runs/${run.id}`} className="history-run-query" title={run.query}>
                        {run.query}
                      </Link>
                      {run.tags.length > 0 && (
                        <span className="history-run-tags">
                          {run.tags.map((item) => (
                            <span key={item} className="chip">
                              {item}
                            </span>
                          ))}
                        </span>
                      )}
                    </span>
                    <span role="cell" className="history-col-status">
                      <StatusBadge status={run.status as RunStatus} />
                    </span>
                    <span role="cell" className="history-col-time hint">
                      {run.created_at ? (
                        <time dateTime={run.created_at}>{formatCreatedAt(run.created_at)}</time>
                      ) : (
                        '—'
                      )}
                    </span>
                    <span role="cell" className="history-col-num numeric">
                      {run.total_tokens} tokens
                    </span>
                    <span role="cell" className="history-col-num numeric">
                      {formatElapsed(run.elapsed)}
                    </span>
                    <span role="cell" className="history-col-action">
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm icon-button danger"
                        title={active ? '研究进行中，请先取消后再删除' : '删除'}
                        disabled={del.isPending || active}
                        aria-label={`删除研究：${run.query}`}
                        onClick={() => void removeOne(run.id, run.query)}
                      >
                        <AppIcon name="trash" size={15} aria-hidden="true" />
                      </button>
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {(offset > 0 || hasNext) && (
          <nav className="history-pagination" aria-label="任务记录分页">
            <span className="hint">第 {Math.floor(offset / PAGE) + 1} 页</span>
            <div className="row">
              <button
                className="btn btn-sm"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE))}
                type="button"
              >
                <AppIcon name="arrow-left" size={14} aria-hidden="true" />
                上一页
              </button>
              <button
                className="btn btn-sm"
                disabled={!hasNext}
                onClick={() => setOffset(offset + PAGE)}
                type="button"
              >
                下一页
                <AppIcon name="arrow-right" size={14} aria-hidden="true" />
              </button>
            </div>
          </nav>
        )}
      </section>
    </div>
  )
}
