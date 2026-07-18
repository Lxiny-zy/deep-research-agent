import type { ResearchEvent, WorkflowRun } from '../types'
import { deriveResearchProgress, mergeWorkflowSteps } from './runProgress'

function event(status: string, id: string, label = id): ResearchEvent {
  return {
    stage: 'ORCHESTRATOR',
    type: 'info',
    message: '',
    elapsed: 1,
    data: { event_name: `step.${status}`, step_run_id: id, node_id: id, status, label },
  }
}

describe('runProgress', () => {
  it('用实时事件覆盖轮询详情中的滞后步骤状态', () => {
    const execution = {
      status: 'running',
      steps: [
        {
          id: 'a', node_id: 'a', label: '规划', kind: 'agent', agent: 'planner',
          status: 'ready', attempt: 0, error: null, started_at: null, finished_at: null,
        },
      ],
    } as WorkflowRun
    const steps = mergeWorkflowSteps(execution, [event('running', 'a', '规划')])
    expect(steps[0].status).toBe('running')
  })

  it('根据计划总数和步骤终态计算阶段进度', () => {
    const events = [
      {
        stage: 'ORCHESTRATOR', type: 'info', message: '', elapsed: 0,
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
