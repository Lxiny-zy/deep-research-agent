import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import Skeleton from '../components/Skeleton'
import { AppIcon } from '../components/AppIcon'
import { useModels, useSearchKeys } from '../hooks/useCatalog'
import { useConfig, useUpdateConfig } from '../hooks/useConfig'
import type { ConfigUpdate, ConfigView } from '../types'

interface NumField {
  key: 'max_sub_questions' | 'max_rounds' | 'max_concurrency' | 'results_per_search'
  label: string
  min: number
  max: number
}

const NUM_FIELDS: NumField[] = [
  { key: 'max_sub_questions', label: '子问题数上限', min: 1, max: 12 },
  { key: 'max_rounds', label: '反思补洞轮数', min: 0, max: 5 },
  { key: 'max_concurrency', label: '并行检索上限', min: 1, max: 16 },
  { key: 'results_per_search', label: '每问检索来源数', min: 1, max: 15 },
]

interface FormState {
  llm_model: string
  llm_base_url: string
  llm_api_key: string
  max_sub_questions: number
  max_rounds: number
  max_concurrency: number
  results_per_search: number
  request_timeout: number
}

function toForm(c: ConfigView): FormState {
  return {
    llm_model: c.llm_model,
    llm_base_url: c.llm_base_url ?? '',
    llm_api_key: '',
    max_sub_questions: c.max_sub_questions,
    max_rounds: c.max_rounds,
    max_concurrency: c.max_concurrency,
    results_per_search: c.results_per_search,
    request_timeout: c.request_timeout,
  }
}

/** 只读「当前生效配置」摘要：LLM 与检索 key 在角色广场维护,此处仅展示实际生效值 + 兜底来源。 */
function EffectiveConfig({ config }: { config: ConfigView }) {
  const models = useModels()
  const keys = useSearchKeys()

  const defaultProfile = models.data?.find((p) => p.is_default)
  const activeKeys = keys.data?.filter((k) => k.enabled).length ?? 0

  const modelLine = defaultProfile
    ? `${defaultProfile.name} · ${defaultProfile.model} · ${defaultProfile.base_url || '官方端点'}`
    : `${config.llm_model} · ${config.llm_base_url || '官方端点'}（环境变量兜底）`

  const keyLine =
    activeKeys > 0
      ? `Key 池：启用 ${activeKeys} 个（主备故障转移）`
      : config.tavily_api_key_set
        ? '单个环境变量 key（兜底）'
        : '未配置'

  return (
    <div className="panel">
      <div className="row between">
        <h3 className="panel-title">当前生效配置</h3>
        <Link to="/agents" className="nav-link inline-link">
          去角色广场管理 <AppIcon name="arrow-up-right" size={14} aria-hidden="true" />
        </Link>
      </div>
      <p className="hint" style={{ marginBottom: 14 }}>
        模型与检索 Key 现统一在角色广场维护。角色绑定的档案 / Key 池优先生效,以下环境变量仅作未配置时的兜底。
      </p>
      <div className="list-row">
        <div>
          <strong>默认模型</strong>
          <div className="muted small">{modelLine}</div>
        </div>
      </div>
      <div className="list-row">
        <div>
          <strong>检索 Key</strong>
          <div className="muted small">{keyLine}</div>
        </div>
      </div>
    </div>
  )
}

