import { useEffect, useState } from 'react'
import Skeleton from '../components/Skeleton'
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
  llm_api_key: string // 始终留空＝保持不变；填写＝覆盖
  tavily_api_key: string
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
    tavily_api_key: '',
    max_sub_questions: c.max_sub_questions,
    max_rounds: c.max_rounds,
    max_concurrency: c.max_concurrency,
    results_per_search: c.results_per_search,
    request_timeout: c.request_timeout,
  }
}

function secretPlaceholder(set: boolean, hint: string): string {
  return set ? `已设置（${hint}）· 留空不改` : '未设置'
}

export default function SettingsPage() {
  const { data, isLoading, isError, error } = useConfig()
  const update = useUpdateConfig()
  const [form, setForm] = useState<FormState | null>(null)

  // 配置到达后初始化表单（密钥保持空）
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
      llm_base_url: form.llm_base_url.trim(),
      max_sub_questions: form.max_sub_questions,
      max_rounds: form.max_rounds,
      max_concurrency: form.max_concurrency,
      results_per_search: form.results_per_search,
      request_timeout: form.request_timeout,
    }
    if (form.llm_api_key) body.llm_api_key = form.llm_api_key
    if (form.tavily_api_key) body.tavily_api_key = form.tavily_api_key
    update.mutate(body, {
      onSuccess: (next) => setForm(toForm(next)), // 重置：密钥清空、回显最新脱敏
    })
  }

  return (
    <div className="stack">
      <div className="panel">
        <h3 className="panel-title">全局设置</h3>
        <p className="hint" style={{ marginBottom: 18 }}>
          修改后持久化到服务端，对此后创建的研究生效。密钥仅脱敏回显，留空表示不修改。
        </p>

        {isLoading && <Skeleton rows={6} />}
        {isError && (
          <p className="error-text">✗ {error instanceof Error ? error.message : '加载失败'}</p>
        )}

        {form && data && (
          <div className="stack">
            <label className="field-label" htmlFor="llm_model">
              LLM 模型
            </label>
            <input
              id="llm_model"
              className="input"
              value={form.llm_model}
              onChange={(e) => setForm({ ...form, llm_model: e.target.value })}
              placeholder="如 gpt-4o-mini / deepseek-chat / qwen-plus"
            />

            <label className="field-label" htmlFor="llm_base_url">
              LLM Base URL（留空＝官方默认端点）
            </label>
            <input
              id="llm_base_url"
              className="input"
              value={form.llm_base_url}
              onChange={(e) => setForm({ ...form, llm_base_url: e.target.value })}
              placeholder="https://api.openai.com/v1"
            />

            <label className="field-label" htmlFor="llm_api_key">
              LLM API Key
            </label>
            <input
              id="llm_api_key"
              className="input"
              type="password"
              autoComplete="off"
              value={form.llm_api_key}
              onChange={(e) => setForm({ ...form, llm_api_key: e.target.value })}
              placeholder={secretPlaceholder(data.llm_api_key_set, data.llm_api_key_hint)}
            />

            <label className="field-label" htmlFor="tavily_api_key">
              Tavily API Key（检索）
            </label>
            <input
              id="tavily_api_key"
              className="input"
              type="password"
              autoComplete="off"
              value={form.tavily_api_key}
              onChange={(e) => setForm({ ...form, tavily_api_key: e.target.value })}
              placeholder={secretPlaceholder(data.tavily_api_key_set, data.tavily_api_key_hint)}
            />

            <label className="field-label">研究行为默认值</label>
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
                {update.isSuccess && !update.isPending ? '✓ 已保存' : ''}
                {update.isError &&
                  `✗ ${update.error instanceof Error ? update.error.message : '保存失败'}`}
              </span>
              <button className="btn" onClick={save} disabled={update.isPending}>
                {update.isPending ? '保存中…' : '保存设置'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
