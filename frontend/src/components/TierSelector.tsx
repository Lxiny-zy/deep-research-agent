import type { TierKey, TierSpec, UsageQuota } from '../types'

interface Props {
  tiers: TierSpec[]
  value: TierKey
  onChange: (tier: TierKey) => void
  usage?: UsageQuota
  disabled?: boolean
}

function quotaText(usage: UsageQuota): string | null {
  const parts: string[] = []
  if (usage.runs.limit != null) parts.push(`今日研究 ${usage.runs.used}/${usage.runs.limit} 次`)
  if (usage.tokens.limit != null)
    parts.push(
      `token ${Math.round(usage.tokens.used / 1000)}k/${Math.round(usage.tokens.limit / 1000)}k`,
    )
  return parts.length ? parts.join(' · ') : null
}

/** 研究档位：轻量 / 标准 / 深度（分段单选），附今日额度。 */
export default function TierSelector({ tiers, value, onChange, usage, disabled }: Props) {
  const active = tiers.find((tier) => tier.key === value)
  const quota = usage ? quotaText(usage) : null
  return (
    <fieldset className="home-option" disabled={disabled}>
      <legend className="home-option-label">研究档位</legend>
      <div className="segmented home-segmented" role="radiogroup" aria-label="研究档位">
        {tiers.map((tier) => (
          <label key={tier.key} className={tier.key === value ? 'is-selected' : ''}>
            <input
              type="radio"
              name="research-tier"
              value={tier.key}
              checked={tier.key === value}
              onChange={() => onChange(tier.key)}
              className="visually-hidden"
            />
            {tier.title}
          </label>
        ))}
      </div>
      {active && (
        <span className="hint">
          {active.description} · 最多 {active.max_sub_questions} 个子问题、{active.max_rounds}{' '}
          轮补洞、预算约 {Math.round(active.max_tokens / 1000)}k token
        </span>
      )}
      {quota && (
        <span className={'home-quota' + (usage?.exhausted ? ' is-exhausted' : '')} role="status">
          {usage?.exhausted ? '今日额度已用完 · ' : ''}
          {quota}
        </span>
      )}
    </fieldset>
  )
}
