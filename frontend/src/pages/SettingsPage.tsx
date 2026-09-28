import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import Skeleton from '../components/Skeleton'
import { AppIcon, type AppIconName } from '../components/AppIcon'
import { useModels, useSearchKeys } from '../hooks/useCatalog'
import { useConfig, useUpdateConfig } from '../hooks/useConfig'
import { useSearchProfiles } from '../hooks/useSearchProfiles'
import { BUILTIN_SEARCH_PROFILES, searchSelectionNames } from '../lib/searchProfiles'
import InfoTip from '../components/InfoTip'
import QualitySettings from '../components/QualitySettings'
import { useQualitySchema } from '../hooks/useWorkbench'
import { SETTINGS_HELP } from '../lib/settingsHelp'
import type { ConfigUpdate, ConfigView, QualityPolicy } from '../types'

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
  quality: QualityPolicy
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
    quality: { ...((c.quality ?? {}) as QualityPolicy) },
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
    <div className="settings-effective" aria-label="当前生效配置">
      <div className="settings-effective-head">
        <span className="settings-effective-title">当前生效配置</span>
        <Link to="/agents" className="inline-link">
          去角色广场管理 <AppIcon name="arrow-up-right" size={13} aria-hidden="true" />
        </Link>
      </div>
      <dl className="settings-effective-grid">
        <div>
          <dt>默认模型</dt>
          <dd>{modelLine}</dd>
        </div>
        <div>
          <dt>检索 Key</dt>
          <dd>{keyLine}</dd>
        </div>
        <div>
          <dt>默认检索档案</dt>
          <dd>
            {searchSelectionNames(
              config.search_profile_ids?.length
                ? config.search_profile_ids
                : config.search_backends.map((p) => 'builtin:' + p),
              searchProfiles.data ?? BUILTIN_SEARCH_PROFILES,
            )}
          </dd>
        </div>
      </dl>
      <p className="hint">
        模型、检索档案与 Key
        在角色广场维护。研究角色可覆盖默认检索档案；自定义检索档案仅使用其绑定的 Key。
      </p>
    </div>
  )
}

const SECTIONS: { id: string; label: string; icon: AppIconName }[] = [
  { id: 'settings-model', label: '模型', icon: 'bot' },
  { id: 'settings-search', label: '检索', icon: 'search' },
  { id: 'settings-research', label: '研究参数', icon: 'sliders' },
  { id: 'settings-quality', label: '交付质量', icon: 'shield' },
  { id: 'settings-advanced', label: '高级', icon: 'workflow' },
]

function FieldLabel({
  label,
  help,
  helpId,
  tipLabel,
}: {
  label: string
  help: string
  helpId?: string
  tipLabel?: string
}) {
  return (
    <span className="settings-label settings-field-label">
      {label}
      <InfoTip text={help} id={helpId} label={tipLabel ?? `${label}说明`} />
    </span>
  )
}

function SwitchRow({
  label,
  description,
  descriptionId,
  help,
  tipLabel,
  checked,
  onChange,
}: {
  label: string
  description: string
  descriptionId: string
  help: string
  tipLabel: string
  checked: boolean
  onChange: (value: boolean) => void
}) {
  return (
    <label className="settings-switch-row">
      <span className="settings-switch-copy">
        <strong className="settings-label">
          {label}
          <InfoTip text={help} label={tipLabel} />
        </strong>
        <small id={descriptionId}>{description}</small>
      </span>
      <span className="toggle-switch">
        <input
          type="checkbox"
          role="switch"
          aria-label={label}
          aria-describedby={descriptionId}
          checked={checked}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span className="toggle-track" aria-hidden="true" />
      </span>
    </label>
  )
}

