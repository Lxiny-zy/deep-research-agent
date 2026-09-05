import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { useResearchStream, type ResearchStreamState } from '../hooks/useResearchStream'
import { useRunDetail, useRunDocument } from '../hooks/useRuns'
import { loadThread } from '../lib/conversation'
import type { ReportDocument, RunDetail, RunStatus } from '../types'
import RunPage from './RunPage'

const navigateMock = vi.hoisted(() => vi.fn())
const resumeMutateMock = vi.hoisted(() => vi.fn())

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => navigateMock }
})
vi.mock('../hooks/useResearchStream', () => ({ useResearchStream: vi.fn() }))
vi.mock('../hooks/useRuns', () => ({
  useRunDetail: vi.fn(),
  useRunDocument: vi.fn(() => ({ data: undefined, isError: false, error: null })),
  useCancelRun: () => ({ mutate: vi.fn(), isPending: false }),
  useResumeRun: () => ({ mutate: resumeMutateMock, isPending: false, isError: false, error: null }),
}))
vi.mock('../components/DagView', () => ({ default: () => null }))
vi.mock('../components/EventTimeline', () => ({ default: () => null }))
vi.mock('../components/OrchestrationPipeline', () => ({ default: () => null }))
vi.mock('../components/ReportActions', () => ({ default: () => null }))
vi.mock('../components/StatsBar', () => ({ default: () => null }))
vi.mock('../components/StatusBadge', () => ({
  default: ({ status }: { status: string }) => <div data-testid="status">{status}</div>,
}))
vi.mock('../components/TagEditor', () => ({ default: () => null }))
vi.mock('../components/ReportView', () => ({
  default: ({
    markdown,
    isLive,
    findings = [],
    citations = [],
  }: {
    markdown: string
    isLive?: boolean
    findings?: unknown[]
    citations?: string[]
  }) => (
    <div
      data-live={isLive ? 'true' : 'false'}
      data-findings={findings.length}
      data-citations={citations.join('|')}
      data-testid="report-markdown"
    >
      {markdown}
    </div>
  ),
}))

const useResearchStreamMock = vi.mocked(useResearchStream)
const useRunDetailMock = vi.mocked(useRunDetail)
const useRunDocumentMock = vi.mocked(useRunDocument)

function makeStream(
  status: ResearchStreamState['status'],
  reportMarkdown = '',
): ResearchStreamState {
  return {
    events: [],
    reportMarkdown,
    status,
    stats: null,
    dag: null,
    elapsed: 0,
    tokens: 0,
    tokensEstimated: false,
    findings: 0,
  }
}

function makeDetail(
  status: RunStatus,
  markdown: string | null = null,
  workflowName = '',
  intent: RunDetail['intent'] = null,
  orchestration?: { attempt?: number; checkpoint?: Record<string, unknown> },
): RunDetail {
  return {
    id: 'run-1',
    query: 'query',
    status,
    created_at: null,
    total_tokens: 0,
    elapsed: 0,
    tags: [],
    interpretation: '',
    sub_questions: [],
    results: [],
    report: markdown === null ? null : { query: 'query', markdown, citations: [] },
    orchestration: workflowName
      ? {
          id: 'workflow-1',
          workflow_name: workflowName,
          status: 'succeeded',
          attempt: orchestration?.attempt,
          input: {},
          output: {},
          checkpoint: orchestration?.checkpoint,
          steps: [],
          started_at: null,
          finished_at: null,
        }
      : null,
    sources: [],
    events: [],
    manifest: null,
    metrics: null,
    intent,
  }
}

function makeIntent(intent: string): NonNullable<RunDetail['intent']> {
  return {
    intent,
    confidence: 1,
    tier: 'rule',
    risk: 'none',
    risk_confidence: 0,
    signals: [],
    escalated: false,
    scores: {},
    reason: '',
    slots: { entities: [], time_range: '', domain: '', language: '', aspects: [] },
    context_resolved: false,
    resolved_query: 'query',
    clarification: null,
  }
}

