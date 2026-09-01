import { useCallback, useEffect, useMemo, useState } from 'react'
import { deriveResearchProgress } from '../lib/runProgress'
import type { ResearchEvent, RunStatus, StepRun, StepRunStatus, WorkflowRun } from '../types'

const TOTAL_DURATION = 27
const STEP_DEFS = [
  { id: 'preview-plan', label: '问题规划', agent: 'planner', start: 1, end: 6 },
  { id: 'preview-search', label: '并行检索', agent: 'researcher', start: 6, end: 15 },
  { id: 'preview-reflect', label: '证据反思', agent: 'reflector', start: 15, end: 20 },
  { id: 'preview-report', label: '报告综合', agent: 'synthesizer', start: 20, end: 26 },
]

function stepStatus(elapsed: number, start: number, end: number): StepRunStatus {
  if (elapsed >= end) return 'succeeded'
  if (elapsed >= start) return 'running'
  if (elapsed >= Math.max(0, start - 1.2)) return 'ready'
  return 'pending'
}

function makeStep(definition: (typeof STEP_DEFS)[number], elapsed: number): StepRun {
  const status = stepStatus(elapsed, definition.start, definition.end)
  return {
    id: definition.id,
    node_id: definition.id,
    label: definition.label,
    kind: 'agent',
    agent: definition.agent,
    status,
    attempt: status === 'running' || status === 'succeeded' ? 1 : 0,
    error: null,
    started_at: null,
    finished_at: null,
  }
}

export function telemetryStageMessage(elapsed: number): string {
  if (elapsed < 1) return '正在初始化研究流程'
  if (elapsed < 6) return '正在拆解问题并规划研究路径'
  if (elapsed < 15) return '多个 Researcher 正在并行检索与抽取证据'
  if (elapsed < 20) return '正在核对证据缺口并进行反思补充'
  if (elapsed < 26) return '正在流式撰写带引用的研究报告'
  return '研究任务已完成'
}

export function useLiveTelemetryDemo(autoReplay = false) {
  const [cycle, setCycle] = useState(0)
  const [elapsed, setElapsed] = useState(0)
  const replay = useCallback(() => setCycle((value) => value + 1), [])

  useEffect(() => {
    const startedAt = performance.now()
    let replayTimer = 0
    setElapsed(0)
    const timer = window.setInterval(() => {
      const next = Math.min(TOTAL_DURATION, (performance.now() - startedAt) / 1000)
      setElapsed(next)
      if (next >= TOTAL_DURATION) {
        window.clearInterval(timer)
        if (autoReplay) replayTimer = window.setTimeout(replay, 2800)
      }
    }, 100)
    return () => {
      window.clearInterval(timer)
      window.clearTimeout(replayTimer)
    }
  }, [autoReplay, cycle, replay])

  const done = elapsed >= 26
  const runStatus: RunStatus = done ? 'done' : 'running'
  const steps = useMemo(() => STEP_DEFS.map((step) => makeStep(step, elapsed)), [elapsed])
  const execution: WorkflowRun = {
    id: 'live-preview',
    workflow_name: 'deep-research-preview',
    status: done ? 'succeeded' : 'running',
    input: {},
    output: {},
    definition: { steps: STEP_DEFS.map(() => ({})), nodes: [], edges: [] },
    steps,
    started_at: null,
    finished_at: null,
  }
  const events: ResearchEvent[] = [
    {
      stage: 'ORCHESTRATOR',
      type: 'info',
      message: '研究流程已就绪，共 4 个阶段',
      elapsed: 0,
      data: { event_name: 'workflow.plan', total_steps: STEP_DEFS.length },
    },
    ...steps
      .filter((step) => step.status !== 'pending')
      .map((step) => ({
        stage: step.agent.toUpperCase(),
        type: 'info' as const,
        message:
          step.status === 'succeeded' ? `${step.label}已完成` : telemetryStageMessage(elapsed),
        elapsed,
        data: {
          event_name: `step.${step.status}`,
          step_run_id: step.id,
          node_id: step.node_id,
          label: step.label,
          kind: step.kind,
          agent: step.agent,
          status: step.status,
          attempt: step.attempt,
        },
      })),
  ]

  return {
    elapsed,
    done,
    runStatus,
    execution,
    events,
    progress: deriveResearchProgress({ execution, events, runStatus }),
    tokens: Math.round(Math.min(1, elapsed / 26) * 6384),
    findings: Math.min(12, Math.max(0, Math.floor((elapsed - 5) * 0.72))),
    replay,
  }
}
