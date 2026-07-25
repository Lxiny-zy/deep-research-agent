import { useState } from 'react'
import { useModelProbe } from '../hooks/useCatalog'
import type { ModelProfile, ModelProfileInput } from '../types'
import { AppIcon } from './AppIcon'

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
  const [replacingApiKey, setReplacingApiKey] = useState(false)
  const [temperature, setTemperature] = useState(initial?.temperature ?? 0.3)
  const [parameterMode, setParameterMode] = useState<'temperature' | 'reasoning'>(
    initial?.parameter_mode ?? 'temperature',
  )
  const [reasoningEffort, setReasoningEffort] = useState<'low' | 'medium' | 'high'>(
    initial?.reasoning_effort ?? 'medium',
  )
  const [isDefault, setIsDefault] = useState(initial?.is_default ?? false)
  const [models, setModels] = useState<string[]>(initial?.model ? [initial.model] : [])
  const [modelQuery, setModelQuery] = useState('')
  const [manualModelEntry, setManualModelEntry] = useState(false)
  const probe = useModelProbe()
  const probeBody = {
    profile_id: initial?.id ?? null,
    base_url: baseUrl.trim() || null,
    api_key: replacingApiKey ? apiKey.trim() : '',
    model,
    parameter_mode: parameterMode,
    reasoning_effort: reasoningEffort,
  }
  const filteredModels = models.filter((item) =>
    item.toLowerCase().includes(modelQuery.trim().toLowerCase()),
  )

  function discover() {
    probe.discover.mutate(probeBody, {
      onSuccess: (result) => {
        setModels(result.models)
        if (!result.models.includes(model) && result.models.length) setModel(result.models[0])
        setManualModelEntry(false)
      },
    })
  }

  function submit() {
    const body: ModelProfileInput = {
      name: name.trim(),
      base_url: baseUrl.trim() || null,
      model: model.trim(),
      temperature,
      parameter_mode: parameterMode,
      reasoning_effort: reasoningEffort,
      is_default: isDefault,
    }
    if (replacingApiKey && apiKey.trim()) body.api_key = apiKey.trim()
    onSubmit(body)
  }

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal editor-modal" role="dialog" aria-modal="true" aria-labelledby="model-editor-title" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header-row">
          <div>
            <span className="eyebrow">MODEL / PROFILE</span>
            <h3 className="panel-title" id="model-editor-title">{editing ? '编辑模型档案' : '新建模型档案'}</h3>
          </div>
          <button className="btn btn-ghost btn-sm icon-button" onClick={onCancel} type="button" aria-label="关闭"><AppIcon name="x" size={15} aria-hidden="true" /></button>
        </div>
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
            Base URL（留空＝官方默认端点）
            <input
              className="input"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://api.openai.com/v1"
            />
          </label>
          <div className="model-discovery-box">
            <div className="row between">
              <div>
                <strong>远端模型</strong>
                <p className="muted small">使用上方端点与密钥读取可用模型，无需手工输入 ID。</p>
              </div>
              <div className="row gap-sm">
                <button type="button" className="btn ghost small" onClick={() => setManualModelEntry((value) => !value)}>
                  <AppIcon name={manualModelEntry ? 'filter' : 'edit'} size={13} aria-hidden="true" />
                  {manualModelEntry ? '使用模型列表' : '手动填写 ID'}
                </button>
                <button className="btn ghost small" onClick={discover} disabled={probe.discover.isPending}>
                  <AppIcon name={probe.discover.isPending ? 'loader' : 'download'} size={13} aria-hidden="true" className={probe.discover.isPending ? 'spin' : ''} />
                  {probe.discover.isPending ? '拉取中…' : '拉取模型列表'}
                </button>
              </div>
            </div>
            {models.length > 0 && (
              <div className="model-search-row">
                <input className="input" value={modelQuery} onChange={(e) => setModelQuery(e.target.value)} placeholder="按名称搜索模型，例如 gpt / deepseek / qwen" />
                <span>{filteredModels.length}/{models.length}</span>
              </div>
            )}
            {manualModelEntry ? (
              <input
                className="input model-select"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="输入供应商提供的模型 ID，例如 deepseek-v4-pro"
              />
            ) : (
              <select className="input model-select" value={model} onChange={(e) => setModel(e.target.value)} disabled={!models.length}>
                {!models.length && <option value={model}>请先拉取模型列表，或切换为手动填写</option>}
                {filteredModels.map((item) => <option key={item} value={item}>{item}</option>)}
              </select>
            )}
            {probe.discover.data && <span className="test-result test-ok"><AppIcon name="check-circle" size={14} aria-hidden="true" />已发现 {probe.discover.data.models.length} 个模型 · {probe.discover.data.latency_ms}ms</span>}
            {probe.discover.isError && <span className="test-result test-fail"><AppIcon name="circle-x" size={14} aria-hidden="true" />{probe.discover.error.message}</span>}
          </div>
          <div className="field-label api-key-field">
            <span>API Key</span>
            {!replacingApiKey ? (
              <div className="saved-credential-row">
                <div>
                  <strong>{initial?.api_key_set ? '使用已保存密钥' : '尚未配置密钥'}</strong>
                  <small>
                    {initial?.api_key_set
                      ? `${initial.api_key_hint} · 拉取模型与测试时由后端安全读取`
                      : '浏览器不会自动填入；点击右侧按钮后手动输入'}
                  </small>
                </div>
                <button
                  type="button"
                  className="btn ghost small"
                  onClick={() => setReplacingApiKey(true)}
                >
                  {initial?.api_key_set ? '更换密钥' : '输入密钥'}
                </button>
              </div>
            ) : (
              <div className="credential-input-row">
                <input
                  className="input"
                  type="password"
                  name="model-api-credential-new"
                  autoComplete="new-password"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  data-lpignore="true"
                  data-1p-ignore="true"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={initial?.api_key_set ? '输入新密钥；留空仍使用原密钥' : '输入模型服务 API Key'}
                />
                <button
                    type="button"
                    className="btn ghost small"
                    onClick={() => {
                      setApiKey('')
                      setReplacingApiKey(false)
                    }}
                  >
                    {initial?.api_key_set ? '使用原密钥' : '取消输入'}
                  </button>
              </div>
            )}
          </div>
          <div className="parameter-mode-switch">
            <button className={parameterMode === 'temperature' ? 'active' : ''} onClick={() => setParameterMode('temperature')}>采样温度</button>
            <button className={parameterMode === 'reasoning' ? 'active' : ''} onClick={() => setParameterMode('reasoning')}>推理强度</button>
          </div>
          <div className="row gap">
            {parameterMode === 'temperature' ? (
              <label className="settings-item" style={{ flex: 1 }}>
                <span className="muted small">温度 {temperature.toFixed(2)}</span>
                <input type="range" min={0} max={2} step={0.05} value={temperature} onChange={(e) => setTemperature(Number(e.target.value))} />
              </label>
            ) : (
              <label className="field-label" style={{ flex: 1 }}>
                Reasoning effort
                <select className="input" value={reasoningEffort} onChange={(e) => setReasoningEffort(e.target.value as 'low' | 'medium' | 'high')}>
                  <option value="low">Low · 更快、更省</option>
                  <option value="medium">Medium · 平衡</option>
                  <option value="high">High · 更深入</option>
                </select>
              </label>
            )}
            <label className="row gap-sm" style={{ alignItems: 'center' }}>
              <input
                type="checkbox"
                checked={isDefault}
                onChange={(e) => setIsDefault(e.target.checked)}
              />
              <span className="muted small">设为全局默认档案</span>
            </label>
          </div>

          {error && <p className="error-text"><AppIcon name="circle-x" size={14} aria-hidden="true" />{error}</p>}

          {(probe.test.data || probe.test.isPending || probe.test.isError) && (
            <p className={`test-result ${probe.test.isPending ? 'test-pending' : probe.test.data?.ok ? 'test-ok' : 'test-fail'}`}>
              {probe.test.isPending ? '正在测试模型调用…' : probe.test.data?.ok ? `连接可用 · ${probe.test.data.latency_ms}ms` : probe.test.data?.detail || probe.test.error?.message}
            </p>
          )}

          <div className="row between" style={{ marginTop: 8 }}>
            <div className="row gap-sm">
              <button className="btn ghost" onClick={onCancel} type="button">取消</button>
              <button className="btn ghost" onClick={() => probe.test.mutate(probeBody)} disabled={probe.test.isPending || !model} type="button"><AppIcon name={probe.test.isPending ? 'loader' : 'activity'} size={13} aria-hidden="true" className={probe.test.isPending ? 'spin' : ''} />测试当前配置</button>
            </div>
            <button className="btn btn-primary" onClick={submit} disabled={pending || !name.trim()} type="button">
              <AppIcon name={pending ? 'loader' : 'save'} size={15} aria-hidden="true" className={pending ? 'spin' : ''} />
              {pending ? '保存中…' : '保存'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
