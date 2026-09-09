import ResearchMotif from '../components/ResearchMotif'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import Skeleton from '../components/Skeleton'
import { AppIcon } from '../components/AppIcon'
import { useRevealOnScroll } from '../hooks/useRevealOnScroll'
import { useModels, useSearchKeys } from '../hooks/useCatalog'
import { useConfig, useUpdateConfig } from '../hooks/useConfig'
import { useSearchProfiles } from '../hooks/useSearchProfiles'
import { BUILTIN_SEARCH_PROFILES, searchSelectionNames } from '../lib/searchProfiles'
import type { ConfigUpdate, ConfigView } from '../types'

interface NumField {
  key:
    | 'max_sub_questions'
    | 'max_rounds'
    | 'max_concurrency'
    | 'results_per_search'
    | 'fulltext_max_chars'
    | 'max_run_seconds'
  label: string
  min: number
  max: number
}

const NUM_FIELDS: NumField[] = [
  { key: 'max_sub_questions', label: '子问题数上限', min: 1, max: 12 },
  { key: 'max_rounds', label: '反思补洞轮数', min: 0, max: 5 },
  { key: 'max_concurrency', label: '并行检索上限', min: 1, max: 16 },
  { key: 'results_per_search', label: '每问检索来源数', min: 1, max: 15 },
  { key: 'fulltext_max_chars', label: 'arXiv 全文字符预算', min: 1_000, max: 200_000 },
  { key: 'max_run_seconds', label: '整次运行期限（秒）', min: 1, max: 86400 },
]

interface FormState {
  llm_model: string
  llm_base_url: string
  llm_api_key: string
  search_profile_ids: string[]
  max_sub_questions: number
  max_rounds: number
  max_concurrency: number
  results_per_search: number
  fulltext_enabled: boolean
  fulltext_max_chars: number
  request_timeout: number
  max_run_seconds: number
  require_corroboration: boolean
}

function toForm(c: ConfigView): FormState {
  return {
    llm_model: c.llm_model,
    llm_base_url: c.llm_base_url ?? '',
    llm_api_key: '',
    search_profile_ids: c.search_profile_ids?.length
      ? c.search_profile_ids
      : (c.search_backends ?? ['tavily']).map((p) => 'builtin:' + p),
    max_sub_questions: c.max_sub_questions,
    max_rounds: c.max_rounds,
    max_concurrency: c.max_concurrency,
    results_per_search: c.results_per_search,
    fulltext_enabled: c.fulltext_enabled,
    fulltext_max_chars: c.fulltext_max_chars,
    request_timeout: c.request_timeout,
    max_run_seconds: c.max_run_seconds,
    require_corroboration: c.require_corroboration ?? false,
  }
}

