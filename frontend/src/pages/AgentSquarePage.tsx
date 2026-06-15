import { useState } from 'react'
import AgentCardEditor from '../components/AgentCardEditor'
import ModelProfileEditor from '../components/ModelProfileEditor'
import Skeleton from '../components/Skeleton'
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

export default function AgentSquarePage() {
  const agents = useAgents()
  const models = useModels()
  const keys = useSearchKeys()
  const agentM = useAgentMutations()
  const modelM = useModelMutations()
  const keyM = useSearchKeyMutations()

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
    <div className="stack">
      {/* ── 角色卡片网格 ── */}
      <div className="panel">
        <div className="row between">
          <h3 className="panel-title">角色广场</h3>
          <button className="btn" onClick={() => setEditAgent(null)}>
            + 新建角色
          </button>
        </div>
        <p className="hint" style={{ marginBottom: 14 }}>
          数据驱动的角色：选一种行为模板，自定义提示词与绑定模型。启用的角色可被工作流按标识引用。
        </p>

        {agents.isLoading && <Skeleton rows={4} />}
        {agents.isError && <p className="error-text">✗ {errMsg(agents.error)}</p>}
        {agents.data && agents.data.length === 0 && (
          <p className="muted">还没有自定义角色。新建一个，或继续使用内置角色（planner / researcher 等）。</p>
        )}

        <div className="card-grid">
          {agents.data?.map((a) => (
            <div key={a.id} className={`role-card${a.enabled ? '' : ' disabled'}`}>
              <div className="role-card-head">
                <span className="role-icon">{a.icon}</span>
                <div className="role-meta">
                  <strong>{a.display_name || a.name}</strong>
                  <span className="muted small">{a.name}</span>
                </div>
                <span className="badge">{behaviorLabel(a.behavior)}</span>
              </div>
              {a.description && <p className="role-desc">{a.description}</p>}
              <div className="row between role-card-foot">
                <span className="muted small">
                  模型：{a.model_profile_name ?? '默认档案'}
                </span>
                <div className="row gap-sm">
                  <button
                    className="btn ghost small"
                    onClick={() =>
                      agentM.update.mutate({ id: a.id, body: { enabled: !a.enabled } })
                    }
                  >
                    {a.enabled ? '停用' : '启用'}
                  </button>
                  <button className="btn ghost small" onClick={() => setEditAgent(a)}>
                    编辑
                  </button>
                  <button
                    className="btn ghost small danger"
                    onClick={() => {
                      if (confirm(`删除角色「${a.display_name || a.name}」？`)) {
                        agentM.remove.mutate(a.id)
                      }
                    }}
                  >
                    删除
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ── 模型档案 ── */}
      <div className="panel">
        <div className="row between">
          <h3 className="panel-title">模型档案</h3>
          <button className="btn" onClick={() => setEditModel(null)}>
            + 新建档案
          </button>
        </div>
        <p className="hint" style={{ marginBottom: 14 }}>
          每个档案是一套独立的 base_url / key / 模型，可被不同角色绑定，完成不同成本与能力的任务。
        </p>
        {models.isLoading && <Skeleton rows={3} />}
        {models.data?.map((p) => (
          <div key={p.id} className="row between list-row">
            <div>
              <strong>{p.name}</strong>
              {p.is_default && <span className="badge" style={{ marginLeft: 8 }}>默认</span>}
              <div className="muted small">
                {p.model} · {p.base_url || '官方端点'} · 温度 {p.temperature} ·{' '}
                {p.api_key_set ? `key ${p.api_key_hint}` : '未设 key'}
              </div>
            </div>
            <div className="row gap-sm">
              <button className="btn ghost small" onClick={() => setEditModel(p)}>
                编辑
              </button>
              <button
                className="btn ghost small danger"
                onClick={() => {
                  if (confirm(`删除模型档案「${p.name}」？绑定它的角色将回退默认档案。`)) {
                    modelM.remove.mutate(p.id)
                  }
                }}
              >
                删除
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* ── 搜索 key 池 ── */}
      <div className="panel">
        <h3 className="panel-title">搜索 Key 池（主备故障转移）</h3>
        <p className="hint" style={{ marginBottom: 14 }}>
          按优先级从小到大使用；当前 key 配额耗尽或限流时自动切换到下一个，全部耗尽才报错。
        </p>
        {keys.isLoading && <Skeleton rows={2} />}
        {keys.data?.map((k) => (
          <div key={k.id} className={`row between list-row${k.enabled ? '' : ' disabled'}`}>
            <div>
              <strong>{k.label || '(无备注)'}</strong>
              <span className="badge" style={{ marginLeft: 8 }}>
                优先级 {k.priority}
              </span>
              <div className="muted small">key {k.api_key_hint}</div>
            </div>
            <div className="row gap-sm">
              <button
                className="btn ghost small"
                onClick={() => keyM.update.mutate({ id: k.id, body: { enabled: !k.enabled } })}
              >
                {k.enabled ? '停用' : '启用'}
              </button>
              <button
                className="btn ghost small danger"
                onClick={() => keyM.remove.mutate(k.id)}
              >
                删除
              </button>
            </div>
          </div>
        ))}

        <div className="row gap" style={{ marginTop: 12 }}>
          <input
            className="input"
            style={{ width: 140 }}
            placeholder="备注（如 主账号）"
            value={newKey.label}
            onChange={(e) => setNewKey({ ...newKey, label: e.target.value })}
          />
          <input
            className="input"
            style={{ flex: 1 }}
            type="password"
            placeholder="Tavily API Key"
            value={newKey.api_key}
            onChange={(e) => setNewKey({ ...newKey, api_key: e.target.value })}
          />
          <input
            className="input"
            style={{ width: 90 }}
            type="number"
            min={0}
            placeholder="优先级"
            value={newKey.priority}
            onChange={(e) => setNewKey({ ...newKey, priority: Number(e.target.value) || 0 })}
          />
          <button className="btn" onClick={addKey} disabled={keyM.create.isPending}>
            添加
          </button>
        </div>
      </div>

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
