import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import type { DeliverableRegistry, RunDetail } from '../types'
import RunPage from './RunPage'

const api = vi.hoisted(() => ({
  getRun: vi.fn(),
  getRunDocument: vi.fn(),
  getDeliverables: vi.fn(),
  retryDeliverable: vi.fn(),
  getRunTemplate: vi.fn(),
  getNarrative: vi.fn(),
  getWorkspace: vi.fn(),
}))

vi.mock('../api/client', async () => ({ ...(await vi.importActual('../api/client')), ...api }))
vi.mock('../hooks/useResearchStream', () => ({
  useResearchStream: () => ({
    status: 'needs_review',
    events: [
      {
        stage: 'ORCHESTRATOR',
        type: 'needs_review',
        message: '原始 PDF 生成失败',
        elapsed: 2,
        data: { status: 'needs_review', completion: { issues: ['原始 PDF 生成失败'] } },
      },
    ],
    reportMarkdown: '',
    stats: null,
    elapsed: 2,
    tokens: 20,
    findings: 1,
    tokensEstimated: false,
    dag: null,
  }),
}))

const scrollToDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollTo')
beforeEach(() => {
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({ matches: true })),
  )
  Object.defineProperty(HTMLElement.prototype, 'scrollTo', { configurable: true, value: vi.fn() })
})
afterEach(() => {
  vi.unstubAllGlobals()
  if (scrollToDescriptor)
    Object.defineProperty(HTMLElement.prototype, 'scrollTo', scrollToDescriptor)
  else Reflect.deleteProperty(HTMLElement.prototype, 'scrollTo')
})

it('refreshes persisted run status after a format retry and removes old review diagnostics', async () => {
  let repaired = false
  const detail: RunDetail = {
    id: 'review-run',
    query: '交付验证',
    status: 'needs_review',
    created_at: null,
    total_tokens: 20,
    elapsed: 2,
    tags: [],
    interpretation: '',
    sub_questions: [],
    results: [],
    report: { query: '交付验证', markdown: '已经核验的正文。', citations: [] },
    orchestration: null,
    sources: [],
    events: [],
    manifest: null,
    metrics: null,
    intent: null,
    completion: { status: 'needs_review', issues: ['原始 PDF 生成失败'] },
  }
  const registry: DeliverableRegistry = {
    version: 1,
    run_id: detail.id,
    template: 'litReview',
    title: '交付验证',
    generated_at: '',
    status: 'fail',
    primary: null,
    items: [],
    gates: [],
    content_version: 'a'.repeat(64),
    can_retry: true,
    failures: [{ format: 'pdf', title: 'PDF', issues: ['渲染失败'], retryable: true }],
  }
  const ready: DeliverableRegistry = {
    ...registry,
    content_version: 'b'.repeat(64),
    status: 'pass',
    failures: [],
    primary: 'report.pdf',
    items: [
      {
        name: 'report.pdf',
        format: 'pdf',
        title: '报告 PDF',
        role: 'report',
        size: 40,
        sha256: 'c'.repeat(64),
        mime_type: 'application/pdf',
        status: 'pass',
        issues: [],
      },
    ],
  }
  api.getRun.mockImplementation(async () =>
    repaired ? { ...detail, status: 'done', completion: { status: 'done', issues: [] } } : detail,
  )
  api.getDeliverables.mockResolvedValue(registry)
  api.getRunDocument.mockResolvedValue({
    schema_version: 1,
    query: detail.query,
    title: detail.query,
    blocks: [{ kind: 'prose', markdown: detail.report!.markdown }],
    references: [],
    evidence: [],
    overview: { blocked_sources: 0 },
  })
  api.getRunTemplate.mockResolvedValue(null)
  api.getNarrative.mockImplementation(async () => ({
    headline: repaired ? '交付修复后的最终叙事' : '交付修复前的待复核叙事',
    sections: [
      {
        key: 'finish',
        title: repaired ? '完成' : '待复核',
        status: repaired ? 'done' : 'needs_review',
        lines: [],
        first_seq: 1,
        last_seq: 1,
        elapsed: 2,
      },
    ],
    counters: {},
    last_seq: 1,
  }))
  api.getWorkspace.mockResolvedValue(null)
  api.retryDeliverable.mockImplementation(async () => {
    // The retry response remains a registry; only a fresh detail read can
    // observe the backend's new durable completion state.
    repaired = true
    return ready
  })

  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  })
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/runs/review-run']}>
        <Routes>
          <Route path="/runs/:id" element={<RunPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  try {
    const retry = await screen.findByRole('button', { name: '重新生成 PDF' })
    expect(view.container.querySelector('.run-head .badge')).toHaveTextContent('待复核')
    expect(screen.getByRole('list', { name: '待复核问题' })).toHaveTextContent('原始 PDF 生成失败')
    const readsBeforeRetry = api.getRun.mock.calls.length
    await screen.findByRole('heading', { name: '交付修复前的待复核叙事' })

    fireEvent.click(retry)

    await waitFor(() =>
      expect(view.container.querySelector('.run-head .badge')).toHaveTextContent('已完成'),
    )
    expect(api.retryDeliverable).toHaveBeenCalledWith(
      'review-run',
      'a'.repeat(64),
      'pdf',
      expect.any(String),
    )
    expect(api.getRun.mock.calls.length).toBeGreaterThan(readsBeforeRetry)
    expect(client.getQueryData<RunDetail>(['run', 'review-run'])?.status).toBe('done')
    expect(screen.queryByRole('list', { name: '待复核问题' })).not.toBeInTheDocument()
    expect(screen.queryByText(/报告或交付尚未通过验收/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '重新生成 PDF' })).not.toBeInTheDocument()
    expect(screen.getByText('自动检查通过')).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: '交付修复后的最终叙事' })).toBeInTheDocument()
    expect(
      screen.queryByRole('heading', { name: '交付修复前的待复核叙事' }),
    ).not.toBeInTheDocument()
  } finally {
    view.unmount()
    client.clear()
  }
})