function makeDocument(
  markdown = 'structured report',
  evidence: ReportDocument['evidence'] = [],
): ReportDocument {
  return {
    schema_version: 1,
    query: 'query',
    blocks: [
      { kind: 'prose', markdown },
      {
        kind: 'table',
        id: 'hsi_reconstruction',
        title: 'Reconstruction algorithms',
        columns: [
          {
            key: 'method',
            label: 'Method',
            unit: '',
            align: 'left',
            numeric: false,
            note_ref: null,
          },
          { key: 'psnr', label: 'PSNR', unit: 'dB', align: 'right', numeric: true, note_ref: null },
        ],
        rows: [
          {
            label: 'Method A',
            citation: 1,
            cells: {
              method: {
                value: 'Method A',
                numeric: null,
                citations: [1],
                note_ref: null,
                disputed: false,
              },
              psnr: {
                value: '38.36',
                numeric: 38.36,
                citations: [1],
                note_ref: null,
                disputed: true,
              },
            },
          },
        ],
        notes: [],
        caption: '',
      },
    ],
    references: [{ index: 1, url: 'https://example.test/paper', reference: 'Paper' }],
    evidence,
    overview: {
      records: 1,
      verbatim_matched: 1,
      semantically_supported: 1,
      corroborated: 0,
      conflicted: 0,
      blocked_sources: 0,
    },
    disclaimer: 'disclaimer',
  }
}

