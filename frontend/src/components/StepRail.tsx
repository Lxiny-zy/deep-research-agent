import { AppIcon, type AppIconName } from './AppIcon'
import { stepLabel } from '../lib/workbench'
import type { RunWorkspace, StepRunStatus } from '../types'

const STATUS: Record<StepRunStatus, { label: string; icon: AppIconName }> = {
  pending: { label: '等待', icon: 'circle-dashed' },
  ready: { label: '就绪', icon: 'circle-dashed' },
  running: { label: '运行中', icon: 'loader' },
  retrying: { label: '重试中', icon: 'refresh' },
  succeeded: { label: '完成', icon: 'check-circle' },
  failed: { label: '失败', icon: 'circle-x' },
  skipped: { label: '跳过', icon: 'circle-dot-dashed' },
  cancelled: { label: '已取消', icon: 'stop' },
}

function duration(seconds: number | null): string {
  if (seconds == null) return ''
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  return `${Math.floor(seconds / 60)}m${Math.round(seconds % 60)}s`
}

/**
 * 左栏：运行步骤轨道。每步显示状态、耗时与重试；重规划插入的补救紧跟在
 * 被补救的步骤后面，用户能看到「部分完成 → 补救 → 结果」的完整链条。
 */
export default function StepRail({ workspace }: { workspace: RunWorkspace | undefined }) {
  if (!workspace || workspace.steps.length === 0) {
    return (
      <section className="panel step-rail" aria-label="运行步骤">
        <h2 className="panel-title">运行步骤</h2>
        <p className="hint">流程启动后这里会列出每个步骤。</p>
      </section>
    )
  }
  return (
    <section className="panel step-rail" aria-label="运行步骤">
      <div className="step-rail-head">
        <h2 className="panel-title">运行步骤</h2>
        {workspace.attempt && workspace.attempt > 1 && (
          <span className="format-chip">第 {workspace.attempt} 次尝试</span>
        )}
      </div>
      <ol className="step-list">
        {workspace.steps.map((step) => {
          const meta = STATUS[step.status] ?? STATUS.pending
          const rescues = workspace.replans.filter(
            (item) => item.target === step.node_id.replace(/^node-/, ''),
          )
          return (
            <li key={step.node_id} className={`step-item is-${step.status}`}>
              <AppIcon
                name={meta.icon}
                size={15}
                className={step.status === 'running' ? 'spin' : ''}
                aria-hidden="true"
              />
              <div className="step-body">
                <span className="step-title">
                  {step.index + 1}. {stepLabel(step.label, step.agent)}
                </span>
                <span className="hint">
                  {meta.label}
                  {step.elapsed != null && ` · ${duration(step.elapsed)}`}
                  {step.attempt > 1 && ` · 第 ${step.attempt} 次`}
                </span>
                {step.error && <span className="step-error">{step.error.slice(0, 160)}</span>}
                {rescues.map((rescue) => (
                  <span key={rescue.id} className="step-rescue">
                    <AppIcon name="route" size={12} aria-hidden="true" />
                    {rescue.action === 'rescue'
                      ? `重规划补救「${rescue.name}」：${rescue.result}`
                      : `重规划接受现状：${rescue.reason || '缺口不影响交付'}`}
                  </span>
                ))}
              </div>
            </li>
          )
        })}
      </ol>
    </section>
  )
}
