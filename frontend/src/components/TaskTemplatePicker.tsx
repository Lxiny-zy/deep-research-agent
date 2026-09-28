import { AppIcon } from './AppIcon'
import { formatLabel, templateIcon } from '../lib/workbench'
import type { TaskTemplate } from '../types'

interface Props {
  templates: TaskTemplate[]
  value: string
  disabled?: boolean
  onChange: (key: string) => void
}

/**
 * 任务类型选择器：一行紧凑的单选标签（radiogroup），下方只展开当前任务的说明与交付格式。
 * 紧凑排列让输入框留在首屏；用原生 radio，键盘方向键切换与读屏语义都由浏览器提供。
 */
export default function TaskTemplatePicker({ templates, value, disabled, onChange }: Props) {
  const active = templates.find((template) => template.key === value) ?? templates[0]
  return (
    <fieldset className="task-picker" disabled={disabled}>
      <legend className="task-picker-legend">科研任务</legend>
      <div className="task-grid" role="radiogroup" aria-label="科研任务">
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
                <AppIcon name={templateIcon(template.icon)} size={15} />
              </span>
              <strong className="task-card-title">{template.title}</strong>
            </label>
          )
        })}
      </div>
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
