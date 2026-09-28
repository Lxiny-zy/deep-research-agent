import InfoTip from './InfoTip'
import type { QualityField, QualityPolicy } from '../types'

interface Props {
  fields: QualityField[]
  value: QualityPolicy
  onChange: (next: QualityPolicy) => void
  disabled?: boolean
}

/**
 * 交付质量设置：字段、分组、取值范围与悬浮说明全部来自后端 schema，
 * 与验收逻辑同源——后端新增一个质量阈值，这里自动出现对应的输入项与说明。
 *
 * 版式：每个分组一张卡片；数字阈值排成网格，开关排成「标签 + 说明图标 / 开关」行。
 */
export default function QualitySettings({ fields, value, onChange, disabled }: Props) {
  const groups = [...new Set(fields.map((field) => field.group))]
  return (
    <div className="quality-settings">
      {groups.map((group) => {
        const items = fields.filter((field) => field.group === group)
        const numbers = items.filter((field) => field.kind !== 'bool')
        const toggles = items.filter((field) => field.kind === 'bool')
        return (
          <fieldset key={group} className="quality-group" disabled={disabled}>
            <legend className="quality-group-title">{group}</legend>
            {numbers.length > 0 && (
              <div className="quality-numbers">
                {numbers.map((field) => {
                  const helpId = `quality-help-${field.key}`
                  const numeric = Number(value[field.key] ?? field.default)
                  const invalid =
                    (field.min != null && numeric < field.min) ||
                    (field.max != null && numeric > field.max)
                  return (
                    <label key={field.key} className="quality-number" title={field.help}>
                      <span className="settings-label quality-label">
                        <span>
                          {field.label}
                          {field.unit && <small className="quality-unit">（{field.unit}）</small>}
                        </span>
                        <InfoTip text={field.help} id={helpId} label={`${field.label}说明`} />
                      </span>
                      <input
                        className="input"
                        type="number"
                        required
                        min={field.min ?? undefined}
                        max={field.max ?? undefined}
                        step={1}
                        aria-label={field.label}
                        aria-describedby={helpId}
                        aria-invalid={invalid || undefined}
                        value={Number.isFinite(numeric) ? numeric : ''}
                        onChange={(event) => {
                          const next = Number(event.target.value)
                          if (!Number.isNaN(next)) onChange({ ...value, [field.key]: next })
                        }}
                      />
                      <span className={`quality-range${invalid ? ' is-invalid' : ''}`}>
                        范围 {field.min}–{field.max}，默认 {String(field.default)}
                      </span>
                    </label>
                  )
                })}
              </div>
            )}
            {toggles.length > 0 && (
              <div className="quality-toggles">
                {toggles.map((field) => {
                  const helpId = `quality-help-${field.key}`
                  const current = value[field.key] ?? field.default
                  return (
                    <label key={field.key} className="quality-toggle" title={field.help}>
                      <span className="settings-label quality-label">
                        <span>{field.label}</span>
                        <InfoTip text={field.help} id={helpId} label={`${field.label}说明`} />
                      </span>
                      <span className="toggle-switch">
                        <input
                          type="checkbox"
                          role="switch"
                          aria-label={field.label}
                          aria-describedby={helpId}
                          checked={Boolean(current)}
                          onChange={(event) =>
                            onChange({ ...value, [field.key]: event.target.checked })
                          }
                        />
                        <span className="toggle-track" aria-hidden="true" />
                      </span>
                    </label>
                  )
                })}
              </div>
            )}
          </fieldset>
        )
      })}
    </div>
  )
}
