import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { OperationsOverviewData } from '../api/operations'
import { getOperations } from '../api/operations'
import OperationsOverview from './OperationsOverview'

vi.mock('../api/operations', () => ({ getOperations: vi.fn() }))

function fixture(): OperationsOverviewData {
  return {
    as_of: '2026-10-06T05:00:00Z', since: '2026-09-29T05:00:00Z',
    scope: 'mine', date_basis: 'task_creation_utc', backend: 'database', max_records_per_kind: 500,
    coverage: { research_records: 2, qa_records: 1, truncated: false, unknown_date_records: 0, unknown_research_first_results: 2 },
    scenarios: [{ scenario: 'paperRead', total: 2, initial_done: 0, statuses: { done: 1, needs_review: 1 }, origins: { initial: 1, continuation: 1 } }],
    daily: [{ date: '2026-10-06', statuses: { done: 1, needs_review: 1 } }],
    needs_review_reasons: { prose_evidence: 1 },
    research_duration: { observations: 2, p50_seconds: 1, p95_seconds: 2 },
    model_calls: {
      recorded_attempts: 2, statuses: { succeeded: 1, failed: 1 }, retries: 1,
      usage: Object.fromEntries(['input_tokens', 'output_tokens', 'total_tokens', 'reasoning_tokens'].map((key) => [key, { known_total: null, reported_calls: 0, unknown_calls: 2 }])),
      duration_observations: 2, p95_seconds: 0.2, cost: null, cost_basis: 'no_verified_price_configuration',
    },
    rendering: null, limitations: ['历史没有请求记录不表示零调用。'],
  }
}

function setup(allowWorkspace = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><OperationsOverview allowWorkspace={allowWorkspace} /></QueryClientProvider>)
}

beforeEach(() => {
  vi.mocked(getOperations).mockReset().mockResolvedValue(fixture())
})

it('loads only when expanded and keeps missing usage and scientific quality distinct', async () => {
  setup()
  expect(getOperations).not.toHaveBeenCalled()
  await userEvent.click(screen.getByText('运行与请求统计'))
  await screen.findByText('论文精读')
  expect(getOperations).toHaveBeenCalledWith(7, 'mine', expect.any(AbortSignal))
  const table = screen.getByRole('table', { name: '提供方返回的用量' })
  expect(within(table).getAllByText('未知')).toHaveLength(4)
  expect(screen.getByText(/系统完成状态不代表人工科研质量验收/)).toBeInTheDocument()
  expect(screen.queryByLabelText('统计对象')).not.toBeInTheDocument()
})

it('labels truncated scope and refetches a selected period', async () => {
  const data = fixture()
  data.coverage.truncated = true
  vi.mocked(getOperations).mockResolvedValue(data)
  setup()
  await userEvent.click(screen.getByText('运行与请求统计'))
  expect(await screen.findByRole('alert')).toHaveTextContent('不是所选时间范围的全量统计')
  fireEvent.change(screen.getByLabelText('时间范围'), { target: { value: '30' } })
  await waitFor(() => expect(getOperations).toHaveBeenCalledWith(30, 'mine', expect.any(AbortSignal)))
})

it('requests workspace totals only after an administrator explicitly selects them', async () => {
  setup(true)
  await userEvent.click(screen.getByText('运行与请求统计'))
  await screen.findByText('论文精读')
  fireEvent.change(screen.getByLabelText('统计对象'), { target: { value: 'workspace' } })
  await waitFor(() => expect(getOperations).toHaveBeenCalledWith(7, 'workspace', expect.any(AbortSignal)))
})
