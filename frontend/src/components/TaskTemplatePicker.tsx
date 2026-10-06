import { useEffect, useId, useRef, useState } from 'react'
import { AppIcon } from './AppIcon'
import { formatLabel, templateEn, templateIcon } from '../lib/workbench'
import type { TaskTemplate } from '../types'

interface Props {
  templates: TaskTemplate[]
  value: string
  disabled?: boolean
  onChange: (key: string) => void
}

/**
 * 任务类型选择器：一行带英文副标题的标签页（radiogroup），下方只展开当前任务的说明与交付格式。
 * 紧凑排列让输入框留在首屏；用原生 radio，键盘方向键切换与读屏语义都由浏览器提供。
 */
export default function TaskTemplatePicker({ templates, value, disabled, onChange }: Props) {
  const active = templates.find((template) => template.key === value) ?? templates[0]
  const gridRef = useRef<HTMLDivElement>(null)
  const hintId = useId()
  const [overflowing, setOverflowing] = useState(false)
  useEffect(() => {
    const grid = gridRef.current
    if (!grid) return
    let disposed = false
    const measure = () => {
      if (!disposed) setOverflowing(grid.scrollWidth > grid.clientWidth + 1)
    }
    measure()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(grid)
    window.addEventListener('resize', measure)
    void document.fonts?.ready.then(measure)
    return () => {
      disposed = true
      observer?.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [templates])
  useEffect(() => {
    const grid = gridRef.current
    const selected = grid?.querySelector<HTMLElement>('.is-selected')
    if (!grid || !selected) return
    const parent = grid.getBoundingClientRect()
    const item = selected.getBoundingClientRect()
    const left =
      item.left < parent.left
        ? item.left - parent.left
        : item.right > parent.right
          ? item.right - parent.right
          : 0
    if (left) grid.scrollBy({ left, behavior: 'auto' })
  }, [value, templates])
  return (
    <fieldset className="task-picker" disabled={disabled}>
      <legend className="task-picker-legend">科研任务</legend>
      <div
        ref={gridRef}
        className="task-grid"
        role="radiogroup"
        aria-label="科研任务"
        aria-describedby={overflowing ? hintId : undefined}
      >
        {templates.map((template) => {
          const checked = template.key === value
          return (
            <label
              key={template.key}
              className={'task-card' + (checked ? ' is-selected' : '')}
              data-template={template.key}
              title={template.tagline}
            >
              <input
                type="radio"
                name="task-template"
                value={template.key}
                checked={checked}
                onChange={() => onChange(template.key)}
                className="visually-hidden"
              />
              <span className="task-card-icon" aria-hidden="true">
                <AppIcon name={templateIcon(template.icon)} size={20} strokeWidth={1.5} />
              </span>
              <span className="task-card-text">
                <strong className="task-card-title">{template.title}</strong>
                {templateEn(template.key) && (
                  <small className="task-card-en" aria-hidden="true">
                    {templateEn(template.key)}
                  </small>
                )}
              </span>
            </label>
          )
        })}
      </div>
      {overflowing && (
        <p className="task-scroll-hint" id={hintId}>
          左右滑动查看更多任务 <span aria-hidden="true">↔</span>
        </p>
      )}
      {active && (
        <p className="task-picker-detail" aria-live="polite">
          <span className="task-card-tagline">{active.tagline}</span>
          <span className="task-card-formats" aria-label="交付格式">
            {active.deliverables.map((format) => (
              <span key={format} className="task-card-format">
                {formatLabel(format)}
              </span>
            ))}
          </span>
        </p>
      )}
    </fieldset>
  )
}
