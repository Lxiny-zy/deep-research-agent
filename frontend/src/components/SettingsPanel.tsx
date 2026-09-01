import { useState } from 'react'
import type { ResearchParams } from '../types'
import { AppIcon } from './AppIcon'

interface Field {
  key: NumericResearchParam
  label: string
  fallback: number
  min: number
  max: number
}

type NumericResearchParam =
  | 'max_sub_questions'
  | 'max_rounds'
  | 'max_concurrency'
  | 'results_per_search'

const FIELDS: Field[] = [
  { key: 'max_sub_questions', label: '子问题数上限', fallback: 5, min: 1, max: 12 },
  { key: 'max_rounds', label: '反思补洞轮数', fallback: 2, min: 0, max: 5 },
  { key: 'max_concurrency', label: '并行检索上限', fallback: 4, min: 1, max: 16 },
  { key: 'results_per_search', label: '每问检索来源数', fallback: 5, min: 1, max: 15 },
]

// 研究参数面板：留空＝沿用服务端默认；填写则随 POST /api/runs 覆盖该次运行。
export default function SettingsPanel({
  value,
  onChange,
  globalRequireCorroboration = false,
}: {
  value: ResearchParams
  onChange: (next: ResearchParams) => void
  globalRequireCorroboration?: boolean
}) {
  const [open, setOpen] = useState(false)

  function set(key: NumericResearchParam, raw: string) {
    const next = { ...value }
    if (raw === '') {
      delete next[key]
    } else {
      // 输入框的 min/max 不拦截手动键入：按字段范围 clamp 并取整，避免提交即 422
      const f = FIELDS.find((x) => x.key === key)
      const n = Math.round(Number(raw))
      if (Number.isNaN(n)) return
      next[key] = f ? Math.min(f.max, Math.max(f.min, n)) : n
    }
    onChange(next)
  }

  const corroborationOverride = value.require_corroboration
  const effectiveCorroboration = corroborationOverride ?? globalRequireCorroboration

  function setCorroboration(checked: boolean) {
    onChange({ ...value, require_corroboration: checked })
  }

  function clearCorroborationOverride() {
    const next = { ...value }
    delete next.require_corroboration
    onChange(next)
  }

  return (
    <div className="settings">
      <button type="button" className="settings-toggle" onClick={() => setOpen((o) => !o)}>
        <AppIcon name={open ? 'chevron-down' : 'chevron-right'} size={15} aria-hidden="true" />
        高级设置（留空＝用服务端默认）
      </button>
      {open && (
        <div className="settings-grid">
          {FIELDS.map((f) => (
            <label key={f.key} className="settings-item">
              <span className="muted small">{f.label}</span>
              <input
                className="input"
                type="number"
                min={f.min}
                max={f.max}
                placeholder={`默认 ${f.fallback}`}
                value={value[f.key] ?? ''}
                onChange={(e) => set(f.key, e.target.value)}
              />
            </label>
          ))}
          <div className="safety-gate-setting per-run-gate">
            <label className="safety-gate-control">
              <span className="safety-gate-copy">
                <strong>严格双源门禁</strong>
                <small>
                  {corroborationOverride == null
                    ? '沿用全局设置'
                    : corroborationOverride
                      ? '本次研究已开启'
                      : '本次研究已关闭'}
                </small>
              </span>
              <span className={`toggle-switch${corroborationOverride == null ? ' inherited' : ''}`}>
                <input
                  type="checkbox"
                  role="switch"
                  aria-label="本次研究启用严格双源门禁"
                  checked={effectiveCorroboration}
                  onChange={(event) => setCorroboration(event.target.checked)}
                />
                <span className="toggle-track" aria-hidden="true" />
              </span>
            </label>
            {corroborationOverride != null && (
              <button
                type="button"
                className="btn ghost sm"
                onClick={clearCorroborationOverride}
                title="恢复为全局默认"
              >
                <AppIcon name="refresh" size={13} aria-hidden="true" />
                沿用全局
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
