import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import FileTree from './FileTree'
import StepRail from './StepRail'
import type { RunWorkspace } from '../types'

const mocks = vi.hoisted(() => ({ readWorkspaceFile: vi.fn() }))
vi.mock('../api/client', () => mocks)

const workspace: RunWorkspace = {
  run_id: 'r1',
  status: 'done',
  workflow: 'plan-x',
  attempt: 2,
  slug: 'x',
  steps: [
    {
      index: 0,
      node_id: 'node-collect',
      label: 'researcher',
      kind: 'agent',
      agent: 'researcher',
      status: 'succeeded',
      attempt: 1,
      error: null,
      started_at: null,
      finished_at: null,
      elapsed: 12.4,
    },
    {
      index: 1,
      node_id: 'node-deliver',
      label: 'plan_executor',
      kind: 'agent',
      agent: 'plan_executor',
      status: 'failed',
      attempt: 2,
      error: 'RuntimeError: bad output',
      started_at: null,
      finished_at: null,
      elapsed: null,
    },
  ],
  replans: [
    {
      id: 'replan-1-deliver',
      target: 'deliver',
      trigger: 'failed',
      action: 'rescue',
      name: '缩小范围',
      reason: '',
      result: 'done',
    },
  ],
  files: [
    {
      path: 'output/x/final/report.md',
      area: 'output',
      stage: 'final',
      name: 'report.md',
      size: 2048,
      sha256: 'abcdef0123456789',
      mime_type: 'text/markdown',
      step: 'deliver',
      attempt: 1,
      created_at: '',
    },
    {
      path: 'work/x/explore/raw.bin',
      area: 'work',
      stage: 'explore',
      name: 'raw.bin',
      size: 10,
      sha256: '00',
      mime_type: 'application/octet-stream',
      step: null,
      attempt: null,
      created_at: '',
    },
  ],
}

describe('StepRail', () => {
  it('shows readable labels, attempts, errors and replan rescues', () => {
    render(<StepRail workspace={workspace} />)
    expect(screen.getByText('1. 检索与核验证据')).toBeInTheDocument()
    expect(screen.getByText('第 2 次尝试')).toBeInTheDocument()
    expect(screen.getByText(/RuntimeError: bad output/)).toBeInTheDocument()
    expect(screen.getByText(/重规划补救「缩小范围」：done/)).toBeInTheDocument()
  })
})

describe('FileTree', () => {
  it('groups outputs first and previews text files only', async () => {
    mocks.readWorkspaceFile.mockResolvedValue({ text: '# 报告正文', truncated: false })
    render(
      <QueryClientProvider client={new QueryClient()}>
        <FileTree runId="r1" workspace={workspace} />
      </QueryClientProvider>,
    )
    expect(screen.getByText(/成品 · final/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /raw.bin/ })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: /report.md/ }))
    expect(await screen.findByText('# 报告正文')).toBeInTheDocument()
    expect(mocks.readWorkspaceFile).toHaveBeenCalledWith(
      'r1',
      'output/x/final/report.md',
      expect.anything(),
    )
  })
})
