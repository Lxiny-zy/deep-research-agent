import { useState } from 'react'
import type { ModelProfile, ModelProfileInput } from '../types'

interface Props {
  initial?: ModelProfile | null
  onSubmit: (body: ModelProfileInput) => void
  onCancel: () => void
  pending?: boolean
  error?: string
}

/** 模型档案新建/编辑表单。api_key 留空＝保持不变（脱敏表单不回写清空）。 */
export default function ModelProfileEditor({ initial, onSubmit, onCancel, pending, error }: Props) {
  const editing = !!initial
  const [name, setName] = useState(initial?.name ?? '')
  const [baseUrl, setBaseUrl] = useState(initial?.base_url ?? '')
  const [model, setModel] = useState(initial?.model ?? 'gpt-4o-mini')
  const [apiKey, setApiKey] = useState('')
  const [temperature, setTemperature] = useState(initial?.temperature ?? 0.3)
  const [isDefault, setIsDefault] = useState(initial?.is_default ?? false)

  function submit() {
    const body: ModelProfileInput = {
      name: name.trim(),
      base_url: baseUrl.trim() || null,
      model: model.trim(),
      temperature,
      is_default: isDefault,
    }
    if (apiKey) body.api_key = apiKey
    onSubmit(body)
  }

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3 className="panel-title">{editing ? '编辑模型档案' : '新建模型档案'}</h3>
        <div className="stack">
          <label className="field-label">
            档案名
            <input
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="如 GPT-4o / 便宜的 DeepSeek"
            />
          </label>
          <label className="field-label">
            模型 ID
            <input
              className="input"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="gpt-4o-mini / deepseek-chat / qwen-plus"
            />
          </label>
          <label className="field-label">
            Base URL（留空＝官方默认端点）
            <input
              className="input"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://api.openai.com/v1"
            />
          </label>
          <label className="field-label">
            API Key
            <input
              className="input"
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={
                initial?.api_key_set ? `已设置（${initial.api_key_hint}）· 留空不改` : '未设置'
              }
            />
          </label>
          <div className="row gap">
            <label className="settings-item" style={{ flex: 1 }}>
              <span className="muted small">温度 {temperature.toFixed(2)}</span>
              <input
                type="range"
                min={0}
                max={2}
                step={0.05}
                value={temperature}
                onChange={(e) => setTemperature(Number(e.target.value))}
              />
            </label>
            <label className="row gap-sm" style={{ alignItems: 'center' }}>
              <input
                type="checkbox"
                checked={isDefault}
                onChange={(e) => setIsDefault(e.target.checked)}
              />
              <span className="muted small">设为全局默认档案</span>
            </label>
          </div>

          {error && <p className="error-text">✗ {error}</p>}

          <div className="row between" style={{ marginTop: 8 }}>
            <button className="btn ghost" onClick={onCancel}>
              取消
            </button>
            <button className="btn" onClick={submit} disabled={pending || !name.trim()}>
              {pending ? '保存中…' : '保存'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