/** 只读「当前生效配置」摘要：LLM 与检索 key 在角色广场维护,此处仅展示实际生效值 + 兜底来源。 */
function EffectiveConfig({ config }: { config: ConfigView }) {
  const models = useModels()
  const keys = useSearchKeys()
  const searchProfiles = useSearchProfiles()

  const defaultProfile = models.data?.find((p) => p.is_default)
  const selectedIds = config.search_profile_ids?.length
    ? config.search_profile_ids
    : config.search_backends.map((p) => 'builtin:' + p)
  const availableProfiles = searchProfiles.data ?? BUILTIN_SEARCH_PROFILES

  const modelLine = defaultProfile
    ? `${defaultProfile.name} · ${defaultProfile.model} · ${defaultProfile.base_url || '官方端点'}`
    : `${config.llm_model} · ${config.llm_base_url || '官方端点'}（环境变量兜底）`

  const fallbackKeys: Record<string, boolean> = {
    tavily: config.tavily_api_key_set,
    serper: config.serper_api_key_set,
    grok: config.xai_api_key_set,
  }
  const keyLine = selectedIds
    .map((id) => {
      const profile = availableProfiles.find((p) => p.id === id)
      if (!profile) return '存在失效的检索档案，请重新选择'
      if (!profile.enabled) return `${profile.name}：已停用`
      if (['openalex', 'arxiv'].includes(profile.provider)) return `${profile.name}：无需 Key`
      const count =
        keys.data?.filter(
          (k) =>
            k.enabled &&
            k.provider === profile.provider &&
            (profile.key_ids === null || profile.key_ids.includes(k.id)),
        ).length ?? 0
      if (count) return `${profile.name}：${count} 个启用 Key`
      if (profile.builtin && fallbackKeys[profile.provider]) return `${profile.name}：环境配置凭据`
      if (profile.builtin && profile.provider === 'brave')
        return `${profile.name}：请确认 BRAVE_API_KEY 环境配置`
      return `${profile.name}：未配置可用 Key`
    })
    .join('；')

  return (
    <div className="panel" data-reveal="1">
      <div className="row between">
        <h3 className="panel-title">当前生效配置</h3>
        <Link to="/agents" className="nav-link inline-link">
          去角色广场管理 <AppIcon name="arrow-up-right" size={14} aria-hidden="true" />
        </Link>
      </div>
      <p className="hint" style={{ marginBottom: 14 }}>
        模型、检索档案与 Key
        在角色广场维护。研究角色可覆盖下方默认检索档案；自定义检索档案仅使用其绑定的 Key。
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
      <div className="list-row">
        <div>
          <strong>默认检索档案</strong>
          <div className="muted small">
            {searchSelectionNames(
              config.search_profile_ids?.length
                ? config.search_profile_ids
                : config.search_backends.map((p) => 'builtin:' + p),
              searchProfiles.data ?? BUILTIN_SEARCH_PROFILES,
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default function SettingsPage() {
  const pageRef = useRef<HTMLDivElement>(null)
  const { data, isLoading, isError, error } = useConfig()
  useRevealOnScroll(pageRef, [data])
  const update = useUpdateConfig()
  const searchProfiles = useSearchProfiles()
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
      search_profile_ids: form.search_profile_ids,
      max_sub_questions: form.max_sub_questions,
      max_rounds: form.max_rounds,
      max_concurrency: form.max_concurrency,
      results_per_search: form.results_per_search,
      fulltext_enabled: form.fulltext_enabled,
      fulltext_max_chars: form.fulltext_max_chars,
      request_timeout: form.request_timeout,
      max_run_seconds: form.max_run_seconds,
      require_corroboration: form.require_corroboration,
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
    <div className="stack page-stack" ref={pageRef}>
      <header className="page-intro settings-intro page-intro-compact intro-unveil">
        <div>
          <span className="eyebrow">
            <AppIcon name="settings" size={14} aria-hidden="true" /> SYSTEM / SETTINGS
          </span>
          <h1>
            让研究按照<em>你的规则</em>运行。
          </h1>
          <p>管理默认模型、并行策略与反思预算。全局设置是长期偏好，单次研究仍可在新建页覆盖。</p>
        </div>
        <ResearchMotif kind="orbit" className="page-motif" />
      </header>
      {data && <EffectiveConfig config={data} />}

      <section className="settings-form-surface" data-reveal="2">
        <h3 className="panel-title">研究行为默认值</h3>
        <p className="hint" style={{ marginBottom: 18 }}>
          修改后持久化到服务端,对此后创建的研究生效（单次研究亦可在新建页临时覆盖）。
        </p>

        {isLoading && <Skeleton rows={6} />}
        {isError && (
          <p className="error-text">
            <AppIcon name="circle-x" size={14} aria-hidden="true" />
            {error instanceof Error ? error.message : '加载失败'}
          </p>
        )}

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
                  <input
                    className="input"
                    value={form.llm_model}
                    onChange={(e) => setForm({ ...form, llm_model: e.target.value })}
                    placeholder="gpt-4o-mini"
                  />
                </label>
                <label className="settings-item">
                  <span className="muted small">Base URL</span>
                  <input
                    className="input"
                    value={form.llm_base_url}
                    onChange={(e) => setForm({ ...form, llm_base_url: e.target.value })}
                    placeholder="https://api.openai.com/v1"
                  />
                </label>
              </div>
              {!editingGlobalKey ? (
                <div className="saved-credential-row">
                  <div>
                    <strong>全局 API Key</strong>
                    <small>密钥不会回显；更新后立即成为全局兜底凭据</small>
                  </div>
                  <button
                    type="button"
                    className="btn ghost small"
                    onClick={() => setEditingGlobalKey(true)}
                  >
                    {data.llm_api_key_set ? '更换密钥' : '设置密钥'}
                  </button>
                </div>
              ) : (
                <div className="credential-input-row">
                  <input
                    className="input"
                    type="password"
                    name="global-llm-key-new"
                    autoComplete="new-password"
                    data-lpignore="true"
                    data-1p-ignore="true"
                    value={form.llm_api_key}
                    onChange={(e) => setForm({ ...form, llm_api_key: e.target.value })}
                    placeholder="输入新的全局模型 API Key"
                  />
                  <button
                    type="button"
                    className="btn ghost small"
                    onClick={() => {
                      setForm({ ...form, llm_api_key: '' })
                      setEditingGlobalKey(false)
                    }}
                  >
                    取消
                  </button>
                </div>
              )}
            </div>
            <section className="search-backend-config" aria-labelledby="search-backend-title">
              <div className="row between">
                <div>
                  <h3 className="panel-title" id="search-backend-title">
                    默认检索档案
                  </h3>
                  <p className="hint">
                    未单独绑定检索服务的研究角色使用这些档案；多个档案并发检索后合并来源。
                  </p>
                </div>
                <Link to="/agents?tab=keys" className="nav-link inline-link">
                  管理检索档案与 Key
                </Link>
              </div>
              {searchProfiles.isError && (
                <p role="alert" className="error-text">
                  无法加载检索档案，请重试。
                </p>
              )}
              <div className="search-backend-options">
                {(searchProfiles.data ?? BUILTIN_SEARCH_PROFILES).map((profile) => (
                  <label className="search-backend-option" key={profile.id}>
                    <input
                      type="checkbox"
                      checked={form.search_profile_ids.includes(profile.id)}
                      disabled={!profile.enabled}
                      onChange={(event) => {
                        const selected = new Set(form.search_profile_ids)
                        if (event.target.checked) selected.add(profile.id)
                        else if (selected.size > 1) selected.delete(profile.id)
                        setForm({ ...form, search_profile_ids: [...selected] })
                      }}
                    />
                    <span>
                      {profile.name}
                      {!profile.enabled ? '（已停用）' : ''}
                    </span>
                  </label>
                ))}
              </div>
              <p className="hint">
                密钥只在检索资源页维护。内置档案的渠道 Key
                池为空时使用环境配置；自定义档案不会借用其他凭据。
              </p>
            </section>
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
              <label className="safety-gate-setting global-gate">
                <span className="safety-gate-heading">
                  <AppIcon name="file" size={18} aria-hidden="true" />
                  <span className="safety-gate-copy">
                    <strong>启用 arXiv LaTeX 全文</strong>
                    <small id="global-fulltext-help">
                      优先获取 e-print 并按章节筛选；下载或解析失败时自动回退到摘要。
                    </small>
                  </span>
                </span>
                <span className="toggle-switch">
                  <input
                    type="checkbox"
                    role="switch"
                    aria-label="启用 arXiv LaTeX 全文"
                    aria-describedby="global-fulltext-help"
                    checked={form.fulltext_enabled}
                    onChange={(event) =>
                      setForm({ ...form, fulltext_enabled: event.target.checked })
                    }
                  />
                  <span className="toggle-track" aria-hidden="true" />
                </span>
              </label>
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
              <label className="safety-gate-setting global-gate">
                <span className="safety-gate-heading">
                  <AppIcon name="shield" size={18} aria-hidden="true" />
                  <span className="safety-gate-copy">
                    <strong>严格双源门禁</strong>
                    <small id="global-corroboration-help">
                      开启后，仅允许至少两个独立来源交叉印证且无冲突的论断进入报告。
                    </small>
                  </span>
                </span>
                <span className="toggle-switch">
                  <input
                    type="checkbox"
                    role="switch"
                    aria-label="严格双源门禁"
                    aria-describedby="global-corroboration-help"
                    checked={form.require_corroboration}
                    onChange={(event) =>
                      setForm({ ...form, require_corroboration: event.target.checked })
                    }
                  />
                  <span className="toggle-track" aria-hidden="true" />
                </span>
              </label>
            </div>

            <div className="row between" style={{ marginTop: 18 }}>
              <span className="hint">
                {update.isSuccess && !update.isPending && (
                  <>
                    <AppIcon name="check-circle" size={14} aria-hidden="true" />
                    已保存
                  </>
                )}
                {update.isError && (
                  <>
                    <AppIcon name="circle-x" size={14} aria-hidden="true" />
                    {update.error instanceof Error ? update.error.message : '保存失败'}
                  </>
                )}
              </span>
              <button
                className="btn btn-primary"
                onClick={save}
                disabled={update.isPending}
                type="button"
              >
                <AppIcon
                  name={update.isPending ? 'loader' : 'save'}
                  size={15}
                  aria-hidden="true"
                  className={update.isPending ? 'spin' : ''}
                />
                {update.isPending ? '保存中…' : '保存设置'}
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  )
}
