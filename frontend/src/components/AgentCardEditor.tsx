import { useState } from 'react'
import { AGENT_ICON_OPTIONS, agentIconName } from '../lib/agentIcons'
import { BEHAVIOR_HINTS, BEHAVIOR_LABELS } from '../lib/behaviors'
import type { AgentCard, AgentCardInput, Behavior, ModelProfile } from '../types'
import { AgentGlyph, AppIcon } from './AppIcon'

const BEHAVIORS: Behavior[] = ['plan', 'research', 'reflect', 'synthesize', 'critique']

interface Props {
  initial?: AgentCard | null
  profiles: ModelProfile[]
  onSubmit: (body: AgentCardInput) => void
  onCancel: () => void
  pending?: boolean
  error?: string
}

/** 角色卡片新建/编辑表单。图标统一保存为 Lucide 语义键，不再接受 emoji。 */
export default function AgentCardEditor({
  initial,
  profiles,
  onSubmit,
  onCancel,
  pending,
  error,
}: Props) {
  const editing = !!initial
  const defaultBehavior = initial?.behavior ?? 'research'
  const [name, setName] = useState(initial?.name ?? '')
  const [displayName, setDisplayName] = useState(initial?.display_name ?? '')
  const [icon, setIcon] = useState(agentIconName(initial?.icon, defaultBehavior))
  const [behavior, setBehavior] = useState<Behavior>(defaultBehavior)
  const [description, setDescription] = useState(initial?.description ?? '')
  const [systemPrompt, setSystemPrompt] = useState(initial?.system_prompt ?? '')
  const [profileId, setProfileId] = useState(initial?.model_profile_id ?? '')

  function submit() {
    const body: AgentCardInput = {
      display_name: displayName.trim(),
      icon,
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
      <div
        className="modal editor-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="agent-editor-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header-row">
          <div>
            <span className="eyebrow">ROLE / CONFIGURATION</span>
            <h3 className="panel-title" id="agent-editor-title">
              {editing ? '编辑角色' : '新建角色'}
            </h3>
          </div>
          <button
            className="btn btn-ghost btn-sm icon-button"
            onClick={onCancel}
            type="button"
            aria-label="关闭"
          >
            <AppIcon name="x" size={15} aria-hidden="true" />
          </button>
        </div>

        <div className="stack editor-stack">
          <div className="agent-identity-row">
            <div className="icon-picker-field">
              <span className="muted small">角色标记</span>
              <div className="icon-picker" role="group" aria-label="选择角色图标">
                {AGENT_ICON_OPTIONS.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    className={`icon-picker-option${icon === option.icon ? ' active' : ''}`}
                    onClick={() => setIcon(option.icon)}
                    aria-label={option.label}
                    aria-pressed={icon === option.icon}
                  >
                    <AppIcon name={option.icon} size={18} aria-hidden="true" />
                  </button>
                ))}
              </div>
            </div>
            <label className="settings-item">
              <span className="muted small">展示名</span>
              <input
                className="input"
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                placeholder="如 严苛评审员"
              />
            </label>
            <span className="agent-preview" aria-hidden="true">
              <AgentGlyph icon={icon} behavior={behavior} size={22} />
            </span>
          </div>

          {!editing && (
            <label className="field-label">
              角色标识（英文，工作流按此引用，创建后不可改）
              <input
                className="input"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="如 my-critic"
              />
            </label>
          )}

          <label className="field-label">
            行为模板
            <select
              className="input"
              value={behavior}
              onChange={(event) => setBehavior(event.target.value as Behavior)}
              disabled={editing}
            >
              {BEHAVIORS.map((item) => (
                <option key={item} value={item}>
                  {BEHAVIOR_LABELS[item]}
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
              onChange={(event) => setProfileId(event.target.value)}
            >
              <option value="">（默认档案）</option>
              {profiles.map((profile) => (
                <option key={profile.id} value={profile.id}>
                  {profile.name} · {profile.model}
                  {profile.is_default ? '（默认）' : ''}
                </option>
              ))}
            </select>
          </label>

          <label className="field-label">
            描述
            <input
              className="input"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="这个角色做什么"
            />
          </label>

          <label className="field-label">
            System Prompt（留空＝沿用该行为的内置默认提示词）
            <textarea
              className="input"
              rows={5}
              value={systemPrompt}
              onChange={(event) => setSystemPrompt(event.target.value)}
              placeholder="自定义这个角色的系统提示词…"
            />
          </label>

          {error && (
            <p className="error-text">
              <AppIcon name="circle-x" size={14} aria-hidden="true" />
              {error}
            </p>
          )}

          <div className="row between editor-actions">
            <button className="btn ghost" onClick={onCancel} type="button">
              取消
            </button>
            <button
              className="btn btn-primary"
              onClick={submit}
              disabled={pending || (!editing && !name.trim())}
              type="button"
            >
              <AppIcon
                name={pending ? 'loader' : 'save'}
                size={15}
                aria-hidden="true"
                className={pending ? 'spin' : ''}
              />
              {pending ? '保存中…' : '保存角色'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
