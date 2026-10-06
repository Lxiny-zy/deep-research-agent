const conditions: Record<string, string> = {
  dataset: '数据集',
  split: '数据划分',
  bands: '波段数',
  spectral_range: '光谱范围',
  scenes: '场景',
  acquisition: '采集方式',
  spatial_size: '空间尺寸',
  calibration: '标定',
  prototype_validation: '原型验证',
  coding_mode: '编码方式',
  dispersive_element: '色散元件',
  protocol: '实验协议',
  train_data: '训练数据',
  hardware: '硬件',
  notes: '补充条件',
}
const asObject = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
const scalar = (value: unknown) =>
  typeof value === 'string'
    ? value
    : typeof value === 'number' && Number.isFinite(value)
      ? String(value)
      : ''

export default function ReadingMeasurement({ records }: { records: Record<string, unknown>[] }) {
  if (!records.length) return null
  return (
    <details className="reading-measurements">
      <summary>数值与实验条件（{records.length} 条记录）</summary>
      <p className="hint">
        这里显示已有记录，仍需对照原文。数值状态不代表实验条件通过核验；空字段表示未记录，不表示原文未报告。
      </p>
      {records.map((record, index) => {
        const quantity = asObject(record.quantity),
          context = asObject(record.conditions)
        const rows = Object.entries(conditions).flatMap(([key, label]) =>
          scalar(context[key]) ? [[label, scalar(context[key])]] : [],
        )
        const recordedValue = scalar(quantity.rendered) || scalar(quantity.value) || '数值未记录'
        const uncertainty = scalar(quantity.uncertainty)
        return (
          <section key={String(record.evidence_id || index)} className="reading-measurement">
            <h4>记录 {index + 1}</h4>
            <p>
              {Object.keys(quantity).length
                ? `原记录：${scalar(quantity.metric)} ${scalar(quantity.comparator)} ${recordedValue}${uncertainty ? ` ± ${uncertainty}` : ''} ${scalar(quantity.unit)}`
                : '尚无结构化数值记录'}
            </p>
            {scalar(record.quantity_reason) && (
              <p className="hint">{scalar(record.quantity_reason)}</p>
            )}
            {rows.length ? (
              <dl>
                {rows.map(([key, value]) => (
                  <div key={key}>
                    <dt>{key}</dt>
                    <dd>{value}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="hint">实验条件尚未记录。</p>
            )}
          </section>
        )
      })}
    </details>
  )
}
