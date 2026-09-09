import { useState } from 'react'
import { useSearchProfileMutations, useSearchProfiles } from '../hooks/useSearchProfiles'
import { SEARCH_PROVIDERS } from '../lib/searchProfiles'
import type { SearchKey, SearchProfile, SearchProfileInput, SearchProvider } from '../types'

const EMPTY: SearchProfileInput = {
  name: '',
  provider: 'tavily',
  endpoint: '',
  model: '',
  key_ids: [],
  enabled: true,
}

export default function SearchProfilesManager({
  keys,
  references = {},
}: {
  keys: SearchKey[]
  references?: Record<string, string[]>
}) {
  const profiles = useSearchProfiles()
  const mutations = useSearchProfileMutations()
  const [session, setSession] = useState<{ id?: string; form: SearchProfileInput } | null>(null)
  const [formError, setFormError] = useState('')
  const [testing, setTesting] = useState<string | null>(null)
  const [results, setResults] = useState<Record<string, string>>({})
  const form = session?.form
  const patch = (value: Partial<SearchProfileInput>) => {
    setFormError('')
    setSession((current) =>
      current ? { ...current, form: { ...current.form, ...value } } : current,
    )
  }
  function edit(profile?: SearchProfile) {
    setFormError('')
    setSession({
      id: profile?.builtin ? undefined : profile?.id,
      form: profile
        ? {
            name: profile.builtin ? `${profile.name} 专属` : profile.name,
            provider: profile.provider,
            endpoint: profile.endpoint,
            model: profile.model,
            enabled: profile.enabled,
            key_ids:
              profile.key_ids ??
              keys.filter((k) => k.provider === profile.provider && k.enabled).map((k) => k.id),
          }
        : { ...EMPTY, key_ids: [] },
    })
  }
  function save() {
    if (!session || !form) return
    const savedSession = session
    mutations.save.mutate(
      { id: session.id, body: form },
      {
        onSuccess: () => setSession((current) => (current === savedSession ? null : current)),
        onError: (error) => setFormError(error.message),
      },
    )
  }
  function test(profile: SearchProfile) {
    setTesting(profile.id)
    mutations.test.mutate(profile.id, {
      onSuccess: (result) =>
        setResults((current) => ({
          ...current,
          [profile.id]: result.ok ? `可用 · ${result.latency_ms}ms` : result.detail,
        })),
      onError: (error) => setResults((current) => ({ ...current, [profile.id]: error.message })),
      onSettled: () => setTesting(null),
    })
  }
  return (
    <div className="stack search-profile-manager">
      <div className="row between">
        <div>
          <h3 className="panel-title">检索档案</h3>
          <p className="hint">
            设置默认服务或为研究角色绑定专属服务。同一档案中的 Key 按优先级切换，多个档案并发检索。
          </p>
        </div>
        <button
          className="btn btn-primary"
          type="button"
          onClick={() => edit()}
          disabled={mutations.save.isPending}
        >
          新建检索档案
        </button>
      </div>
      {profiles.isLoading && <p role="status">正在加载检索档案…</p>}
      {profiles.isError && (
        <p role="alert" className="error-text">
          {profiles.error.message}
        </p>
      )}
      {mutations.remove.isError && (
        <p role="alert" className="error-text">
          {mutations.remove.error.message}
        </p>
      )}
      <div className="card-grid">
        {profiles.data?.map((profile) => {
          const available = keys.filter(
            (k) =>
              k.enabled &&
              k.provider === profile.provider &&
              (profile.key_ids === null || profile.key_ids.includes(k.id)),
          )
          const free = ['openalex', 'arxiv'].includes(profile.provider)
          return (
            <article className={`role-card${profile.enabled ? '' : ' disabled'}`} key={profile.id}>
              <div className="role-card-head">
                <div className="role-meta">
                  <strong>{profile.name}</strong>
                  <span className="muted small">
                    {SEARCH_PROVIDERS[profile.provider]}
                    {profile.model ? ` · ${profile.model}` : ''}
                  </span>
                </div>
                <span className="badge">
                  {profile.builtin ? '内置' : profile.enabled ? '自定义' : '已停用'}
                </span>
              </div>
              {profile.endpoint && <p className="hint search-endpoint">{profile.endpoint}</p>}
              {!!references[profile.id]?.length && (
                <p className="hint">使用位置：{references[profile.id].join('、')}</p>
              )}
              <p className="hint">
                {free
                  ? '无需 Key'
                  : `${available.length} 个启用的 Key${profile.builtin ? ' · 池为空时使用环境配置' : ' · 仅使用绑定凭据'}`}
              </p>
              {results[profile.id] && (
                <p role="status" className="small">
                  {results[profile.id]}
                </p>
              )}
              <div className="row gap-sm role-card-foot">
                <button
                  className="btn ghost small"
                  type="button"
                  onClick={() => test(profile)}
                  disabled={testing !== null || !profile.enabled}
                >
                  {testing === profile.id ? '测试中…' : '测试档案'}
                </button>
                <button
                  className="btn ghost small"
                  type="button"
                  onClick={() => edit(profile)}
                  disabled={mutations.save.isPending}
                >
                  {profile.builtin ? '复制为专属档案' : '编辑'}
                </button>
                {!profile.builtin && (
                  <button
                    className="btn ghost small danger"
                    type="button"
                    onClick={() => mutations.remove.mutate(profile.id)}
                    disabled={mutations.remove.isPending}
                  >
                    删除
                  </button>
                )}
              </div>
            </article>
          )
        })}
      </div>
      {form && (
        <section className="panel stack search-profile-form" aria-label="编辑检索档案">
          <h3 className="panel-title">{session?.id ? '编辑检索档案' : '新建检索档案'}</h3>
          <div className="settings-grid">
            <label className="field-label">
              档案名称
              <input
                className="input"
                value={form.name}
                onChange={(e) => patch({ name: e.target.value })}
                placeholder="例如：新闻检索 / 学术检索"
              />
            </label>
            <label className="field-label">
              检索协议
              <select
                className="input"
                value={form.provider}
                onChange={(e) =>
                  patch({
                    provider: e.target.value as SearchProvider,
                    key_ids: [],
                    endpoint: '',
                    model: '',
                  })
                }
              >
                {Object.entries(SEARCH_PROVIDERS).map(([value, name]) => (
                  <option value={value} key={value}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            {['responses', 'chat_search', 'grok'].includes(form.provider) && (
              <>
                <label className="field-label">
                  请求端点
                  <input
                    className="input"
                    type="url"
                    value={form.endpoint}
                    onChange={(e) => patch({ endpoint: e.target.value })}
                    placeholder={
                      form.provider === 'chat_search'
                        ? 'https://服务地址/v1/chat/completions'
                        : 'https://服务地址/v1/responses'
                    }
                  />
                </label>
                <label className="field-label">
                  搜索模型
                  <input
                    className="input"
                    value={form.model}
                    onChange={(e) => patch({ model: e.target.value })}
                    placeholder="服务商提供的联网模型名称"
                  />
                </label>
              </>
            )}
          </div>
          {form.provider === 'chat_search' && (
            <p className="hint">
              需使用服务端自带联网能力、返回 citations / search_results / annotations
              的模型。普通聊天模型不会自动获得搜索能力。
            </p>
          )}
          {['responses', 'chat_search', 'grok'].includes(form.provider) && (
            <p className="hint">引用网页将独立获取正文后验证。无法读取的网页不作为原文证据。</p>
          )}
          {!['openalex', 'arxiv'].includes(form.provider) && (
            <fieldset className="search-backend-options">
              <legend>绑定 Key（按优先级自动切换）</legend>
              {keys
                .filter((k) => k.provider === form.provider)
                .map((key) => (
                  <label className="search-backend-option" key={key.id}>
                    <input
                      type="checkbox"
                      checked={form.key_ids.includes(key.id)}
                      onChange={(e) =>
                        patch({
                          key_ids: e.target.checked
                            ? [...form.key_ids, key.id]
                            : form.key_ids.filter((id) => id !== key.id),
                        })
                      }
                    />
                    {key.label || key.api_key_hint} · 优先级 {key.priority}
                    {!key.enabled ? '（已停用）' : ''}
                  </label>
                ))}
              {!keys.some((k) => k.provider === form.provider) && (
                <p className="hint">此渠道还没有 Key，请先在下方 Key 池添加。</p>
              )}
            </fieldset>
          )}
          <label className="search-backend-option">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => patch({ enabled: e.target.checked })}
            />
            启用档案
          </label>
          {!form.enabled && session?.id && !!references[session.id]?.length && (
            <p role="status">停用后以下配置将不可用：{references[session.id].join('、')}</p>
          )}
          {formError && (
            <p className="error-text" role="alert">
              {formError}
            </p>
          )}
          <div className="row gap-sm">
            <button
              className="btn btn-primary"
              type="button"
              onClick={save}
              disabled={mutations.save.isPending || !form.name.trim()}
            >
              保存检索档案
            </button>
            <button
              className="btn ghost"
              type="button"
              onClick={() => setSession(null)}
              disabled={mutations.save.isPending}
            >
              取消
            </button>
          </div>
        </section>
      )}
    </div>
  )
}
