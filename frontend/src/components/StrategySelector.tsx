import type { StrategyKey, TemplateStrategy } from '../types'

interface Props {
  strategies: TemplateStrategy[]
  value: StrategyKey
  onChange: (strategy: StrategyKey) => void
  disabled?: boolean
}

/**
 * 检索策略：任务「怎么找证据」。深度检索是其中一种策略，而不是独立的任务类型。
 * 只有一种策略的任务（精读、审稿、数据分析只用你给的材料）显示为只读说明。
 */
export default function StrategySelector({ strategies, value, onChange, disabled }: Props) {
  const active = strategies.find((item) => item.key === value) ?? strategies[0]
  if (!active) return null
  if (strategies.length === 1) {
    return (
      <div className="home-option">
        <span className="home-option-label">检索策略</span>
        <div className="home-option-static">
          <strong>{active.label}</strong>
          <span className="hint">{active.description}</span>
        </div>
      </div>
    )
  }
  return (
    <fieldset className="home-option" disabled={disabled}>
      <legend className="home-option-label">检索策略</legend>
      <div className="segmented home-segmented" role="radiogroup" aria-label="检索策略">
        {strategies.map((item) => (
          <label key={item.key} className={item.key === active.key ? 'is-selected' : ''}>
            <input
              type="radio"
              name="research-strategy"
              value={item.key}
              checked={item.key === active.key}
              onChange={() => onChange(item.key)}
              className="visually-hidden"
            />
            {item.label}
          </label>
        ))}
      </div>
      <span className="hint">{active.description}</span>
    </fieldset>
  )
}
