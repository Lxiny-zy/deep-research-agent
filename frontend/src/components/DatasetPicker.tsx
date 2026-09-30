import { useState } from 'react'
import { parseDatasetFile } from '../api/client'
import { toBase64 } from '../hooks/useAttachments'
import { AppIcon } from './AppIcon'
import { DATASET_MAX_BYTES, chosenSheet, type DatasetChoice } from '../lib/datasetChoice'

/**
 * 数据分析的数据入口：文件交给服务端按分析规则解析（CSV / TSV / XLSX，中文编码也能读），
 * 多工作表由用户选择；没有数据时可主动勾选示例数据演示，否则不会替用户补数据。
 */
export default function DatasetPicker({
  value,
  onChange,
  demo,
  onDemoChange,
  disabled,
}: {
  value: DatasetChoice | null
  onChange: (value: DatasetChoice | null) => void
  demo: boolean
  onDemoChange: (value: boolean) => void
  disabled?: boolean
}) {
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function pick(file: File | undefined) {
    setError(null)
    if (!file) return onChange(null)
    if (file.size > DATASET_MAX_BYTES) return setError('文件超过 16 MB，请先抽样或聚合后再上传')
    if (file.size === 0) return setError('文件为空')
    setLoading(true)
    try {
      const parsed = await parseDatasetFile({
        filename: file.name,
        data_base64: await toBase64(file),
      })
      onChange({ parsed, sheet: parsed.sheets.length === 1 ? parsed.sheets[0].name : null })
      onDemoChange(false)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '无法解析这个表格')
    } finally {
      setLoading(false)
    }
  }

  const sheet = chosenSheet(value)
  const multiple = (value?.parsed.sheets.length ?? 0) > 1
  return (
    <div className="home-dataset">
      <label className="btn btn-secondary btn-sm" htmlFor="dataset-file">
        <AppIcon
          name={loading ? 'loader' : 'database'}
          size={14}
          className={loading ? 'spin' : ''}
          aria-hidden="true"
        />
        {value ? '更换数据文件' : '上传 CSV / TSV / XLSX'}
      </label>
      <input
        id="dataset-file"
        type="file"
        accept=".csv,.tsv,.txt,.xlsx,text/csv,text/tab-separated-values,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        className="visually-hidden"
        disabled={disabled || loading}
        onChange={(event) => {
          void pick(event.target.files?.[0])
          event.target.value = ''
        }}
      />
      {value ? (
        <span className="home-dataset-file">
          <AppIcon name="file" size={14} aria-hidden="true" />
          已选择 {value.parsed.filename}
          {sheet &&
            `（${sheet.name ? `工作表「${sheet.name}」，` : ''}${sheet.rows} 行 × ${sheet.columns.length} 列）`}
          <button
            type="button"
            className="btn btn-ghost btn-sm icon-button"
            aria-label="移除数据文件"
            onClick={() => onChange(null)}
          >
            <AppIcon name="x" size={13} aria-hidden="true" />
          </button>
        </span>
      ) : (
        <span className="hint">也可以直接把表格粘贴到上方输入框，第一行写分析问题。</span>
      )}
      {value && multiple && (
        <label className="home-dataset-sheet">
          <span>分析哪张工作表</span>
          <select
            value={value.sheet ?? ''}
            onChange={(event) => onChange({ ...value, sheet: event.target.value || null })}
            disabled={disabled}
          >
            <option value="">请选择</option>
            {value.parsed.sheets.map((item) => (
              <option key={item.name} value={item.name}>
                {item.name}（{item.rows} 行 × {item.columns.length} 列）
              </option>
            ))}
          </select>
        </label>
      )}
      {value && value.parsed.skipped.length > 0 && (
        <span className="hint">
          未采用：
          {value.parsed.skipped.map((item) => `「${item.name}」${item.error}`).join('；')}
        </span>
      )}
      {!value && (
        <label className="home-dataset-demo">
          <input
            type="checkbox"
            checked={demo}
            disabled={disabled}
            onChange={(event) => onDemoChange(event.target.checked)}
          />
          暂无数据，用示例数据演示分析流程
        </label>
      )}
      {error && (
        <span className="error-text" role="alert">
          {error}
        </span>
      )}
    </div>
  )
}