function renderRunPage() {
  return render(
    <MemoryRouter initialEntries={['/runs/run-1']}>
      <Routes>
        <Route path="/runs/:id" element={<RunPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('RunPage database synchronization', () => {
  beforeEach(() => {
    useRunDocumentMock.mockReturnValue({
      data: undefined,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRunDocument>)
  })

  it('keeps the live report bounded when full-width reading is active', () => {
    useResearchStreamMock.mockReturnValue(makeStream('streaming', '# partial report'))
    useRunDetailMock.mockReturnValue({
      data: makeDetail('running'),
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useRunDetail>)

    const { container } = renderRunPage()
    const reportPanel = container.querySelector('.report-panel')
    expect(reportPanel).toHaveClass('is-streaming')
    expect(screen.getByTestId('report-markdown')).toHaveAttribute('data-live', 'true')

    const toggle = container.querySelector<HTMLButtonElement>('.report-expand-toggle')
    expect(toggle).not.toBeNull()
    if (!toggle) return
    fireEvent.click(toggle)

    expect(container.querySelector('.grid-2')).toHaveClass('report-expanded')
    expect(reportPanel).toHaveClass('is-streaming')
  })

  it('keeps the live report bounded after SSE disconnects while the database run is active', () => {
    useResearchStreamMock.mockReturnValue(makeStream('disconnected', 'partial stream'))
    useRunDetailMock.mockReturnValue({
      data: makeDetail('running'),
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useRunDetail>)

    const { container } = renderRunPage()
    const reportPanel = container.querySelector('.report-panel')
    expect(reportPanel).toHaveClass('is-streaming')
    expect(screen.getByTestId('report-markdown')).toHaveAttribute('data-live', 'true')

    const toggle = container.querySelector<HTMLButtonElement>('.report-expand-toggle')
    expect(toggle).not.toBeNull()
    if (!toggle) return
    fireEvent.click(toggle)

    expect(container.querySelector('.grid-2')).toHaveClass('report-expanded')
    expect(reportPanel).toHaveClass('is-streaming')
  })

  afterEach(() => vi.clearAllMocks())

  it('uses a persisted complete report after the stream disconnects', () => {
    useResearchStreamMock.mockReturnValue(makeStream('disconnected', 'partial stream'))
    useRunDetailMock.mockReturnValue({
      data: makeDetail('running', 'complete database report'),
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useRunDetail>)

    renderRunPage()

    expect(screen.getByTestId('report-markdown')).toHaveTextContent('complete database report')
    expect(screen.getByTestId('report-markdown')).not.toHaveTextContent('partial stream')
  })

  it('falls back to the persisted markdown when the structured document request fails', () => {
    useResearchStreamMock.mockReturnValue(makeStream('done', 'partial stream'))
    useRunDetailMock.mockReturnValue({
      data: makeDetail('done', 'legacy persisted report'),
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useRunDetail>)
    useRunDocumentMock.mockReturnValue({
      data: undefined,
      isError: true,
      error: new Error('document unavailable'),
    } as unknown as ReturnType<typeof useRunDocument>)

    renderRunPage()

    expect(screen.getByTestId('report-markdown')).toHaveTextContent('legacy persisted report')
    expect(screen.getByTestId('report-markdown')).not.toHaveTextContent('partial stream')
    expect(screen.queryByTestId('structured-document-preview')).not.toBeInTheDocument()
  })

  it('uses the structured document after a terminal run and renders its tables', () => {
    useResearchStreamMock.mockReturnValue(makeStream('done', 'legacy report'))
    useRunDetailMock.mockReturnValue({
      data: makeDetail('done', 'legacy report'),
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useRunDetail>)
    useRunDocumentMock.mockReturnValue({
      data: makeDocument(),
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRunDocument>)

    renderRunPage()

    expect(screen.getByTestId('report-markdown')).toHaveTextContent('structured report')
    expect(screen.getAllByTestId('structured-document-preview')).toHaveLength(2)
    expect(screen.getAllByTestId('structured-table-hsi_reconstruction')).toHaveLength(2)
    expect(screen.getAllByTestId('structured-table-hsi_reconstruction')[0]).toHaveTextContent(
      '38.36',
    )
    expect(screen.getAllByTestId('structured-table-hsi_reconstruction')[0]).toHaveTextContent('†')
  })

  it('passes structured evidence and references to the report view', () => {
    useResearchStreamMock.mockReturnValue(makeStream('done', 'legacy report'))
    useRunDetailMock.mockReturnValue({
      data: makeDetail('done', 'legacy report'),
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useRunDetail>)
    useRunDocumentMock.mockReturnValue({
      data: makeDocument('structured report', [
        {
          citation: 1,
          claim_id: 'claim-1',
          statement: 'structured claim',
          quote: 'source quote',
          context: 'source context',
          source_url: 'https://example.test/paper',
          reference: 'Paper',
          source_section: 'Results',
          content_hash: 'hash',
          verbatim_verified: true,
          verification_reason: '',
          semantic_status: 'supported',
          semantic_confidence: 0.9,
          semantic_reason: '',
          consistency_status: 'clear',
          contradicts_claim_ids: [],
          contradiction_reason: '',
          corroboration_status: 'single_source',
          independent_source_count: 1,
          corroboration_reason: '',
          quantity_label: '',
          conditions_label: '',
          quantity_status: 'not_applicable',
          quantity_reason: '',
        },
      ]),
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRunDocument>)

    renderRunPage()

    const report = screen.getByTestId('report-markdown')
    expect(report).toHaveAttribute('data-findings', '1')
    expect(report).toHaveAttribute('data-citations', 'https://example.test/paper')
  })

  it('does not opt into HSI tables for ordinary workflows', () => {
    useResearchStreamMock.mockReturnValue(makeStream('done'))
    useRunDetailMock.mockReturnValue({
      data: makeDetail('done', 'complete'),
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useRunDetail>)

    renderRunPage()

    expect(useRunDocumentMock).toHaveBeenCalledWith('run-1', {
      enabled: true,
      includeHsiTables: false,
    })
  })

  it('opts into HSI tables when the persisted workflow is hsi_review', () => {
    useResearchStreamMock.mockReturnValue(makeStream('done'))
    useRunDetailMock.mockReturnValue({
      data: makeDetail('done', 'complete', 'hsi_review'),
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useRunDetail>)

    renderRunPage()

    expect(useRunDocumentMock).toHaveBeenCalledWith('run-1', {
      enabled: true,
      includeHsiTables: true,
    })
  })

  it('opts into HSI tables for canonical literature review intents', () => {
    useResearchStreamMock.mockReturnValue(makeStream('done'))
    useRunDetailMock.mockReturnValue({
      data: makeDetail('done', 'complete', '', makeIntent('literature_review')),
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useRunDetail>)

    renderRunPage()

    expect(useRunDocumentMock).toHaveBeenCalledWith('run-1', {
      enabled: true,
      includeHsiTables: true,
    })
  })

  it('refetches immediately after an SSE error and polls until the database is terminal', async () => {
    type Interval = (query: { state: { data?: RunDetail } }) => number | false
    let interval: Interval | undefined
    const refetch = vi.fn().mockResolvedValue(undefined)
    useResearchStreamMock.mockReturnValue(makeStream('error'))
    useRunDetailMock.mockImplementation((_id, options) => {
      if (typeof options?.refetchInterval === 'function') {
        interval = options.refetchInterval
      }
      return {
        data: makeDetail('running'),
        isError: false,
        error: null,
        refetch,
      } as unknown as ReturnType<typeof useRunDetail>
    })

    renderRunPage()

    await waitFor(() => expect(refetch).toHaveBeenCalledTimes(1))
    expect(interval?.({ state: { data: makeDetail('running') } })).toBe(4000)
    expect(interval?.({ state: { data: makeDetail('error') } })).toBe(false)
  })

  it('lets a persisted terminal status override a stale live stream', () => {
    useResearchStreamMock.mockReturnValue(makeStream('streaming', 'partial'))
    useRunDetailMock.mockReturnValue({
      data: makeDetail('done', 'complete'),
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useRunDetail>)

    renderRunPage()

    expect(screen.getByTestId('status')).toHaveTextContent('done')
  })

  it('toggles the report between the weighted two-column and full-width layout', () => {
    useResearchStreamMock.mockReturnValue(makeStream('idle'))
    useRunDetailMock.mockReturnValue({
      data: makeDetail('done', 'complete'),
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useRunDetail>)

    const { container } = renderRunPage()

    const grid = container.querySelector('.grid-2')
    expect(grid).not.toBeNull()
    expect(grid).not.toHaveClass('report-expanded')

    expect(screen.getByRole('button', { name: '双栏视图' })).toHaveAttribute('aria-pressed', 'true')
    const toggle = screen.getByRole('button', { name: '全宽阅读' })
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(toggle)

    expect(grid).toHaveClass('report-expanded')
    expect(toggle).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '双栏视图' })).toHaveAttribute('aria-pressed', 'false')

    fireEvent.click(screen.getByRole('button', { name: '双栏视图' }))
    expect(grid).not.toHaveClass('report-expanded')
    expect(screen.getByRole('button', { name: '双栏视图' })).toHaveAttribute('aria-pressed', 'true')
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
  })
})

// --- 继续追问：把本次运行折叠成一轮上下文再跳去提问页 ---

describe('RunPage follow-up', () => {
  beforeEach(() => sessionStorage.clear())
  afterEach(() => vi.clearAllMocks())

  it('records the finished run as a turn and navigates to the composer', () => {
    useResearchStreamMock.mockReturnValue(makeStream('done'))
    useRunDetailMock.mockReturnValue({
      data: makeDetail('done', 'complete'),
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useRunDetail>)

    renderRunPage()

    fireEvent.click(screen.getByRole('button', { name: '继续追问' }))

    expect(loadThread()).toEqual([
      {
        query: 'query',
        intent: 'unknown',
        slots: { entities: [], time_range: '', domain: '', language: '', aspects: [] },
      },
    ])
    expect(navigateMock).toHaveBeenCalledWith('/?followup=1')
  })

  it('hides the follow-up action while the run is still going', () => {
    // 半截的运行没有可供下一轮指代的结论，把它塞进历史只会误导消解器。
    useResearchStreamMock.mockReturnValue(makeStream('streaming'))
    useRunDetailMock.mockReturnValue({
      data: makeDetail('running'),
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useRunDetail>)

    const { container } = renderRunPage()

    expect(screen.queryByRole('button', { name: '继续追问' })).not.toBeInTheDocument()
    // 按钮不在时那条网格轨道也不该留着，否则标题与状态之间凭空多出一段空白。
    expect(container.querySelector('.run-head')).not.toHaveClass('has-followup')
  })
})

describe('RunPage resume', () => {
  afterEach(() => vi.clearAllMocks())

  it('exposes resume when a failed run has a recoverable checkpoint', () => {
    useResearchStreamMock.mockReturnValue(makeStream('error'))
    const detail = makeDetail('error')
    detail.orchestration = {
      id: 'workflow-1',
      workflow_name: 'deep',
      status: 'failed',
      input: {},
      output: {},
      checkpoint: { scratch: { revision: 1 } },
      steps: [],
      started_at: null,
      finished_at: null,
    }
    useRunDetailMock.mockReturnValue({
      data: detail,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useRunDetail>)

    renderRunPage()

    fireEvent.click(screen.getByRole('button', { name: '恢复运行' }))
    expect(resumeMutateMock).toHaveBeenCalledTimes(1)
  })

  // 回归：恢复后的新尝试如果立刻再次失败，DB 状态会停在 error，与恢复前
  // 完全一样。若"是否在等恢复生效"只看 status，这一刻就再也解不开——页面
  // 永远显示运行中、每 4s 轮询一次、恢复按钮不再出现。attempt 是唯一能区分
  // "同一个 error"和"新一次尝试的 error"的信号。
  it('does not latch when the resumed attempt fails again immediately', () => {
    useResearchStreamMock.mockReturnValue(makeStream('error'))
    const failed = (attempt: number) =>
      makeDetail('error', null, 'deep', null, {
        attempt,
        checkpoint: { scratch: { revision: 1 } },
      })

    resumeMutateMock.mockImplementation((_vars: unknown, opts?: { onSuccess?: () => void }) =>
      opts?.onSuccess?.(),
    )
    useRunDetailMock.mockReturnValue({
      data: failed(1),
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useRunDetail>)

    const { rerender } = renderRunPage()
    fireEvent.click(screen.getByRole('button', { name: '恢复运行' }))

    // 服务端已开始新尝试（attempt 2），但它又失败了——status 仍是 error
    useRunDetailMock.mockReturnValue({
      data: failed(2),
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useRunDetail>)
    rerender(
      <MemoryRouter initialEntries={['/runs/run-1']}>
        <Routes>
          <Route path="/runs/:id" element={<RunPage />} />
        </Routes>
      </MemoryRouter>,
    )

    // 回到终态：状态如实显示 error，而不是停在"运行中"
    expect(screen.getByTestId('status')).toHaveTextContent('error')
    // 且可以再次恢复，而不是把用户锁在一个没有出口的页面上
    expect(screen.getByRole('button', { name: '恢复运行' })).toBeInTheDocument()
  })

  it('treats the run as live while the resumed attempt has not been reported yet', () => {
    useResearchStreamMock.mockReturnValue(makeStream('error'))
    resumeMutateMock.mockImplementation((_vars: unknown, opts?: { onSuccess?: () => void }) =>
      opts?.onSuccess?.(),
    )
    useRunDetailMock.mockReturnValue({
      data: makeDetail('error', null, 'deep', null, {
        attempt: 1,
        checkpoint: { scratch: { revision: 1 } },
      }),
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useRunDetail>)

    renderRunPage()
    fireEvent.click(screen.getByRole('button', { name: '恢复运行' }))

    // 快照还是恢复前那一份（attempt 未变），此时不能把它当权威终态，
    // 否则刚重启的实时流会被立刻关掉。
    expect(screen.queryByRole('button', { name: '恢复运行' })).not.toBeInTheDocument()
  })
})
