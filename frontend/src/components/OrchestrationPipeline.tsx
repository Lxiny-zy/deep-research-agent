import { deriveResearchProgress, mergeWorkflowSteps } from '../lib/runProgress'
import type { ResearchEvent, RunStatus, StepRunStatus, WorkflowRun } from '../types'
import { AppIcon } from './AppIcon'

interface Props {
  execution: WorkflowRun | null | undefined
  events?: ResearchEvent[]
  runStatus: RunStatus
}

const STEP_STATUS_LABEL: Record<StepRunStatus, string> = {
  pending: '等待',
  ready: '就绪',
  running: '执行中',
  retrying: '重试中',
  succeeded: '完成',
  failed: '失败',
  skipped: '跳过',
  cancelled: '取消',
}

const RUN_STATUS_LABEL = {
  pending: '排队',
  running: '运行中',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

export default function OrchestrationPipeline({ execution, events = [], runStatus }: Props) {
  const steps = mergeWorkflowSteps(execution, events, runStatus)
  const progress = deriveResearchProgress({ execution, events, runStatus })
  if (!steps.length && progress.total === 0) return null

  const runtimeStatus =
    runStatus === 'running'
      ? 'running'
      : runStatus === 'done'
        ? 'succeeded'
        : runStatus === 'error'
          ? 'failed'
          : execution?.status ?? runStatus

  return (
    <div className="orchestration-runtime">
      <div className="orchestration-runtime-head">
        <div>
          <span className="workflow-kicker">WORKFLOW RUNTIME</span>
          <strong>{execution?.workflow_name ?? '实时编排'}</strong>
          <small className="runtime-current-step">{progress.currentLabel}</small>
        </div>
        <span className={`runtime-status ${runtimeStatus}`}>
          {RUN_STATUS_LABEL[runtimeStatus]}
        </span>
      </div>
      <div className="runtime-header-progress" aria-hidden>
        <span style={{ transform: `scaleX(${progress.percent / 100})` }} />
      </div>
      <div className="runtime-pipeline-scroll">
        <div className="runtime-terminal"><AppIcon name="play" size={13} aria-hidden="true" /> INPUT</div>
        {steps.map((step, index) => (
          <div className="runtime-step-wrap" key={step.id}>
            <span className="runtime-edge" aria-hidden="true"><AppIcon name="arrow-right" size={16} /></span>
            <div className={`runtime-step ${step.status}`} title={step.error ?? undefined}>
              <span>{index + 1}</span>
              <div><strong>{step.label}</strong><small>{step.agent || step.kind}</small></div>
              <i>{STEP_STATUS_LABEL[step.status]}</i>
            </div>
          </div>
        ))}
        <span className="runtime-edge" aria-hidden="true"><AppIcon name="arrow-right" size={16} /></span>
        <div className={`runtime-terminal output${runStatus === 'done' ? ' completed' : ''}`}><AppIcon name="check-circle" size={13} aria-hidden="true" /> OUTPUT</div>
      </div>
    </div>
  )
}