export default function SettingsPage() {
  const { data, isLoading, isError, error } = useConfig()
  const update = useUpdateConfig()
  const searchProfiles = useSearchProfiles()
  const qualitySchema = useQualitySchema()
  const [form, setForm] = useState<FormState | null>(null)
  const [editingGlobalKey, setEditingGlobalKey] = useState(false)
  const [activeSection, setActiveSection] = useState(SECTIONS[0].id)
  const hasChanges = Boolean(form && data && JSON.stringify(form) !== JSON.stringify(toForm(data)))

  // 配置到达后初始化表单
  useEffect(() => {
    if (data && form === null) setForm(toForm(data))
  }, [data, form])

  // 左侧锚点导航跟随滚动高亮当前分区
  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined' || !form) return
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
        if (visible[0]) setActiveSection(visible[0].target.id)
      },
      { rootMargin: '-80px 0px -60% 0px' },
    )
    for (const section of SECTIONS) {
      const element = document.getElementById(section.id)
      if (element) observer.observe(element)
    }
    return () => observer.disconnect()
  }, [form])

  function setNum(key: NumField['key'] | 'request_timeout', raw: string) {
    if (!form) return
    const n = Number(raw)
    if (Number.isNaN(n)) return
    setForm({ ...form, [key]: n })
  }

  function save() {
    if (!form) return
    const body: ConfigUpdate = {
      version: data?.version,
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
      quality: form.quality,
    }
    if (editingGlobalKey && form.llm_api_key.trim()) body.llm_api_key = form.llm_api_key.trim()
    update.mutate(body, {
      onSuccess: (next) => {
        setForm(toForm(next))
        setEditingGlobalKey(false)
      },
    })
  }

  function jump(id: string) {
    setActiveSection(id)
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div className="stack page-stack settings-page">
      <header className="page-header">
        <div>
          <h1>设置</h1>
          <p>管理默认模型、检索来源、研究参数与交付质量口径。修改后对新建任务生效。</p>
        </div>
      </header>

      <div className="settings-layout">
        <nav className="settings-nav" aria-label="设置分区">
          {SECTIONS.map((section) => (
            <a
              key={section.id}
              href={`#${section.id}`}
              className={`settings-nav-link${activeSection === section.id ? ' is-active' : ''}`}
              aria-current={activeSection === section.id ? 'true' : undefined}
              onClick={(event) => {
                event.preventDefault()
                jump(section.id)
              }}
            >
              <AppIcon name={section.icon} size={16} aria-hidden="true" />
              {section.label}
            </a>
          ))}
        </nav>

        <div className="settings-content">
          {isLoading && (
            <section className="panel">
              <Skeleton rows={6} />
            </section>
          )}
          {isError && (
            <p className="alert error" role="alert">
              <AppIcon name="circle-x" size={15} aria-hidden="true" />
              {error instanceof Error ? error.message : '加载失败'}
            </p>
          )}

          {form && data && (
            <form
              className="settings-form"
              onSubmit={(event) => {
                event.preventDefault()
                save()
              }}
            >
              <fieldset className="settings-fields" disabled={update.isPending}>
                <section
                  className="panel settings-card"
                  id="settings-model"
                  aria-labelledby="settings-model-title"
                >
                  <div className="settings-card-head">
                    <div>
                      <h2 id="settings-model-title">模型</h2>
                      <p className="hint">模型档案不可用或未绑定角色时，系统使用这里的兜底配置。</p>
                    </div>
                    <span className={`badge ${data.llm_api_key_set ? 'success' : 'warning'}`}>
                      {data.llm_api_key_set
                        ? `密钥已设置 ${data.llm_api_key_hint}`
                        : '尚未设置密钥'}
                    </span>
                  </div>
                  <EffectiveConfig config={data} />
                  <div className="settings-grid">
                    <label className="settings-item">
                      <FieldLabel
                        label="默认模型 ID"
                        help={SETTINGS_HELP.llm_model}
                        tipLabel="默认模型 ID 说明"
                      />
                      <input
                        className="input"
                        required
                        value={form.llm_model}
                        onChange={(e) => setForm({ ...form, llm_model: e.target.value })}
                        placeholder="gpt-4o-mini"
                      />
                    </label>
                    <label className="settings-item">
                      <FieldLabel
                        label="Base URL"
                        help={SETTINGS_HELP.llm_base_url}
                        tipLabel="Base URL 说明"
                      />
                      <input
                        className="input"
                        value={form.llm_base_url}
                        onChange={(e) => setForm({ ...form, llm_base_url: e.target.value })}
                        placeholder="https://api.openai.com/v1"
                      />
                    </label>
                  </div>
                  {!editingGlobalKey ? (
                    <div className="settings-credential">
                      <div>
                        <strong className="settings-label">
                          全局 API Key
                          <InfoTip text={SETTINGS_HELP.llm_api_key} label="全局 API Key 说明" />
                        </strong>
                        <small>密钥不会回显；更新后立即成为全局兜底凭据</small>
                      </div>
                      <button
                        type="button"
                        className="btn btn-sm"
                        onClick={() => setEditingGlobalKey(true)}
                      >
                        <AppIcon name="key" size={14} aria-hidden="true" />
                        {data.llm_api_key_set ? '更换密钥' : '设置密钥'}
                      </button>
                    </div>
                  ) : (
                    <div className="settings-credential is-editing">
                      <input
                        className="input"
                        type="password"
                        aria-label="全局模型 API Key"
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
                        className="btn btn-ghost btn-sm"
                        onClick={() => {
                          setForm({ ...form, llm_api_key: '' })
                          setEditingGlobalKey(false)
                        }}
                      >
                        取消
                      </button>
                    </div>
                  )}
                </section>

                <section
                  className="panel settings-card"
                  id="settings-search"
                  aria-labelledby="search-backend-title"
                >
                  <div className="settings-card-head">
                    <div>
                      <h2 className="settings-label" id="search-backend-title">
                        默认检索档案
                        <InfoTip text={SETTINGS_HELP.search_profiles} label="默认检索档案说明" />
                      </h2>
                      <p className="hint">
                        未单独绑定检索服务的研究角色使用这些档案；多个档案并发检索后合并来源。
                      </p>
                    </div>
                    <Link to="/agents?tab=keys" className="inline-link">
                      管理检索档案与 Key
                      <AppIcon name="arrow-up-right" size={13} aria-hidden="true" />
                    </Link>
                  </div>
                  {searchProfiles.isError && (
                    <p role="alert" className="error-text">
                      无法加载检索档案，请重试。
                    </p>
                  )}
                  <div className="settings-options">
                    {(searchProfiles.data ?? BUILTIN_SEARCH_PROFILES).map((profile) => {
                      const checked = form.search_profile_ids.includes(profile.id)
                      return (
                        <label
                          className={`settings-option${checked ? ' is-checked' : ''}${
                            profile.enabled ? '' : ' is-disabled'
                          }`}
                          key={profile.id}
                        >
                          <input
                            type="checkbox"
                            checked={checked}
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
                      )
                    })}
                  </div>
                  <p className="hint">
                    密钥只在检索资源页维护。内置档案的渠道 Key
                    池为空时使用环境配置；自定义档案不会借用其他凭据。
                  </p>
                </section>

                <section
                  className="panel settings-card"
                  id="settings-research"
                  aria-labelledby="settings-research-title"
                >
                  <div className="settings-card-head">
                    <div>
                      <h2 id="settings-research-title">研究参数</h2>
                      <p className="hint">
                        研究行为的全局默认值，单次任务仍可在工作台的高级设置里临时覆盖。
                      </p>
                    </div>
                  </div>
                  <div className="settings-grid settings-grid-3">
                    {NUM_FIELDS.map((f) => (
                      <label key={f.key} className="settings-item" title={SETTINGS_HELP[f.key]}>
                        <FieldLabel
                          label={f.label}
                          help={SETTINGS_HELP[f.key]}
                          helpId={`settings-help-${f.key}`}
                        />
                        <input
                          aria-label={f.label}
                          aria-describedby={`settings-help-${f.key}`}
                          className="input"
                          type="number"
                          required
                          min={f.min}
                          max={f.max}
                          value={form[f.key]}
                          onChange={(e) => setNum(f.key, e.target.value)}
                        />
                        <span className="settings-range">
                          范围 {f.min.toLocaleString()}–{f.max.toLocaleString()}
                        </span>
                      </label>
                    ))}
                    <label className="settings-item" title={SETTINGS_HELP.request_timeout}>
                      <FieldLabel
                        label="请求超时（秒）"
                        help={SETTINGS_HELP.request_timeout}
                        helpId="settings-help-request_timeout"
                        tipLabel="请求超时说明"
                      />
                      <input
                        aria-label="请求超时（秒）"
                        aria-describedby="settings-help-request_timeout"
                        className="input"
                        type="number"
                        required
                        min={1}
                        max={600}
                        value={form.request_timeout}
                        onChange={(e) => setNum('request_timeout', e.target.value)}
                      />
                      <span className="settings-range">范围 1–600</span>
                    </label>
                  </div>
                  <div className="settings-switches">
                    <SwitchRow
                      label="启用 arXiv LaTeX 全文"
                      description="优先获取 e-print 并按章节筛选；下载或解析失败时自动回退到摘要。"
                      descriptionId="global-fulltext-help"
                      help={SETTINGS_HELP.fulltext_enabled}
                      tipLabel="arXiv 全文说明"
                      checked={form.fulltext_enabled}
                      onChange={(value) => setForm({ ...form, fulltext_enabled: value })}
                    />
                    <SwitchRow
                      label="严格双源门禁"
                      description="开启后，仅允许至少两个独立来源交叉印证且无冲突的论断进入报告。"
                      descriptionId="global-corroboration-help"
                      help={SETTINGS_HELP.require_corroboration}
                      tipLabel="严格双源门禁说明"
                      checked={form.require_corroboration}
                      onChange={(value) => setForm({ ...form, require_corroboration: value })}
                    />
                  </div>
                </section>
              </fieldset>

              <section
                className="panel settings-card settings-quality"
                id="settings-quality"
                aria-labelledby="settings-quality-title"
              >
                <div className="settings-card-head">
                  <div>
                    <h2 id="settings-quality-title">交付质量</h2>
                    <p className="hint">
                      科研交付物的验收口径：引用下限、学术文体、返工次数等。修改后对新建任务生效，
                      已开始的任务按创建时的口径验收。鼠标悬停在{' '}
                      <AppIcon name="help" size={12} aria-hidden="true" />{' '}
                      上可查看每一项的详细说明。
                    </p>
                  </div>
                  <button
                    type="button"
                    className="btn btn-sm"
                    disabled={!qualitySchema.data || update.isPending}
                    onClick={() =>
                      setForm({
                        ...form,
                        quality: Object.fromEntries(
                          (qualitySchema.data ?? []).map((field) => [field.key, field.default]),
                        ),
                      })
                    }
                  >
                    <AppIcon name="undo" size={14} aria-hidden="true" />
                    恢复默认
                  </button>
                </div>
                {qualitySchema.isLoading && <Skeleton rows={3} />}
                {qualitySchema.isError && (
                  <p className="error-text" role="alert">
                    无法加载交付质量设置，请刷新重试。
                  </p>
                )}
                {qualitySchema.data && (
                  <QualitySettings
                    fields={qualitySchema.data}
                    value={form.quality}
                    onChange={(quality) => setForm({ ...form, quality })}
                    disabled={update.isPending}
                  />
                )}
              </section>

              <div className="settings-savebar">
                <span
                  className={`settings-save-status${hasChanges ? ' is-dirty' : ''}`}
                  role={update.isError ? 'alert' : 'status'}
                >
                  {!update.isError && hasChanges && !update.isPending && '有未保存的更改'}
                  {!hasChanges && !update.isSuccess && '保存后，对新建研究生效'}
                  {update.isSuccess && !update.isPending && !hasChanges && (
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
                <div className="row">
                  {hasChanges && (
                    <button
                      type="button"
                      className="btn btn-ghost"
                      disabled={update.isPending}
                      onClick={() => {
                        setForm(toForm(data))
                        setEditingGlobalKey(false)
                      }}
                    >
                      放弃更改
                    </button>
                  )}
                  <button className="btn btn-primary" disabled={update.isPending} type="submit">
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
            </form>
          )}

          <section
            className="panel settings-card settings-advanced"
            id="settings-advanced"
            aria-labelledby="settings-advanced-title"
          >
            <div className="settings-card-head">
              <div>
                <h2 id="settings-advanced-title">高级</h2>
                <p className="hint">
                  自定义研究流程与智能体角色。日常任务直接在工作台选择任务类型即可，不需要改这里。
                </p>
              </div>
            </div>
            <ul className="settings-advanced-links">
              <li>
                <Link to="/workflows">
                  <span className="settings-advanced-icon" aria-hidden="true">
                    <AppIcon name="workflow" size={18} />
                  </span>
                  <span className="settings-advanced-text">
                    <strong>工作流构建</strong>
                    <small>编排自定义研究流程</small>
                  </span>
                  <AppIcon name="chevron-right" size={16} aria-hidden="true" />
                </Link>
              </li>
              <li>
                <Link to="/agents">
                  <span className="settings-advanced-icon" aria-hidden="true">
                    <AppIcon name="users" size={18} />
                  </span>
                  <span className="settings-advanced-text">
                    <strong>角色广场</strong>
                    <small>管理与编辑智能体角色卡</small>
                  </span>
                  <AppIcon name="chevron-right" size={16} aria-hidden="true" />
                </Link>
              </li>
            </ul>
          </section>
        </div>
      </div>
    </div>
  )
}
