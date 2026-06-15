import { useState } from 'react'
import { BEHAVIOR_HINTS, BEHAVIOR_LABELS } from '../lib/behaviors'
import type { AgentCard, AgentCardInput, Behavior, ModelProfile } from '../types'

const BEHAVIORS: Behavior[] = ['plan', 'research', 'reflect', 'synthesize', 'critique']

interface Props {
  initial?: AgentCard | null // 传入＝编辑，否则＝新建
  profiles: ModelProfile[]
  onSubmit: (body: AgentCardInput) => void
  onCancel: () => void
  pending?: boolean
  error?: string
}

/** 角色卡片新建/编辑表单（弹窗内容）。name 仅新建时可填（工作流按名引用，编辑不改名）。 */
export default function AgentCardEditor({
  initial,
  profiles,
  onSubmit,
  onCancel,
  pending,
  error,
}: Props) {
  const editing = !!initial
  const [name, setName] = useState(initial?.name ?? '')
  const [displayName, setDisplayName] = useState(initial?.display_name ?? '')
  const [icon, setIcon] = useState(initial?.icon ?? '🧩')
  const [behavior, setBehavior] = useState<Behavior>(initial?.behavior ?? 'research')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [systemPrompt, setSystemPrompt] = useState(initial?.system_prompt ?? '')
  const [profileId, setProfileId] = useState(initial?.model_profile_id ?? '')

  function submit() {
    const body: AgentCardInput = {
      display_name: displayName.trim(),
      icon: icon.trim() || '🧩',
      behavior,
      description: description.trim(),
      system_prompt: systemPrompt,
      model_profile_id: profileId || null,
    }
    if (!editing) body.name = name.trim()
    onSubmit(body)
  }

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3 className="panel-title">{editing ? '编辑角色' : '新建角色'}</h3>

        <div className="stack">
          <div className="row gap">
            <label className="settings-item" style={{ width: 72 }}>
              <span className="muted small">图标</span>
              <input className="input" value={icon} onChange={(e) => setIcon(e.target.value)} maxLength={4} />
            </label>
            <label className="settings-item" style={{ flex: 1 }}>
              <span className="muted small">展示名</span>
              <input
                className="input"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="如 严苛评审员"
              />
            </label>
          </div>

          {!editing && (
            <label className="field-label">
              角色标识（英文，工作流按此引用，创建后不可改）
              <input
                className="input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="如 my-critic"
              />
            </label>
          )}

          <label className="field-label">
            行为模板
            <select
              className="input"
              value={behavior}
              onChange={(e) => setBehavior(e.target.value as Behavior)}
              disabled={editing}
            >
              {BEHAVIORS.map((b) => (
                <option key={b} value={b}>
                  {BEHAVIOR_LABELS[b]}
                </option>
              ))}
            </select>
            <span className="hint">{BEHAVIOR_HINTS[behavior]}</span>
          </label>

          <label className="field-label">
            绑定模型档案（留空＝用全局默认档案）
            <select
              className="input"
              value={profileId}
              onChange={(e) => setProfileId(e.target.value)}
            >
              <option value="">（默认档案）</option>
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} · {p.model}
                  {p.is_default ? '（默认）' : ''}
                </option>
              ))}
            </select>
          </label>

          <label className="field-label">
            描述
            <input
              className="input"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="这个角色做什么"
            />
          </label>

          <label className="field-label">
            System Prompt（留空＝沿用该行为的内置默认提示词）
            <textarea
              className="input"
              rows={5}
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              placeholder="自定义这个角色的系统提示词…"
            />
          </label>

          {error && <p className="error-text">✗ {error}</p>}

          <div className="row between" style={{ marginTop: 8 }}>
            <button className="btn ghost" onClick={onCancel}>
              取消
            </button>
            <button
              className="btn"
              onClick={submit}
              disabled={pending || (!editing && !name.trim())}
            >
              {pending ? '保存中…' : '保存'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
