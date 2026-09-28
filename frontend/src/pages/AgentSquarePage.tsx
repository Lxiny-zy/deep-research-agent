import { useId, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import AgentCardEditor from '../components/AgentCardEditor'
import BuiltinRoleGallery from '../components/BuiltinRoleGallery'
import ModelProfileCard from '../components/ModelProfileCard'
import ModelProfileEditor from '../components/ModelProfileEditor'
import SearchKeyCard from '../components/SearchKeyCard'
import SearchProfilesManager from '../components/SearchProfilesManager'
import { useSearchProfiles, useSearchResourceImpact } from '../hooks/useSearchProfiles'
import { SEARCH_PROVIDERS, searchSelectionNames } from '../lib/searchProfiles'
import Skeleton from '../components/Skeleton'
import EmptyState from '../components/EmptyState'
import { AgentGlyph, AppIcon, type AppIconName } from '../components/AppIcon'
import {
  useAgentMutations,
  useAgents,
  useModelMutations,
  useModels,
  useRoles,
  useSearchKeyMutations,
  useSearchKeys,
} from '../hooks/useCatalog'
import { behaviorLabel } from '../lib/behaviors'
import type {
  AgentCard,
  AgentCardInput,
  ModelProfile,
  ModelProfileInput,
  SearchKeyInput,
} from '../types'

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : '操作失败'
}

type Tab = 'agents' | 'models' | 'keys'

// 每次打开弹窗都是一个独立的编辑会话（同 WorkflowBuilderPage）：
// 错误与保存中状态按会话 key 隔离，避免上一次失败的提示残留到新弹窗。
type EditSession<T> = {
  key: number
  item: T | null // null＝新建
}

const TABS: { key: Tab; label: string; icon: AppIconName }[] = [
  { key: 'agents', label: '角色', icon: 'users' },
  { key: 'models', label: '模型档案', icon: 'server' },
  { key: 'keys', label: '检索资源', icon: 'key' },
]

