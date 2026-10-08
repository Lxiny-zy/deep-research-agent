import type { ResearchEvent } from '../types'
import { modelCallLabel } from '../lib/modelCalls'

export default function RoleBindingSummary({ events }: { events: ResearchEvent[] }) {
  const bindings = new Map<string, Record<string, unknown>>()
  for (const event of events) {
    const value = event.data?.role_binding
    if (!value || typeof value !== 'object' || !('role' in value) || typeof value.role !== 'string') continue
    bindings.set(value.role, value as Record<string, unknown>)
  }
  if (!bindings.size) return null
  return (
    <details className="model-reasoning-panel">
      <summary className="model-reasoning-heading">本次角色与模型配置 · {bindings.size} 个角色</summary>
      <p className="model-call-note">这里记录本次运行实际解析的配置。修改角色卡片后，新任务使用新配置，当前任务继续使用原配置。</p>
      <ul className="model-call-list">
        {[...bindings].map(([role, binding]) => (
          <li key={role}>
            <strong>{modelCallLabel({ call_id: role, status: 'succeeded', role })}</strong>
            <div className="hint">
              {typeof binding.card === 'string' ? `卡片：${binding.card}` : '内置角色'}
              {' · '}{typeof binding.profile === 'string' ? binding.profile : '运行默认档案'}
              {typeof binding.model === 'string' && ` · ${binding.model}`}
              {' · '}{binding.model_source === 'card' ? '使用卡片绑定模型' : '使用默认模型'}
            </div>
          </li>
        ))}
      </ul>
    </details>
  )
}
