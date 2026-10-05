import { useEffect, useRef, useState } from 'react'
import { mergeDatasetTables } from '../api/client'
import type { DatasetJoinStep, DatasetMergeRequest } from '../types'
import type { DatasetChoice } from '../lib/datasetChoice'

const emptyStep = (): DatasetJoinStep => ({
  sheet: '', left_keys: [], right_keys: [], how: 'inner', relationship: 'one_to_one',
})

export default function DatasetMergePicker({ value, onChange, disabled }: {
  value: DatasetChoice
  onChange: (value: DatasetChoice) => void
  disabled?: boolean
}) {
  const [base, setBase] = useState(value.merge?.request.base ?? value.parsed.sheets[0].name)
  const [steps, setSteps] = useState<DatasetJoinStep[]>(
    value.merge?.request.joins.map((step) => ({ ...emptyStep(), ...step })) ?? [emptyStep()],
  )
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const requestRef = useRef<AbortController | null>(null)
  useEffect(() => () => requestRef.current?.abort(), [])
  const locked = disabled || loading

  function change(nextBase: string, nextSteps: DatasetJoinStep[]) {
    requestRef.current?.abort()
    setBase(nextBase)
    setSteps(nextSteps)
    setError(null)
    onChange({ ...value, mode: 'merge', sheet: null, merge: null })
  }

  function edit(index: number, patch: Partial<DatasetJoinStep>) {
    change(base, steps.map((step, at) => at === index ? { ...step, ...patch } : step))
  }

  function editKey(index: number, position: number, side: 'left_keys' | 'right_keys', column: string) {
    const step = steps[index]
    const size = Math.max(position + 1, step.left_keys.length, step.right_keys.length)
    const left = Array.from({ length: size }, (_, at) => step.left_keys[at] ?? '')
    const right = Array.from({ length: size }, (_, at) => step.right_keys[at] ?? '')
    ;(side === 'left_keys' ? left : right)[position] = column
    edit(index, { left_keys: left, right_keys: right })
  }

  async function preview() {
    const names = new Set([base, ...steps.map((step) => step.sheet)])
    const request: DatasetMergeRequest = {
      base, joins: steps,
      tables: value.parsed.sheets.filter((sheet) => names.has(sheet.name))
        .map((sheet) => ({ name: sheet.name, csv: sheet.csv })),
    }
    const controller = new AbortController()
    requestRef.current?.abort()
    requestRef.current = controller
    setLoading(true)
    setError(null)
    try {
      const result = await mergeDatasetTables(request, controller.signal)
      if (!controller.signal.aborted) onChange({ ...value, mode: 'merge', merge: { request, result } })
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '无法合并表格')
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
  }

  const ready = steps.every((step) => step.sheet && step.left_keys.length &&
    step.left_keys.every(Boolean) && step.right_keys.length && step.right_keys.every(Boolean))
  return <div className="home-dataset-merge">
    <label>主表
      <select aria-label="合并主表" value={base} disabled={locked}
        onChange={(event) => change(event.target.value, [emptyStep()])}>
        {value.parsed.sheets.map((sheet) => <option key={sheet.name} value={sheet.name}>{sheet.name}</option>)}
      </select>
    </label>
    {steps.map((step, index) => {
      const used = new Set([base, ...steps.filter((_, at) => at !== index).map((item) => item.sheet)])
      const leftColumns = [
        ...(value.parsed.sheets.find((sheet) => sheet.name === base)?.columns.map((col) => col.name) ?? []),
        ...steps.slice(0, index).flatMap((previous) =>
          value.parsed.sheets.find((sheet) => sheet.name === previous.sheet)?.columns
            .filter((col) => !previous.right_keys.includes(col.name))
            .map((col) => `${previous.sheet}.${col.name}`) ?? []),
      ]
      const rightColumns = value.parsed.sheets.find((sheet) => sheet.name === step.sheet)?.columns ?? []
      return <fieldset key={index} disabled={locked}>
        <legend>第 {index + 1} 步连接</legend>
        <label>连接表
          <select aria-label={`连接表 第${index + 1}步`} value={step.sheet}
            onChange={(event) => edit(index, { sheet: event.target.value, right_keys: [] })}>
            <option value="">请选择</option>
            {value.parsed.sheets.filter((sheet) => !used.has(sheet.name)).map((sheet) =>
              <option key={sheet.name} value={sheet.name}>{sheet.name}</option>)}
          </select>
        </label>
        {Array.from({ length: Math.max(1, step.left_keys.length) }, (_, position) => {
          const suffix = position ? ` 第${position + 1}键` : ''
          return <div key={position}>
            <label>主表连接键 {position + 1}
              <select aria-label={`主表连接键 第${index + 1}步${suffix}`} value={step.left_keys[position] ?? ''}
                onChange={(event) => editKey(index, position, 'left_keys', event.target.value)}>
                <option value="">请选择</option>
                {leftColumns.map((column) => <option key={column} value={column}>{column}</option>)}
              </select>
            </label>
            <label>对应的右表键 {position + 1}
              <select aria-label={`右表连接键 第${index + 1}步${suffix}`} value={step.right_keys[position] ?? ''}
                onChange={(event) => editKey(index, position, 'right_keys', event.target.value)}>
                <option value="">请选择</option>
                {rightColumns.map((column) => <option key={column.name} value={column.name}>{column.name}</option>)}
              </select>
            </label>
            {position > 0 && <button type="button" onClick={() => edit(index, {
              left_keys: step.left_keys.filter((_, at) => at !== position),
              right_keys: step.right_keys.filter((_, at) => at !== position),
            })}>移除此键</button>}
          </div>
        })}
        {Math.max(1, step.left_keys.length) < 4 && <button type="button" className="btn btn-ghost btn-sm"
          onClick={() => editKey(index, Math.max(1, step.left_keys.length), 'left_keys', '')}>
          添加复合键
        </button>}
        <label>保留范围
          <select aria-label={`保留范围 第${index + 1}步`} value={step.how}
            onChange={(event) => edit(index, { how: event.target.value as DatasetJoinStep['how'] })}>
            <option value="inner">只保留匹配行</option><option value="left">保留主表全部行</option>
          </select>
        </label>
        <label>连接键唯一性
          <select aria-label={`连接关系 第${index + 1}步`} value={step.relationship}
            onChange={(event) => edit(index, { relationship: event.target.value as DatasetJoinStep['relationship'] })}>
            <option value="one_to_one">两表的键均唯一</option>
            <option value="many_to_one">右表键唯一，主表可重复</option>
          </select>
        </label>
        {steps.length > 1 && <button type="button" className="btn btn-ghost btn-sm"
          onClick={() => change(base, steps.filter((_, at) => at !== index))}>移除此步</button>}
      </fieldset>
    })}
    {steps.length < Math.min(7, value.parsed.sheets.length - 1) &&
      <button type="button" className="btn btn-secondary btn-sm" disabled={locked}
        onClick={() => change(base, [...steps, emptyStep()])}>再连接一张表</button>}
    <button type="button" className="btn btn-secondary btn-sm" disabled={locked || !ready}
      onClick={() => void preview()}>{loading ? '正在合并…' : '预览合并结果'}</button>
    {value.merge && <div role="status">
      <p>合并结果：{value.merge.result.rows} 行 × {value.merge.result.columns.length} 列</p>
      {value.merge.result.merge.notes.map((note, index) => <p key={index}>{note}</p>)}
    </div>}
    {error && <p className="error-text" role="alert">{error}</p>}
  </div>
}
