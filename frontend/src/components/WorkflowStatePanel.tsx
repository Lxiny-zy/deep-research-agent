import type { WorkflowAction, WorkflowPhaseStatus, WorkflowState } from '../types'
import { AppIcon } from './AppIcon'

const STATUS: Record<WorkflowPhaseStatus, string> = {
  pending: '待开始', active: '进行中', done: '已完成', needs_review: '待处理',
  blocked: '已阻断', skipped: '跳过',
}
const ACTION: Record<WorkflowAction, string> = {
  retrieve: '补充检索', read: '补读原文', rewrite: '修订正文', render: '重试格式',
  security: '处理安全问题', review: '继续核验', none: '无需操作',
}

export default function WorkflowStatePanel({
  state,
  onAction,
  busy = false,
}: {
  state?: WorkflowState | null
  onAction?: (action: WorkflowAction) => void
  busy?: boolean
}) {
  if (!state) return null
  return (
    <section className="panel workflow-state-panel" aria-labelledby="workflow-state-title">
      <div className="workflow-state-head">
        <div>
          <span className="panel-kicker">研究流程</span>
          <h2 id="workflow-state-title">{state.current_label}</h2>
        </div>
        <span className="workflow-next-action">{ACTION[state.next_action]}</span>
      </div>
      <div className="workflow-phase-track" role="list" aria-label="研究阶段">
        {state.phases.map((phase) => (
          <div key={phase.id} className={`workflow-phase ${phase.status}`} role="listitem">
            <span className="workflow-phase-dot" aria-hidden="true">
              {phase.status === 'done' ? <AppIcon name="check" size={12} /> : phase.status === 'blocked' ? <AppIcon name="shield" size={12} /> : null}
            </span>
            <div>
              <strong>{phase.label}</strong>
              <small>{STATUS[phase.status]}</small>
            </div>
            {phase.issues.length > 0 && <em>{phase.issues.length}</em>}
          </div>
        ))}
      </div>
      {state.issue_total > 0 && (
        <div className="workflow-state-foot">
          <p className="workflow-state-hint">当前有 {state.issue_total} 项待处理，问题已按补证据、修订正文、格式和安全类型分流。</p>
          {['rewrite', 'retrieve', 'read'].includes(state.next_action) && onAction && (
            <button type="button" className="btn btn-primary btn-sm" onClick={() => onAction(state.next_action)} disabled={busy}>
              <AppIcon name="edit" size={13} aria-hidden="true" />
              {busy ? '正在创建后续任务…' : state.next_action === 'rewrite' ? '继续修订正文' : '继续补充研究'}
            </button>
          )}
        </div>
      )}
    </section>
  )
}
