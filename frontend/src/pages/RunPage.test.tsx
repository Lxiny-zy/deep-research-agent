import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { useResearchStream, type ResearchStreamState } from '../hooks/useResearchStream'
import { useRunDetail } from '../hooks/useRuns'
import type { RunDetail, RunStatus } from '../types'
import RunPage from './RunPage'

vi.mock('../hooks/useResearchStream', () => ({ useResearchStream: vi.fn() }))
vi.mock('../hooks/useRuns', () => ({ useRunDetail: vi.fn() }))
vi.mock('../components/DagView', () => ({ default: () => null }))
vi.mock('../components/EventTimeline', () => ({ default: () => null }))
vi.mock('../components/OrchestrationPipeline', () => ({ default: () => null }))
vi.mock('../components/ReportActions', () => ({ default: () => null }))
vi.mock('../components/StatsBar', () => ({ default: () => null }))
vi.mock('../components/StatusBadge', () => ({ default: ({ status }: { status: string }) => <div data-testid="status">{status}</div> }))
vi.mock('../components/TagEditor', () => ({ default: () => null }))
vi.mock('../components/ReportView', () => ({
  default: ({ markdown }: { markdown: string }) => (
    <div data-testid="report-markdown">{markdown}</div>
  ),
}))

const useResearchStreamMock = vi.mocked(useResearchStream)
const useRunDetailMock = vi.mocked(useRunDetail)

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

function makeDetail(status: RunStatus, markdown: string | null = null): RunDetail {
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
    orchestration: null,
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

    const toggle = screen.getByRole('button', { name: '全宽阅读' })
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(toggle)

    expect(grid).toHaveClass('report-expanded')
    expect(screen.getByRole('button', { name: '双栏视图' })).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(screen.getByRole('button', { name: '双栏视图' }))
    expect(grid).not.toHaveClass('report-expanded')
  })
})
