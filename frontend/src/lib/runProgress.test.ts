import type { ResearchEvent, WorkflowRun } from '../types'
import { deriveResearchProgress, mergeWorkflowSteps } from './runProgress'

function event(status: string, id: string, label = id, attempt = 0, nodeId = id): ResearchEvent {
  return {
    stage: 'ORCHESTRATOR',
    type: 'info',
    message: '',
    elapsed: 1,
    data: {
      event_name: `step.${status}`,
      step_run_id: id,
      node_id: nodeId,
      status,
      label,
      attempt,
    },
  }
}

function workflowRun(overrides: Partial<WorkflowRun>): WorkflowRun {
  return {
    id: 'workflow-run',
    workflow_name: 'test',
    status: 'running',
    input: {},
    output: {},
    steps: [],
    started_at: null,
    finished_at: null,
    ...overrides,
  }
}

describe('runProgress', () => {
  it('用实时事件覆盖轮询详情中的滞后步骤状态', () => {
    const execution = {
      status: 'running',
      steps: [
        {
          id: 'a',
          node_id: 'a',
          label: '规划',
          kind: 'agent',
          agent: 'planner',
          status: 'ready',
          attempt: 0,
          error: null,
          started_at: null,
          finished_at: null,
        },
      ],
    } as WorkflowRun
    const steps = mergeWorkflowSteps(execution, [event('running', 'a', '规划')])
    expect(steps[0].status).toBe('running')
  })

  it('keeps a persisted terminal step over an older live event', () => {
    const execution = {
      status: 'running',
      steps: [
        {
          id: 'a',
          node_id: 'a',
          label: '规划',
          kind: 'agent',
          agent: 'planner',
          status: 'succeeded',
          attempt: 1,
          error: null,
          started_at: '2026-01-01T00:00:00Z',
          finished_at: '2026-01-01T00:00:01Z',
        },
      ],
    } as WorkflowRun

    const steps = mergeWorkflowSteps(execution, [event('running', 'a', '规划')])

    expect(steps[0].status).toBe('succeeded')
    expect(steps[0].finished_at).toBe('2026-01-01T00:00:01Z')
  })

  it('keeps a newer persisted retry attempt over an older live event', () => {
    const execution = {
      status: 'running',
      steps: [
        {
          id: 'a',
          node_id: 'a',
          label: 'Retrying',
          kind: 'agent',
          agent: 'researcher',
          status: 'retrying',
          attempt: 2,
          error: 'temporary failure',
          started_at: '2026-01-01T00:00:02Z',
          finished_at: null,
        },
      ],
    } as WorkflowRun

    const steps = mergeWorkflowSteps(execution, [event('running', 'a', 'Retrying', 1)])

    expect(steps[0]).toMatchObject({
      status: 'retrying',
      attempt: 2,
      error: 'temporary failure',
      started_at: '2026-01-01T00:00:02Z',
    })
  })

  it('accepts a live event from a newer attempt without erasing persisted timestamps', () => {
    const execution = {
      status: 'running',
      steps: [
        {
          id: 'a',
          node_id: 'a',
          label: 'Retrying',
          kind: 'agent',
          agent: 'researcher',
          status: 'retrying',
          attempt: 1,
          error: 'temporary failure',
          started_at: '2026-01-01T00:00:01Z',
          finished_at: null,
        },
      ],
    } as WorkflowRun

    const steps = mergeWorkflowSteps(execution, [event('running', 'a', 'Retrying', 2)])

    expect(steps[0]).toMatchObject({
      status: 'running',
      attempt: 2,
      started_at: '2026-01-01T00:00:01Z',
    })
  })

  it('uses only persisted steps after the workflow itself is terminal', () => {
    const execution = {
      status: 'failed',
      steps: [
        {
          id: 'a',
          node_id: 'a',
          label: '规划',
          kind: 'agent',
          agent: 'planner',
          status: 'failed',
          attempt: 1,
          error: 'failed',
          started_at: null,
          finished_at: '2026-01-01T00:00:01Z',
        },
      ],
    } as WorkflowRun

    const steps = mergeWorkflowSteps(execution, [event('running', 'a'), event('running', 'stale')])

    expect(steps).toHaveLength(1)
    expect(steps[0].status).toBe('failed')
  })

  it('lets a resumed research run override a stale terminal workflow snapshot', () => {
    const execution = workflowRun({
      status: 'failed',
      definition: { nodes: [{ id: 'research', step: { kind: 'agent', agent: 'researcher' } }] },
      steps: [
        {
          id: 'old-run',
          node_id: 'research',
          label: '研究',
          kind: 'agent',
          agent: 'researcher',
          status: 'failed',
          attempt: 1,
          error: 'old failure',
          started_at: '2026-01-01T00:00:00Z',
          finished_at: '2026-01-01T00:00:01Z',
        },
      ],
    })
    const live = [event('running', 'new-run', '研究', 1, 'research')]

    const steps = mergeWorkflowSteps(execution, live, 'running')
    const progress = deriveResearchProgress({ execution, events: live, runStatus: 'running' })

    expect(steps).toHaveLength(1)
    expect(steps[0]).toMatchObject({ id: 'new-run', node_id: 'research', status: 'running' })
    expect(progress).toMatchObject({ total: 1, completed: 0, terminal: false })
    expect(progress.activeStep?.id).toBe('new-run')
    expect(progress.percent).toBeLessThan(100)
  })

  it('collapses resumed step records by node without merging different parallel nodes', () => {
    const execution = workflowRun({
      status: 'succeeded',
      definition: {
        nodes: [
          { id: 'left', step: { kind: 'agent', agent: 'researcher' } },
          { id: 'right', step: { kind: 'agent', agent: 'critic' } },
        ],
      },
      steps: [
        {
          id: 'left-old',
          node_id: 'left',
          label: '左分支',
          kind: 'agent',
          agent: 'researcher',
          status: 'failed',
          attempt: 1,
          error: 'old failure',
          started_at: null,
          finished_at: null,
        },
        {
          id: 'right-run',
          node_id: 'right',
          label: '右分支',
          kind: 'agent',
          agent: 'critic',
          status: 'succeeded',
          attempt: 1,
          error: null,
          started_at: null,
          finished_at: null,
        },
        {
          id: 'left-new',
          node_id: 'left',
          label: '左分支',
          kind: 'agent',
          agent: 'researcher',
          status: 'succeeded',
          attempt: 1,
          error: null,
          started_at: null,
          finished_at: null,
        },
      ],
    })

    const steps = mergeWorkflowSteps(execution, [], 'done')
    const progress = deriveResearchProgress({ execution, events: [], runStatus: 'done' })

    expect(steps.map((step) => step.id)).toEqual(['left-new', 'right-run'])
    expect(progress).toMatchObject({ total: 2, completed: 2, percent: 100 })
  })

  it('根据计划总数和步骤终态计算阶段进度', () => {
    const events = [
      {
        stage: 'ORCHESTRATOR',
        type: 'info',
        message: '',
        elapsed: 0,
        data: { event_name: 'workflow.plan', total_steps: 4 },
      },
      event('succeeded', 'a'),
      event('running', 'b'),
    ] as ResearchEvent[]
    const progress = deriveResearchProgress({ execution: null, events, runStatus: 'running' })
    expect(progress.total).toBe(4)
    expect(progress.completed).toBe(1)
    expect(progress.percent).toBeGreaterThan(25)
    expect(progress.percent).toBeLessThan(50)
    expect(progress.currentLabel).toBe('b')
  })

  it('完成状态强制收敛到 100%', () => {
    const progress = deriveResearchProgress({ execution: null, events: [], runStatus: 'done' })
    expect(progress.percent).toBe(100)
    expect(progress.currentLabel).toBe('研究任务已完成')
  })
})
