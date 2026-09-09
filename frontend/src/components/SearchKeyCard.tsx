import { useTestSearchKey } from '../hooks/useCatalog'
import { useState } from 'react'
import type { SearchKey, SearchKeyInput } from '../types'
import { AppIcon } from './AppIcon'

interface Props {
  references?: string[]
  k: SearchKey
  onToggle: () => void
  onDelete: () => void
  onUpdate?: (body: SearchKeyInput) => Promise<unknown>
}

/** 搜索 key 卡片：展示优先级/启用态 + 启停/删除/测试连接。 */
export default function SearchKeyCard({ k, onToggle, onDelete, onUpdate, references = [] }: Props) {
  const test = useTestSearchKey()
  const r = test.data
  const [editing, setEditing] = useState(false)
  const [label, setLabel] = useState(k.label)
  const [priority, setPriority] = useState(k.priority)
  const [secret, setSecret] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  async function save() {
    if (!onUpdate) return
    setSaving(true)
    setError('')
    try {
      await onUpdate({ label, priority, ...(secret.trim() ? { api_key: secret.trim() } : {}) })
      setSecret('')
      setEditing(false)
    } catch (error) {
      setError(error instanceof Error ? error.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className={`role-card${k.enabled ? '' : ' disabled'}`}>
      <div className="role-card-head">
        <span className="role-icon">
          <AppIcon name="key" size={20} aria-hidden="true" />
        </span>
        <div className="role-meta">
          <strong>{k.label || '(无备注)'}</strong>
          <span className="muted small">
            {(k.provider ?? 'tavily').toUpperCase()} · key {k.api_key_hint}
          </span>
        </div>
        <span className="badge" title="越小越先用">
          优先级 {k.priority}
        </span>
      </div>

      {references.length > 0 && (
        <p className="hint">
          使用位置：{references.join('、')}。停用后这些配置将使用其他可用
          Key；没有备用时检索会失败。
        </p>
      )}
      {(test.isPending || r || test.isError) && (
        <p
          className={
            test.isPending
              ? 'test-result test-pending'
              : r?.ok
                ? 'test-result test-ok'
                : 'test-result test-fail'
          }
        >
          {test.isPending
            ? '测试中…'
            : r?.ok
              ? `可用 · ${r.latency_ms}ms`
              : r?.detail || '请求失败'}
        </p>
      )}

      {editing && (
        <div className="stack">
          <label className="field-label">
            Key 备注
            <input className="input" value={label} onChange={(e) => setLabel(e.target.value)} />
          </label>
          <label className="field-label">
            Key 优先级
            <input
              className="input"
              type="number"
              min={0}
              max={1000}
              value={priority}
              onChange={(e) => setPriority(Number(e.target.value))}
            />
          </label>
          <label className="field-label">
            更换 Key
            <input
              className="input"
              type="password"
              autoComplete="new-password"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              placeholder="留空保留原密钥"
            />
          </label>
          {error && (
            <p role="alert" className="error-text">
              {error}
            </p>
          )}
          <div className="row gap-sm">
            <button
              type="button"
              className="btn btn-primary small"
              disabled={saving}
              onClick={save}
            >
              保存 Key
            </button>
            <button
              type="button"
              className="btn ghost small"
              disabled={saving}
              onClick={() => {
                setSecret('')
                setEditing(false)
              }}
            >
              取消
            </button>
          </div>
        </div>
      )}
      <div className="row between role-card-foot">
        <button
          className="btn ghost small"
          onClick={() => test.mutate(k.id)}
          disabled={test.isPending || ['responses', 'chat_search'].includes(k.provider)}
          title={
            ['responses', 'chat_search'].includes(k.provider)
              ? '请在绑定的检索档案上测试端点、模型与 Key'
              : undefined
          }
        >
          <AppIcon
            name={test.isPending ? 'loader' : 'activity'}
            size={13}
            aria-hidden="true"
            className={test.isPending ? 'spin' : ''}
          />
          测试连接
        </button>
        <div className="row gap-sm">
          {onUpdate && (
            <button
              className="btn ghost small"
              type="button"
              onClick={() => {
                setLabel(k.label)
                setPriority(k.priority)
                setEditing(!editing)
              }}
            >
              编辑
            </button>
          )}
          <button className="btn ghost small" onClick={onToggle}>
            <AppIcon name={k.enabled ? 'eye-off' : 'eye'} size={13} aria-hidden="true" />
            {k.enabled ? '停用' : '启用'}
          </button>
          <button className="btn ghost small danger" onClick={onDelete}>
            <AppIcon name="trash" size={13} aria-hidden="true" /> 删除
          </button>
        </div>
      </div>
    </div>
  )
}