export default function AgentSquarePage() {
  const tabsId = useId()
  const [searchParams, setSearchParams] = useSearchParams()
  const agents = useAgents()
  const models = useModels()
  const keys = useSearchKeys()
  const searchProfiles = useSearchProfiles()
  const resourceImpact = useSearchResourceImpact()
  const roles = useRoles()
  const agentM = useAgentMutations()
  const modelM = useModelMutations()
  const keyM = useSearchKeyMutations()

  const requestedTab = searchParams.get('tab')
  const tab: Tab = requestedTab === 'models' || requestedTab === 'keys' ? requestedTab : 'agents'
  function setTab(nextTab: Tab) {
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous)
        if (nextTab === 'agents') next.delete('tab')
        else next.set('tab', nextTab)
        return next
      },
      { replace: true },
    )
  }
  const sessionSequence = useRef(0)
  const [agentSession, setAgentSession] = useState<EditSession<AgentCard> | undefined>(undefined) // undefined=关闭
  const [modelSession, setModelSession] = useState<EditSession<ModelProfile> | undefined>(undefined)
  const [savingSessionKey, setSavingSessionKey] = useState<number | null>(null)
  const [saveErrors, setSaveErrors] = useState<Record<number, string>>({})
  const [newKey, setNewKey] = useState<SearchKeyInput>({
    provider: 'tavily',
    label: '',
    api_key: '',
    priority: 0,
  })

  const profiles = models.data ?? []

  function openAgentEditor(agent: AgentCard | null) {
    setAgentSession({ key: ++sessionSequence.current, item: agent })
  }

  function openModelEditor(model: ModelProfile | null) {
    setModelSession({ key: ++sessionSequence.current, item: model })
  }

  function sessionOptions(sessionKey: number, close: () => void) {
    setSavingSessionKey(sessionKey)
    setSaveErrors((current) => {
      const next = { ...current }
      delete next[sessionKey]
      return next
    })
    return {
      onSuccess: () => close(),
      onError: (error: unknown) => {
        setSaveErrors((current) => ({ ...current, [sessionKey]: errMsg(error) }))
      },
      onSettled: () => {
        setSavingSessionKey((current) => (current === sessionKey ? null : current))
      },
    }
  }

  function saveAgent(body: AgentCardInput) {
    const session = agentSession
    if (!session) return
    const options = sessionOptions(session.key, () =>
      setAgentSession((current) => (current?.key === session.key ? undefined : current)),
    )
    if (session.item) agentM.update.mutate({ id: session.item.id, body }, options)
    else agentM.create.mutate(body, options)
  }

  function saveModel(body: ModelProfileInput) {
    const session = modelSession
    if (!session) return
    const options = sessionOptions(session.key, () =>
      setModelSession((current) => (current?.key === session.key ? undefined : current)),
    )
    if (session.item) modelM.update.mutate({ id: session.item.id, body }, options)
    else modelM.create.mutate(body, options)
  }

  function addKey() {
    const apiKey = newKey.api_key?.trim() ?? ''
    if (!apiKey) return
    keyM.create.mutate(
      {
        provider: newKey.provider,
        label: newKey.label?.trim(),
        api_key: apiKey,
        priority: newKey.priority,
      },
      {
        onSuccess: () =>
          setNewKey({ provider: newKey.provider, label: '', api_key: '', priority: 0 }),
      },
    )
  }

  return (
    <div className="stack page-stack catalog-page">
      <header className="page-header">
        <div>
          <h1>角色广场</h1>
          <p>管理研究角色、模型档案与检索资源。工作流按标识引用这里的角色。</p>
        </div>
      </header>

      <div className="tabs agent-tabs" role="tablist" aria-label="角色广场分类">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            id={`${tabsId}-${t.key}`}
            aria-controls={`${tabsId}-${t.key}-panel`}
            tabIndex={tab === t.key ? 0 : -1}
            className={`tab${tab === t.key ? ' active' : ''}`}
            onClick={() => setTab(t.key)}
            onKeyDown={(event) => {
              const index = TABS.findIndex((item) => item.key === t.key)
              const next =
                event.key === 'ArrowRight'
                  ? (index + 1) % TABS.length
                  : event.key === 'ArrowLeft'
                    ? (index + TABS.length - 1) % TABS.length
                    : event.key === 'Home'
                      ? 0
                      : event.key === 'End'
                        ? TABS.length - 1
                        : null
              if (next === null) return
              event.preventDefault()
              setTab(TABS[next].key)
              document.getElementById(`${tabsId}-${TABS[next].key}`)?.focus()
            }}
          >
            <AppIcon name={t.icon} size={15} aria-hidden="true" />
            {t.label}
          </button>
        ))}
      </div>

      {/* ── 角色 ── */}
      {tab === 'agents' && (
        <section
          className="panel catalog-panel"
          role="tabpanel"
          id={`${tabsId}-agents-panel`}
          aria-labelledby={`${tabsId}-agents`}
          tabIndex={0}
        >
          <div className="panel-header">
            <div>
              <h2 className="panel-title">研究角色</h2>
              <p className="hint">内置角色始终可用；自定义角色可以绑定专属提示词与模型。</p>
            </div>
            <button className="btn btn-primary" onClick={() => openAgentEditor(null)}>
              <AppIcon name="plus" size={15} aria-hidden="true" />
              新建角色
            </button>
          </div>
          <div className="catalog-panel-body">
            {roles.isLoading && <Skeleton rows={2} />}
            {roles.isError && (
              <p className="error-text" role="alert">
                内置角色加载失败：{errMsg(roles.error)}
              </p>
            )}
            <BuiltinRoleGallery roles={roles.data ?? []} />

            <div className="catalog-section-head custom-follow">
              <h3 className="builtin-rail-title">自定义角色</h3>
              <span className="hint">
                选一种行为模板，自定义提示词与绑定模型；启用后可被工作流引用。
              </span>
            </div>

            {agents.isLoading && <Skeleton rows={4} />}
            {agents.isError && (
              <p className="error-text" role="alert">
                <AppIcon name="circle-x" size={14} aria-hidden="true" />
                {errMsg(agents.error)}
              </p>
            )}
            {(agentM.remove.isError || agentM.update.isError) && !agentSession && (
              <p className="error-text" role="alert">
                {errMsg(agentM.remove.error ?? agentM.update.error)}
              </p>
            )}
            {!agents.isLoading && !agents.isError && agents.data?.length === 0 && (
              <EmptyState
                icon="user-cog"
                title="把研究经验，交给专属角色"
                description="内置角色已经可以使用。需要特定的提示词或模型时，创建自己的研究助手。"
              >
                <button className="btn btn-secondary" onClick={() => openAgentEditor(null)}>
                  创建自定义角色
                </button>
              </EmptyState>
            )}

            <div className="card-grid custom-role-grid">
              {agents.data?.map((a, index) => (
                <div
                  key={a.id}
                  className={`role-card catalog-card custom-role-card${a.enabled ? '' : ' disabled'}`}
                  data-card-size="fixed"
                  data-card-index={`R-${String(index + 1).padStart(2, '0')}`}
                  data-behavior={a.behavior}
                  title={a.display_name || a.name}
                >
                  <span className="catalog-card-accent" aria-hidden="true" />
                  <div className="catalog-card-top">
                    <span className="catalog-card-index">
                      R-{String(index + 1).padStart(2, '0')}
                    </span>
                    <span className="badge">{behaviorLabel(a.behavior)}</span>
                  </div>
                  <div className="catalog-card-title">
                    <span className="catalog-card-glyph" aria-hidden="true">
                      <AgentGlyph icon={a.icon} behavior={a.behavior} size={20} />
                    </span>
                    <div className="catalog-card-name">
                      <strong>{a.display_name || a.name}</strong>
                      <code className="catalog-card-code">{a.name}</code>
                    </div>
                  </div>
                  <p className="catalog-card-desc">
                    {a.description?.trim() || '自定义角色，可在工作流中复用。'}
                  </p>
                  <div
                    className="catalog-card-detail"
                    title={`模型：${a.model_profile_name ?? '默认档案'}`}
                  >
                    <AppIcon name="server" size={13} aria-hidden="true" />
                    <span>模型：{a.model_profile_name ?? '默认档案'}</span>
                  </div>
                  {a.behavior === 'research' && (
                    <p className="hint">
                      检索：
                      {a.search_profile_ids
                        ? searchSelectionNames(a.search_profile_ids, searchProfiles.data ?? [])
                        : '继承全局默认检索档案'}
                    </p>
                  )}
                  <div className="role-card-foot catalog-card-foot">
                    <button
                      className="btn btn-ghost btn-sm"
                      onClick={() =>
                        agentM.update.mutate({ id: a.id, body: { enabled: !a.enabled } })
                      }
                    >
                      <AppIcon name={a.enabled ? 'eye-off' : 'eye'} size={13} aria-hidden="true" />
                      {a.enabled ? '停用' : '启用'}
                    </button>
                    <button className="btn btn-ghost btn-sm" onClick={() => openAgentEditor(a)}>
                      <AppIcon name="edit" size={13} aria-hidden="true" /> 编辑
                    </button>
                    <button
                      className="btn btn-ghost btn-sm danger"
                      aria-label={`删除 ${a.display_name || a.name}`}
                      onClick={() => {
                        if (confirm(`删除角色「${a.display_name || a.name}」？`)) {
                          agentM.remove.mutate(a.id)
                        }
                      }}
                    >
                      <AppIcon name="trash" size={13} aria-hidden="true" /> 删除
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>
      )}

      {/* ── 模型档案 ── */}
      {tab === 'models' && (
        <section
          className="panel catalog-panel"
          role="tabpanel"
          id={`${tabsId}-models-panel`}
          aria-labelledby={`${tabsId}-models`}
          tabIndex={0}
        >
          <div className="panel-header">
            <div>
              <h2 className="panel-title">模型档案</h2>
              <p className="hint">
                每个档案是一套独立的端点、密钥与模型；标为「全局默认」的档案在角色未绑定时生效。
              </p>
            </div>
            <button className="btn btn-primary" onClick={() => openModelEditor(null)}>
              <AppIcon name="plus" size={15} aria-hidden="true" />
              新建档案
            </button>
          </div>
          <div className="catalog-panel-body">
            {models.isLoading && <Skeleton rows={3} />}
            {models.isError && (
              <p className="error-text" role="alert">
                模型档案加载失败：{errMsg(models.error)}
              </p>
            )}
            {modelM.remove.isError && (
              <p className="error-text" role="alert">
                {errMsg(modelM.remove.error)}
              </p>
            )}
            {!models.isLoading && !models.isError && models.data?.length === 0 && (
              <EmptyState
                icon="server"
                title="连接适合你的研究模型"
                description="保存模型与连接信息，设为全局默认，或为不同的研究角色单独指定。"
              >
                <button className="btn btn-secondary" onClick={() => openModelEditor(null)}>
                  创建模型档案
                </button>
              </EmptyState>
            )}
            <div className="card-grid">
              {models.data?.map((p) => (
                <ModelProfileCard
                  key={p.id}
                  profile={p}
                  onEdit={() => openModelEditor(p)}
                  onDelete={() => {
                    if (confirm(`删除模型档案「${p.name}」？绑定它的角色将回退默认档案。`)) {
                      modelM.remove.mutate(p.id)
                    }
                  }}
                />
              ))}
            </div>
          </div>
        </section>
      )}

      {/* ── 搜索 key 池 ── */}
      {tab === 'keys' && (
        <section
          className="panel catalog-panel"
          role="tabpanel"
          id={`${tabsId}-keys-panel`}
          aria-labelledby={`${tabsId}-keys`}
          tabIndex={0}
        >
          <div className="panel-header">
            <div>
              <h2 className="panel-title">检索服务与 Key 池</h2>
              <p className="hint">
                按来源分别管理 API Key；同一来源按优先级主备切换，多来源并发检索后合并去重。
              </p>
            </div>
            <span className="badge info">主备故障转移</span>
          </div>
          <div className="catalog-panel-body">
            <SearchProfilesManager
              keys={keys.data ?? []}
              references={resourceImpact.data?.profiles}
            />
            <div className="catalog-section-head">
              <h3 className="builtin-rail-title">API Key 池</h3>
            </div>
            <p className="hint">
              内置档案使用同渠道的全部启用 Key；自定义档案仅使用明确绑定的
              Key。池中全部凭据不可用时会报告错误。
            </p>
            {(keyM.create.isError || keyM.update.isError || keyM.remove.isError) && (
              <p className="error-text" role="alert">
                {errMsg(keyM.create.error || keyM.update.error || keyM.remove.error)}
              </p>
            )}
            {keys.isLoading && <Skeleton rows={2} />}
            {keys.isError && (
              <p className="error-text" role="alert">
                检索密钥加载失败：{errMsg(keys.error)}
              </p>
            )}
            {keys.data && keys.data.length === 0 && (
              <p className="hint catalog-empty-line">
                还没有检索 Key。下方可选择 Tavily、Brave、Serper 或 Grok；OpenAlex 与 arXiv 无需
                Key。
              </p>
            )}
            <div className="card-grid">
              {keys.data?.map((k) => (
                <SearchKeyCard
                  key={k.id}
                  k={k}
                  references={resourceImpact.data?.keys[k.id]}
                  onToggle={() => keyM.update.mutate({ id: k.id, body: { enabled: !k.enabled } })}
                  onDelete={() => keyM.remove.mutate(k.id)}
                  onUpdate={(body) => keyM.update.mutateAsync({ id: k.id, body })}
                />
              ))}
            </div>

            <div className="key-create-grid" aria-label="添加检索 Key">
              <label className="field-label">
                来源
                <select
                  className="input"
                  value={newKey.provider}
                  onChange={(e) =>
                    setNewKey({ ...newKey, provider: e.target.value as SearchKeyInput['provider'] })
                  }
                >
                  {Object.entries(SEARCH_PROVIDERS)
                    .filter(([value]) => !['openalex', 'arxiv'].includes(value))
                    .map(([value, name]) => (
                      <option key={value} value={value}>
                        {name}
                      </option>
                    ))}
                </select>
              </label>
              <label className="field-label">
                备注
                <input
                  className="input"
                  placeholder="例如：主账号"
                  value={newKey.label}
                  onChange={(e) => setNewKey({ ...newKey, label: e.target.value })}
                />
              </label>
              <label className="field-label">
                {(newKey.provider ?? 'tavily').toUpperCase()} API Key
                <input
                  className="input"
                  type="password"
                  name="tavily-key-new"
                  autoComplete="new-password"
                  placeholder="粘贴新的检索 Key"
                  value={newKey.api_key}
                  onChange={(e) => setNewKey({ ...newKey, api_key: e.target.value })}
                />
              </label>
              <label className="field-label">
                优先级
                <input
                  className="input"
                  type="number"
                  min={0}
                  value={newKey.priority}
                  onChange={(e) => setNewKey({ ...newKey, priority: Number(e.target.value) || 0 })}
                />
              </label>
              <button
                className="btn btn-primary key-create-button"
                onClick={addKey}
                disabled={keyM.create.isPending}
              >
                <AppIcon
                  name={keyM.create.isPending ? 'loader' : 'plus'}
                  size={15}
                  aria-hidden="true"
                  className={keyM.create.isPending ? 'spin' : ''}
                />
                添加 Key
              </button>
            </div>
          </div>
        </section>
      )}

      {agentSession !== undefined && (
        <AgentCardEditor
          key={agentSession.key}
          initial={agentSession.item}
          profiles={profiles}
          searchProfiles={searchProfiles.data ?? []}
          onSubmit={saveAgent}
          onCancel={() => setAgentSession(undefined)}
          pending={savingSessionKey === agentSession.key}
          error={saveErrors[agentSession.key]}
        />
      )}
      {modelSession !== undefined && (
        <ModelProfileEditor
          key={modelSession.key}
          initial={modelSession.item}
          onSubmit={saveModel}
          onCancel={() => setModelSession(undefined)}
          pending={savingSessionKey === modelSession.key}
          error={saveErrors[modelSession.key]}
        />
      )}
    </div>
  )
}
