import { useState } from 'react'
import AgentCardEditor from '../components/AgentCardEditor'
import ModelProfileCard from '../components/ModelProfileCard'
import ModelProfileEditor from '../components/ModelProfileEditor'
import SearchKeyCard from '../components/SearchKeyCard'
import Skeleton from '../components/Skeleton'
import { AgentGlyph, AppIcon, type AppIconName } from '../components/AppIcon'
import {
  useAgentMutations,
  useAgents,
  useModelMutations,
  useModels,
  useSearchKeyMutations,
  useSearchKeys,
} from '../hooks/useCatalog'
import { behaviorLabel } from '../lib/behaviors'
import type { AgentCard, AgentCardInput, ModelProfile, ModelProfileInput } from '../types'

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : '操作失败'
}

type Tab = 'agents' | 'models' | 'keys'

const TABS: { key: Tab; label: string; icon: AppIconName }[] = [
  { key: 'agents', label: '角色', icon: 'users' },
  { key: 'models', label: '模型档案', icon: 'server' },
  { key: 'keys', label: '检索 Key', icon: 'key' },
]

export default function AgentSquarePage() {
  const agents = useAgents()
  const models = useModels()
  const keys = useSearchKeys()
  const agentM = useAgentMutations()
  const modelM = useModelMutations()
  const keyM = useSearchKeyMutations()

  const [tab, setTab] = useState<Tab>('agents')
  const [editAgent, setEditAgent] = useState<AgentCard | null | undefined>(undefined) // undefined=关闭
  const [editModel, setEditModel] = useState<ModelProfile | null | undefined>(undefined)
  const [newKey, setNewKey] = useState({ label: '', api_key: '', priority: 0 })

  const profiles = models.data ?? []

  function saveAgent(body: AgentCardInput) {
    const onDone = { onSuccess: () => setEditAgent(undefined) }
    if (editAgent) agentM.update.mutate({ id: editAgent.id, body }, onDone)
    else agentM.create.mutate(body, onDone)
  }

  function saveModel(body: ModelProfileInput) {
    const onDone = { onSuccess: () => setEditModel(undefined) }
    if (editModel) modelM.update.mutate({ id: editModel.id, body }, onDone)
    else modelM.create.mutate(body, onDone)
  }

  function addKey() {
    if (!newKey.api_key.trim()) return
    keyM.create.mutate(
      { label: newKey.label.trim(), api_key: newKey.api_key.trim(), priority: newKey.priority },
      { onSuccess: () => setNewKey({ label: '', api_key: '', priority: 0 }) },
    )
  }

  return (
    <div className="stack page-stack">
      <header className="page-intro agents-intro">
        <div>
          <span className="eyebrow"><AppIcon name="users" size={14} aria-hidden="true" /> PERSONAS / CAPABILITY LAYER</span>
          <h1>把专业角色，<em>装配成研究团队。</em></h1>
          <p>管理 Agent 行为模板、模型档案与检索 Key 池，让每条工作流都能调用清晰、稳定、可复用的能力单元。</p>
        </div>
        <div className="page-intro-mark" aria-hidden="true"><AppIcon name="brain" size={54} strokeWidth={1.2} /></div>
      </header>

      <div className="tabs agent-tabs" role="tablist" aria-label="角色广场分类">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            className={`tab${tab === t.key ? ' active' : ''}`}
            onClick={() => setTab(t.key)}
          >
            <AppIcon name={t.icon} size={15} aria-hidden="true" />
            {t.label}
          </button>
        ))}
      </div>

      {/* ── 角色 ── */}
      {tab === 'agents' && (
        <section className="panel catalog-panel">
          <div className="panel-header">
            <div>
              <span className="panel-kicker">AGENTS / 01</span>
              <h2 className="panel-title">角色</h2>
            </div>
            <button className="btn btn-primary" onClick={() => setEditAgent(null)}>
              <AppIcon name="plus" size={15} aria-hidden="true" />
              新建角色
            </button>
          </div>
          <div className="panel-body">
            <p className="hint catalog-description">
              数据驱动的角色：选一种行为模板，自定义提示词与绑定模型。启用的角色可被工作流按标识引用。
            </p>

            {agents.isLoading && <Skeleton rows={4} />}
            {agents.isError && <p className="error-text"><AppIcon name="circle-x" size={14} aria-hidden="true" />{errMsg(agents.error)}</p>}
            {agents.data && agents.data.length === 0 && (
              <p className="muted">还没有自定义角色。新建一个，或继续使用内置角色（planner / researcher 等）。</p>
            )}

            <div className="card-grid">
              {agents.data?.map((a) => (
                <div key={a.id} className={`role-card${a.enabled ? '' : ' disabled'}`}>
                  <div className="role-card-head">
                    <span className="role-icon"><AgentGlyph icon={a.icon} behavior={a.behavior} size={20} /></span>
                    <div className="role-meta">
                      <strong>{a.display_name || a.name}</strong>
                      <span className="muted small">{a.name}</span>
                    </div>
                    <span className="badge">{behaviorLabel(a.behavior)}</span>
                  </div>
                  {a.description && <p className="role-desc">{a.description}</p>}
                  <div className="row between role-card-foot">
                    <span className="muted small">模型：{a.model_profile_name ?? '默认档案'}</span>
                    <div className="row gap-sm">
                      <button
                        className="btn ghost small"
                        onClick={() => agentM.update.mutate({ id: a.id, body: { enabled: !a.enabled } })}
                      >
                        <AppIcon name={a.enabled ? 'eye-off' : 'eye'} size={13} aria-hidden="true" />
                        {a.enabled ? '停用' : '启用'}
                      </button>
                      <button className="btn ghost small" onClick={() => setEditAgent(a)}>
                        <AppIcon name="edit" size={13} aria-hidden="true" /> 编辑
                      </button>
                      <button
                        className="btn ghost small danger"
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
                </div>
              ))}
            </div>
          </div>
        </section>
      )}

      {/* ── 模型档案 ── */}
      {tab === 'models' && (
        <section className="panel catalog-panel">
          <div className="panel-header">
            <div>
              <span className="panel-kicker">MODELS / 02</span>
              <h2 className="panel-title">模型档案</h2>
            </div>
            <button className="btn btn-primary" onClick={() => setEditModel(null)}>
              <AppIcon name="plus" size={15} aria-hidden="true" />
              新建档案
            </button>
          </div>
          <div className="panel-body">
            <p className="hint catalog-description">
              每个档案是一套独立的 base_url / key / 模型，可被不同角色绑定。标为「全局默认」的档案在角色未绑定时生效。
            </p>
            {models.isLoading && <Skeleton rows={3} />}
            {models.data && models.data.length === 0 && (
              <p className="muted">还没有模型档案。新建一个并设为「全局默认」，即可替代环境变量兜底。</p>
            )}
            <div className="card-grid">
              {models.data?.map((p) => (
                <ModelProfileCard
                  key={p.id}
                  profile={p}
                  onEdit={() => setEditModel(p)}
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
        <section className="panel catalog-panel">
          <div className="panel-header">
            <div>
              <span className="panel-kicker">SEARCH ACCESS / 03</span>
              <h2 className="panel-title">搜索 Key 池</h2>
            </div>
            <span className="badge info">主备故障转移</span>
          </div>
          <div className="panel-body">
            <p className="hint catalog-description">
              按优先级从小到大使用；当前 Key 配额耗尽或限流时自动切换到下一个，全部耗尽才报错。
            </p>
            {keys.isLoading && <Skeleton rows={2} />}
            {keys.data && keys.data.length === 0 && (
              <p className="muted">还没有检索 Key。在下方添加一个，即可替代环境变量里的单个 Tavily Key。</p>
            )}
            <div className="card-grid">
              {keys.data?.map((k) => (
                <SearchKeyCard
                  key={k.id}
                  k={k}
                  onToggle={() => keyM.update.mutate({ id: k.id, body: { enabled: !k.enabled } })}
                  onDelete={() => keyM.remove.mutate(k.id)}
                />
              ))}
            </div>

            <div className="key-create-grid">
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
                Tavily API Key
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
              <button className="btn btn-primary key-create-button" onClick={addKey} disabled={keyM.create.isPending}>
                <AppIcon name={keyM.create.isPending ? 'loader' : 'plus'} size={15} aria-hidden="true" className={keyM.create.isPending ? 'spin' : ''} />
                添加 Key
              </button>
            </div>
          </div>
        </section>
      )}

      {editAgent !== undefined && (
        <AgentCardEditor
          initial={editAgent}
          profiles={profiles}
          onSubmit={saveAgent}
          onCancel={() => setEditAgent(undefined)}
          pending={agentM.create.isPending || agentM.update.isPending}
          error={
            agentM.create.isError
              ? errMsg(agentM.create.error)
              : agentM.update.isError
                ? errMsg(agentM.update.error)
                : undefined
          }
        />
      )}
      {editModel !== undefined && (
        <ModelProfileEditor
          initial={editModel}
          onSubmit={saveModel}
          onCancel={() => setEditModel(undefined)}
          pending={modelM.create.isPending || modelM.update.isPending}
          error={
            modelM.create.isError
              ? errMsg(modelM.create.error)
              : modelM.update.isError
                ? errMsg(modelM.update.error)
                : undefined
          }
        />
      )}
    </div>
  )
}
