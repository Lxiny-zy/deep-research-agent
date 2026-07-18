import type { ResearchEvent, RunStatus, StepRun, StepRunStatus, WorkflowRun } from '../types'

const TERMINAL_STEP_STATUSES = new Set<StepRunStatus>([
  'succeeded',
  'failed',
  'skipped',
  'cancelled',
])

const ACTIVE_STEP_STATUSES = new Set<StepRunStatus>(['running', 'retrying'])

const VALID_STEP_STATUSES = new Set<StepRunStatus>([
  'pending',
  'ready',
  'running',
  'retrying',
  'succeeded',
  'failed',
  'skipped',
  'cancelled',
])

export interface ResearchProgress {
  percent: number
  completed: number
  total: number
  activeStep: StepRun | null
  currentLabel: string
  terminal: boolean
  estimated: boolean
}

function eventSteps(events: ResearchEvent[]): StepRun[] {
  const order: string[] = []
  const byId = new Map<string, StepRun>()

  for (const event of events) {
    const data = event.data
    if (!data || typeof data.step_run_id !== 'string' || typeof data.status !== 'string') continue
    if (!VALID_STEP_STATUSES.has(data.status as StepRunStatus)) continue

    const id = data.step_run_id
    if (!byId.has(id)) order.push(id)
    byId.set(id, {
      id,
      node_id: String(data.node_id ?? id),
      label: String(data.label ?? 'Agent'),
      kind: String(data.kind ?? 'agent'),
      agent: String(data.agent ?? ''),
      status: data.status as StepRunStatus,
      attempt: Number(data.attempt ?? 0),
      error: typeof data.error === 'string' ? data.error : null,
      started_at: null,
      finished_at: null,
    })
  }

  return order.map((id) => byId.get(id)!).filter(Boolean)
}

export function mergeWorkflowSteps(
  execution: WorkflowRun | null | undefined,
  events: ResearchEvent[],
): StepRun[] {
  const base = execution?.steps ?? []
  const live = eventSteps(events)
  const order = base.map((step) => step.id)
  const merged = new Map(base.map((step) => [step.id, step]))

  for (const step of live) {
    if (!merged.has(step.id)) order.push(step.id)
    merged.set(step.id, { ...merged.get(step.id), ...step })
  }

  return order.map((id) => merged.get(id)!).filter(Boolean)
}

function plannedStepCount(
  execution: WorkflowRun | null | undefined,
  events: ResearchEvent[],
): number {
  let fromEvents = 0
  for (const event of events) {
    const total = event.data?.total_steps
    if (event.data?.event_name === 'workflow.plan' && typeof total === 'number') {
      fromEvents = Math.max(fromEvents, Math.max(0, Math.floor(total)))
    }
  }

  const definition = execution?.definition
  const nodes = Array.isArray(definition?.nodes) ? definition.nodes.length : 0
  const steps = Array.isArray(definition?.steps) ? definition.steps.length : 0
  return Math.max(fromEvents, nodes, steps)
}

function latestActivityLabel(events: ResearchEvent[]): string {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]
    if (event.type === 'token') return '正在撰写研究报告'
    if (event.message && event.data?.event_name !== 'checkpoint.saved') return event.message
  }
  return '正在初始化研究流程'
}

export function deriveResearchProgress({
  execution,
  events,
  runStatus,
}: {
  execution: WorkflowRun | null | undefined
  events: ResearchEvent[]
  runStatus: RunStatus
}): ResearchProgress {
  const steps = mergeWorkflowSteps(execution, events)
  const planned = plannedStepCount(execution, events)
  const total = Math.max(planned, steps.length)
  const completed = steps.filter((step) => TERMINAL_STEP_STATUSES.has(step.status)).length
  const activeStep =
    [...steps].reverse().find((step) => ACTIVE_STEP_STATUSES.has(step.status)) ?? null
  const readyStep = [...steps].reverse().find((step) => step.status === 'ready') ?? null
  const terminal =
    runStatus === 'done' ||
    runStatus === 'error' ||
    execution?.status === 'succeeded' ||
    execution?.status === 'failed' ||
    execution?.status === 'cancelled'

  let percent = 0
  if (runStatus === 'done' || execution?.status === 'succeeded') {
    percent = 100
  } else if (total > 0) {
    const activeWeight = activeStep
      ? activeStep.status === 'retrying'
        ? 0.58
        : 0.42
      : readyStep
        ? 0.12
        : 0
    percent = ((completed + activeWeight) / total) * 100
    percent = Math.min(terminal ? 99 : 96, Math.max(runStatus === 'pending' ? 0 : 2, percent))
  } else if (runStatus === 'running') {
    percent = 3
  }

  const currentLabel =
    runStatus === 'done'
      ? '研究任务已完成'
      : runStatus === 'error'
        ? '研究任务已停止'
        : activeStep?.label || readyStep?.label || latestActivityLabel(events)

  return {
    percent,
    completed,
    total,
    activeStep,
    currentLabel,
    terminal,
    estimated: !terminal && percent < 100,
  }
}
