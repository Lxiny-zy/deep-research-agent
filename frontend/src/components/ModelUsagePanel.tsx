import type { ResearchEvent } from '../types'

/** Cache tokens are a subset of input tokens, not a reduction in token counts. */
export default function ModelUsagePanel({ events }: { events: ResearchEvent[] }) {
  const records = events.flatMap((event) => {
    const value = event.data?.llm_usage
    return value && typeof value === 'object' ? [value as Record<string, unknown>] : []
  })
  if (!records.length) return null
  function total(field: string) {
    const values = records.flatMap((record) => {
      const value = record[field]
      return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? [value] : []
    })
    return { count: values.length, value: values.reduce((sum, value) => sum + value, 0) }
  }
  const input = total('input_tokens')
  const output = total('output_tokens')
  const cached = total('cached_input_tokens')
  const display = (value: { count: number; value: number }) =>
    value.count ? value.value.toLocaleString() : '供应商未返回'
  return (
    <details className="model-usage-panel">
      <summary>模型用量与缓存</summary>
      <dl>
        <div>
          <dt>输入 Token</dt>
          <dd>{display(input)}</dd>
        </div>
        <div>
          <dt>输出 Token</dt>
          <dd>{display(output)}</dd>
        </div>
        <div>
          <dt>缓存命中 Token</dt>
          <dd>{display(cached)}</dd>
        </div>
      </dl>
      <p className="hint">
        {cached.count} / {records.length} 次已记录调用返回了缓存数据。 命中 Token
        包含在输入用量中；未返回统计的调用不记作未命中。
      </p>
    </details>
  )
}
