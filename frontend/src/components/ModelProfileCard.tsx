import { useTestModel } from '../hooks/useCatalog'
import type { ModelProfile } from '../types'
import { AppIcon } from './AppIcon'

interface Props {
  profile: ModelProfile
  onEdit: () => void
  onDelete: () => void
}

/** 模型档案卡片：展示参数 + 编辑/删除/测试连接（结果就地显示在卡片上）。 */
export default function ModelProfileCard({ profile: p, onEdit, onDelete }: Props) {
  const test = useTestModel()
  const r = test.data

  return (
    <div className="role-card">
      <div className="role-card-head">
        <span className="role-icon">
          <AppIcon name="brain" size={20} aria-hidden="true" />
        </span>
        <div className="role-meta">
          <strong>{p.name}</strong>
          <span className="muted small">{p.model}</span>
        </div>
        {p.is_default && (
          <span className="badge" title="角色未绑定档案时使用">
            全局默认
          </span>
        )}
      </div>
      <p className="role-desc">
        {p.base_url || '官方端点'} ·{' '}
        {p.parameter_mode === 'reasoning' ? `推理 ${p.reasoning_effort}` : `温度 ${p.temperature}`}{' '}
        · {p.api_key_set ? `key ${p.api_key_hint}` : '未设 key'}
      </p>

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

      <div className="row between role-card-foot">
        <button
          className="btn ghost small"
          onClick={() => test.mutate(p.id)}
          disabled={test.isPending}
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
          <button className="btn ghost small" onClick={onEdit}>
            <AppIcon name="edit" size={13} aria-hidden="true" />
            编辑
          </button>
          <button className="btn ghost small danger" onClick={onDelete}>
            <AppIcon name="trash" size={13} aria-hidden="true" />
            删除
          </button>
        </div>
      </div>
    </div>
  )
}