export default function SettingsPage() {
  const { data, isLoading, isError, error } = useConfig()
  const update = useUpdateConfig()
  const [form, setForm] = useState<FormState | null>(null)
  const [editingGlobalKey, setEditingGlobalKey] = useState(false)

  // 配置到达后初始化表单
  useEffect(() => {
    if (data && form === null) setForm(toForm(data))
  }, [data, form])

  function setNum(key: NumField['key'] | 'request_timeout', raw: string) {
    if (!form) return
    const n = Number(raw)
    if (Number.isNaN(n)) return
    setForm({ ...form, [key]: n })
  }

  function save() {
    if (!form) return
    const body: ConfigUpdate = {
      llm_model: form.llm_model.trim(),
      llm_base_url: form.llm_base_url.trim() || null,
      max_sub_questions: form.max_sub_questions,
      max_rounds: form.max_rounds,
      max_concurrency: form.max_concurrency,
      results_per_search: form.results_per_search,
      request_timeout: form.request_timeout,
    }
    if (editingGlobalKey && form.llm_api_key.trim()) body.llm_api_key = form.llm_api_key.trim()
    update.mutate(body, {
      onSuccess: (next) => {
        setForm(toForm(next))
        setEditingGlobalKey(false)
      },
    })
  }

  return (
    <div className="stack page-stack">
      <header className="page-intro settings-intro">
        <div>
          <span className="eyebrow"><AppIcon name="settings" size={14} aria-hidden="true" /> SYSTEM / SETTINGS</span>
          <h1>让研究按照<em>你的规则</em>运行。</h1>
          <p>管理默认模型、并行策略与反思预算。全局设置是长期偏好，单次研究仍可在新建页覆盖。</p>
        </div>
        <div className="page-intro-mark" aria-hidden="true"><AppIcon name="sliders" size={54} strokeWidth={1.2} /></div>
      </header>
      {data && <EffectiveConfig config={data} />}

      <div className="panel">
        <h3 className="panel-title">研究行为默认值</h3>
        <p className="hint" style={{ marginBottom: 18 }}>
          修改后持久化到服务端,对此后创建的研究生效（单次研究亦可在新建页临时覆盖）。
        </p>

        {isLoading && <Skeleton rows={6} />}
        {isError && <p className="error-text"><AppIcon name="circle-x" size={14} aria-hidden="true" />{error instanceof Error ? error.message : '加载失败'}</p>}

        {form && data && (
          <div className="stack">
            <div className="global-model-config">
              <div className="row between">
                <div>
                  <h3 className="panel-title">全局默认模型</h3>
                  <p className="hint">模型档案不可用或未绑定角色时，系统使用这里的兜底配置。</p>
                </div>
                <span className={`badge ${data.llm_api_key_set ? 'success' : 'warning'}`}>
                  {data.llm_api_key_set ? `密钥已设置 ${data.llm_api_key_hint}` : '尚未设置密钥'}
                </span>
              </div>
              <div className="settings-grid global-model-grid">
                <label className="settings-item">
                  <span className="muted small">默认模型 ID</span>
                  <input className="input" value={form.llm_model} onChange={(e) => setForm({ ...form, llm_model: e.target.value })} placeholder="gpt-4o-mini" />
                </label>
                <label className="settings-item">
                  <span className="muted small">Base URL</span>
                  <input className="input" value={form.llm_base_url} onChange={(e) => setForm({ ...form, llm_base_url: e.target.value })} placeholder="https://api.openai.com/v1" />
                </label>
              </div>
              {!editingGlobalKey ? (
                <div className="saved-credential-row">
                  <div><strong>全局 API Key</strong><small>密钥不会回显；更新后立即成为全局兜底凭据</small></div>
                  <button type="button" className="btn ghost small" onClick={() => setEditingGlobalKey(true)}>{data.llm_api_key_set ? '更换密钥' : '设置密钥'}</button>
                </div>
              ) : (
                <div className="credential-input-row">
                  <input className="input" type="password" name="global-llm-key-new" autoComplete="new-password" data-lpignore="true" data-1p-ignore="true" value={form.llm_api_key} onChange={(e) => setForm({ ...form, llm_api_key: e.target.value })} placeholder="输入新的全局模型 API Key" />
                  <button type="button" className="btn ghost small" onClick={() => { setForm({ ...form, llm_api_key: '' }); setEditingGlobalKey(false) }}>取消</button>
                </div>
              )}
            </div>
            <div className="settings-grid">
              {NUM_FIELDS.map((f) => (
                <label key={f.key} className="settings-item">
                  <span className="muted small">{f.label}</span>
                  <input
                    className="input"
                    type="number"
                    min={f.min}
                    max={f.max}
                    value={form[f.key]}
                    onChange={(e) => setNum(f.key, e.target.value)}
                  />
                </label>
              ))}
              <label className="settings-item">
                <span className="muted small">请求超时（秒）</span>
                <input
                  className="input"
                  type="number"
                  min={1}
                  max={600}
                  value={form.request_timeout}
                  onChange={(e) => setNum('request_timeout', e.target.value)}
                />
              </label>
            </div>

            <div className="row between" style={{ marginTop: 18 }}>
              <span className="hint">
                {update.isSuccess && !update.isPending && <><AppIcon name="check-circle" size={14} aria-hidden="true" />已保存</>}
                {update.isError &&
                  <><AppIcon name="circle-x" size={14} aria-hidden="true" />{update.error instanceof Error ? update.error.message : '保存失败'}</>}
              </span>
              <button className="btn btn-primary" onClick={save} disabled={update.isPending} type="button">
                <AppIcon name={update.isPending ? 'loader' : 'save'} size={15} aria-hidden="true" className={update.isPending ? 'spin' : ''} />
                {update.isPending ? '保存中…' : '保存设置'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
