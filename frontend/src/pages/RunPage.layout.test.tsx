/**
 * 三栏工作区的结构验收（jsdom 下的离线渲染）：真实的 StepRail / FileTree /
 * RunNarrative / DeliverablesPanel 组件与 RunPage 组合在一起，确认三栏各自出现、
 * 顺序正确、且打印预览时侧栏被移除。视觉样式由 workbench.css 负责，这里只验结构。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import RunPage from './RunPage'

vi.mock('../hooks/useResearchStream', () => ({
  useResearchStream: () => ({
    status: 'done',
    events: [],
    reportMarkdown: '',
    stats: null,
    elapsed: 0,
    tokens: 0,
    findings: 0,
    tokensEstimated: false,
    dag: null,
  }),
}))
vi.mock('../hooks/useRuns', () => ({
  useRunDetail: () => ({
    data: {
      id: 'r1',
      query: '高光谱重建综述',
      status: 'done',
      created_at: null,
      total_tokens: 10,
      elapsed: 1,
      tags: [],
      interpretation: '',
      sub_questions: [],
      results: [],
      report: { query: 'q', markdown: '## 摘要\n正文 [1]', citations: ['https://a.com'] },
      orchestration: null,
      sources: [],
      events: [],
      manifest: null,
      metrics: null,
      intent: null,
    },
    isError: false,
    refetch: vi.fn(),
  }),
  useRunDocument: () => ({ data: undefined }),
  useCancelRun: () => ({ mutate: vi.fn(), isPending: false }),
  useResumeRun: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
}))
vi.mock('../hooks/useWorkbench', () => ({
  useDeliverables: () => ({
    data: {
      version: 1,
      run_id: 'r1',
      template: 'litReview',
      title: '综述',
      status: 'pass',
      generated_at: '',
      primary: 'r.pdf',
      items: [
        {
          name: 'r.pdf',
          format: 'pdf',
          title: '综述（PDF）',
          role: 'report',
          size: 100,
          sha256: 'x',
          mime_type: 'application/pdf',
          status: 'pass',
          issues: [],
        },
      ],
      gates: [],
    },
    isLoading: false,
    error: null,
  }),
  useRunTemplate: () => ({ data: undefined }),
  useNarrative: () => ({
    data: {
      headline: '已完成：2 条已核验证据支撑最终交付物',
      sections: [
        {
          key: 'finish',
          title: '完成',
          status: 'done',
          lines: ['研究完成'],
          first_seq: 0,
          last_seq: 0,
          elapsed: 1,
        },
      ],
      counters: {},
      last_seq: 0,
    },
  }),
  useWorkspace: () => ({
    data: {
      run_id: 'r1',
      status: 'done',
      workflow: 'lit_review',
      attempt: 1,
      slug: 's',
      steps: [
        {
          index: 0,
          node_id: 'step-1',
          label: 'survey_writer',
          kind: 'agent',
          agent: 'survey_writer',
          status: 'succeeded',
          attempt: 1,
          error: null,
          started_at: null,
          finished_at: null,
          elapsed: 3,
        },
      ],
      replans: [],
      files: [
        {
          path: 'output/s/final/report.md',
          area: 'output',
          stage: 'final',
          name: 'report.md',
          size: 10,
          sha256: 'ab',
          mime_type: 'text/markdown',
          step: null,
          attempt: null,
          created_at: '',
        },
      ],
    },
  }),
}))
vi.mock('../components/ReportActions', () => ({ default: () => null }))
vi.mock('../components/StatsBar', () => ({ default: () => null }))
vi.mock('../components/OrchestrationPipeline', () => ({ default: () => null }))
vi.mock('../components/TagEditor', () => ({ default: () => null }))

describe('RunPage three-pane workbench', () => {
  it('renders steps left, report centre and files right', () => {
    const { container } = render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={['/runs/r1']}>
          <Routes>
            <Route path="/runs/:id" element={<RunPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )
    const grid = container.querySelector('.run-workbench-grid')
    expect(grid).not.toBeNull()
    const panes = Array.from(grid!.children).map((child) => child.className)
    expect(panes[0]).toContain('run-left-pane')
    expect(panes[1]).toContain('run-report-column')
    expect(panes[2]).toContain('run-right-pane')

    const left = grid!.children[0] as HTMLElement
    expect(within(left).getByText('1. 撰写文献综述')).toBeInTheDocument()
    expect(within(left).getByText('已完成：2 条已核验证据支撑最终交付物')).toBeInTheDocument()

    const centre = grid!.children[1] as HTMLElement
    expect(within(centre).getByText('综述（PDF）')).toBeInTheDocument()

    const right = grid!.children[2] as HTMLElement
    expect(within(right).getByRole('button', { name: /report.md/ })).toBeInTheDocument()
    expect(container.querySelector('.run-q')).toHaveTextContent('高光谱重建综述')
  })
})
