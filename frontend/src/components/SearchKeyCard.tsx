import { useTestSearchKey } from '../hooks/useCatalog'
import type { SearchKey } from '../types'

interface Props {
  k: SearchKey
  onToggle: () => void
  onDelete: () => void
}

/** 搜索 key 卡片：展示优先级/启用态 + 启停/删除/测试连接。 */
export default function SearchKeyCard({ k, onToggle, onDelete }: Props) {
  const test = useTestSearchKey()
  const r = test.data

  return (
    <div className={`role-card${k.enabled ? '' : ' disabled'}`}>
      <div className="role-card-head">
        <span className="role-icon">🔑</span>
        <div className="role-meta">
          <strong>{k.label || '(无备注)'}</strong>
          <span className="muted small">key {k.api_key_hint}</span>
        </div>
        <span className="badge" title="越小越先用">
          优先级 {k.priority}
        </span>
      </div>

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
              ? `✓ 可用 · ${r.latency_ms}ms`
              : `✗ ${r?.detail || '请求失败'}`}
        </p>
      )}

      <div className="row between role-card-foot">
        <button
          className="btn ghost small"
          onClick={() => test.mutate(k.id)}
          disabled={test.isPending}
        >
          测试连接
        </button>
        <div className="row gap-sm">
          <button className="btn ghost small" onClick={onToggle}>
            {k.enabled ? '停用' : '启用'}
          </button>
          <button className="btn ghost small danger" onClick={onDelete}>
            删除
          </button>
        </div>
      </div>
    </div>
  )
}
