import { render, screen } from '@testing-library/react'
import type { ResearchEvent, WorkflowRun } from '../types'
import OrchestrationPipeline from './OrchestrationPipeline'

function terminalExecution(status: 'failed' | 'cancelled'): WorkflowRun {
  return {
    id: 'workflow-run',
    workflow_name: 'test',
    status,
    input: {},
    output: {},
    definition: {
      nodes: [{ id: 'research', step: { kind: 'agent', agent: 'researcher' } }],
    },
    steps: [
      {
        id: 'old-step',
        node_id: 'research',
        label: 'Old research',
        kind: 'agent',
        agent: 'researcher',
        status,
        attempt: 1,
        error: 'old failure',
        started_at: '2026-01-01T00:00:00Z',
        finished_at: '2026-01-01T00:00:01Z',
      },
    ],
    started_at: '2026-01-01T00:00:00Z',
    finished_at: '2026-01-01T00:00:01Z',
  }
}

const resumedEvents: ResearchEvent[] = [
  {
    stage: 'ORCHESTRATOR',
    type: 'info',
    message: '',
    elapsed: 1,
    data: {
      event_name: 'step.running',
      step_run_id: 'new-step',
      node_id: 'research',
      status: 'running',
      label: 'New research',
      kind: 'agent',
      agent: 'researcher',
      attempt: 1,
    },
  },
]

describe('OrchestrationPipeline resume state', () => {
  it.each(['failed', 'cancelled'] as const)(
    'shows a resumed run as running over a stale %s workflow snapshot',
    (terminalStatus) => {
      const { container } = render(
        <OrchestrationPipeline
          execution={terminalExecution(terminalStatus)}
          events={resumedEvents}
          runStatus="running"
        />,
      )

      expect(container.querySelector('.runtime-status')).toHaveClass('running')
      expect(container.querySelectorAll('.runtime-step')).toHaveLength(1)
      expect(container.querySelector('.runtime-step')).toHaveClass('running')
      expect(container.querySelector('.runtime-step')).toHaveTextContent('New research')
      expect(screen.queryByText('Old research')).not.toBeInTheDocument()
    },
  )

  it.each([
    ['done', 'succeeded'],
    ['error', 'failed'],
  ] as const)(
    'shows the research %s state over a stale running workflow snapshot',
    (runStatus, expectedClass) => {
      const execution = terminalExecution('failed')
      execution.status = 'running'

      const { container } = render(
        <OrchestrationPipeline
          execution={execution}
          events={[]}
          runStatus={runStatus}
        />,
      )

      expect(container.querySelector('.runtime-status')).toHaveClass(expectedClass)
      expect(container.querySelector('.runtime-status')).not.toHaveClass('running')
    },
  )
})
