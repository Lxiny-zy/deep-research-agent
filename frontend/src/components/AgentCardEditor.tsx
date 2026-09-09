import { useState } from 'react'
import { createPortal } from 'react-dom'
import { useDialogFocus } from '../hooks/useDialogFocus'
import { AGENT_ICON_OPTIONS, agentIconName } from '../lib/agentIcons'
import { BEHAVIOR_HINTS, BEHAVIOR_LABELS } from '../lib/behaviors'
import type {
  AgentCard,
  AgentCardInput,
  Behavior,
  ModelProfile,
  SearchProfile,
  PromptPreview,
} from '../types'
import { previewRolePrompt } from '../api/client'
import { searchSelectionNames } from '../lib/searchProfiles'
import { AgentGlyph, AppIcon } from './AppIcon'

const BEHAVIORS: Behavior[] = ['plan', 'research', 'reflect', 'synthesize', 'critique']

interface Props {
  initial?: AgentCard | null
  profiles: ModelProfile[]
  searchProfiles?: SearchProfile[]
  onSubmit: (body: AgentCardInput) => void
  onCancel: () => void
  pending?: boolean
  error?: string
}

/** 角色卡片新建/编辑表单。图标统一保存为 Lucide 语义键，不再接受 emoji。 */
export default function AgentCardEditor({
  initial,
  profiles,
  searchProfiles = [],
  onSubmit,
  onCancel,
  pending,
  error,
}: Props) {
  const dialogRef = useDialogFocus(onCancel)
  const editing = !!initial
  const defaultBehavior = initial?.behavior ?? 'research'
  const [name, setName] = useState(initial?.name ?? '')
  const [displayName, setDisplayName] = useState(initial?.display_name ?? '')
  const [icon, setIcon] = useState(agentIconName(initial?.icon, defaultBehavior))
  const [behavior, setBehavior] = useState<Behavior>(defaultBehavior)
  const [description, setDescription] = useState(initial?.description ?? '')
  const [systemPrompt, setSystemPrompt] = useState(initial?.system_prompt ?? '')
  const [profileId, setProfileId] = useState(initial?.model_profile_id ?? '')
  const [searchIds, setSearchIds] = useState<string[] | null>(initial?.search_profile_ids ?? null)
  const [promptMode, setPromptMode] = useState<'append' | 'replace'>(
    initial ? (initial.prompt_mode ?? 'replace') : 'append',
  )
  const [preview, setPreview] = useState<{ signature: string; data: PromptPreview } | null>(null)
  const [previewError, setPreviewError] = useState('')
  const [previewPending, setPreviewPending] = useState(false)
  const signature = JSON.stringify([behavior, systemPrompt, promptMode])
  async function showPreview() {
    setPreviewPending(true)
    setPreviewError('')
    try {
      const data = await previewRolePrompt(behavior, systemPrompt, promptMode)
      setPreview({ signature, data })
    } catch (error) {
      setPreviewError(error instanceof Error ? error.message : '无法加载提示词预览')
    } finally {
      setPreviewPending(false)
    }
  }

  function submit() {
    const body: AgentCardInput = {
      display_name: displayName.trim(),
      icon,
      behavior,
      description: description.trim(),
      system_prompt: systemPrompt,
      prompt_mode: promptMode,
      search_profile_ids: behavior === 'research' ? searchIds : null,
      model_profile_id: profileId || null,
    }
    if (!editing) body.name = name.trim()
    onSubmit(body)
  }

  return createPortal(
    <div className="modal-backdrop" onClick={onCancel}>
      <section
        ref={dialogRef}
        className="modal editor-modal agent-editor-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="agent-editor-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header-row">
          <div>
            <span className="eyebrow">角色 / 配置</span>
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

          {behavior === 'research' && (
            <fieldset className="stack search-role-binding">
              <legend>检索服务</legend>
              <label className="search-backend-option">
                <input
                  type="checkbox"
                  checked={searchIds === null}
                  onChange={(e) => setSearchIds(e.target.checked ? null : [])}
                />
                继承全局默认检索档案
              </label>
              {searchIds !== null && (
                <div className="search-backend-options">
                  {searchProfiles.map((profile) => (
                    <label key={profile.id} className="search-backend-option">
                      <input
                        type="checkbox"
                        disabled={!profile.enabled}
                        checked={searchIds.includes(profile.id)}
                        onChange={(e) =>
                          setSearchIds(
                            e.target.checked
                              ? [...searchIds, profile.id]
                              : searchIds.filter((id) => id !== profile.id),
                          )
                        }
                      />
                      {profile.name}
                      {!profile.enabled ? '（已停用）' : ''}
                    </label>
                  ))}
                </div>
              )}
              <p className="hint">
                {searchIds === null
                  ? '使用设置页选中的检索档案。角色模型负责理解和抽取证据。'
                  : searchIds.length
                    ? `专属检索：${searchSelectionNames(searchIds, searchProfiles)}`
                    : '请选择至少一个档案。可在角色广场的检索资源中创建。'}
              </p>
              <p className="hint">
                使用外接联网模型时，请先将它配置为检索档案。仅绑定上面的模型档案不会启用联网。
              </p>
            </fieldset>
          )}

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
            提示词模式
            <select
              className="input"
              value={promptMode}
              onChange={(e) => setPromptMode(e.target.value as 'append' | 'replace')}
            >
              <option value="append">补充内置提示词（推荐）</option>
              <option value="replace">替换角色提示词（高级）</option>
            </select>
          </label>
          <label className="field-label">
            角色指令（留空使用内置默认）
            <textarea
              className="input"
              rows={5}
              maxLength={8000}
              value={systemPrompt}
              onChange={(event) => setSystemPrompt(event.target.value)}
              placeholder="自定义这个角色的系统提示词…"
            />
          </label>
          <p className="hint">
            {systemPrompt.length}/8000 字符。
            {promptMode === 'replace'
              ? '替换角色默认描述；固定行为契约和输出格式仍然有效。'
              : '保留内置职责，在其后加入你的领域、关注点和写作要求。'}
          </p>
          <button
            type="button"
            className="btn ghost"
            onClick={showPreview}
            disabled={previewPending}
          >
            {previewPending ? '加载中…' : '预览最终提示词'}
          </button>
          {previewError && (
            <p role="alert" className="error-text">
              {previewError}
            </p>
          )}
          {preview?.signature === signature && (
            <div className="prompt-preview">
              <p className="hint">
                以下是角色的基础 system
                prompt。运行时还会提供当前问题、来源资料，以及工作流适用的任务上下文。
              </p>
              {(
                [
                  ['default_prompt', '内置角色模板'],
                  ['contract', '固定行为契约'],
                  ['global_rules', '全局规则'],
                  ['effective_system_prompt', '最终 System Prompt（含输出格式）'],
                ] as const
              ).map(([key, title]) => (
                <details key={key}>
                  <summary>{title}</summary>
                  <pre>{preview.data[key]}</pre>
                </details>
              ))}
            </div>
          )}

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
              disabled={
                pending ||
                (!editing && !name.trim()) ||
                (behavior === 'research' && searchIds !== null && searchIds.length === 0)
              }
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
      </section>
    </div>,
    document.body,
  )
}
