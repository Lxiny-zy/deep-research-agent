import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type { RunSummary } from '../types'
import HistoryPage from './HistoryPage'

const mocks = vi.hoisted(() => ({
  useRunsList: vi.fn(),
  useTags: vi.fn(),
  useDeleteRun: vi.fn(),
  useBatchDeleteRuns: vi.fn(),
}))

vi.mock('../hooks/useRuns', () => ({
  useRunsList: mocks.useRunsList,
  useTags: mocks.useTags,
  useDeleteRun: mocks.useDeleteRun,
  useBatchDeleteRuns: mocks.useBatchDeleteRuns,
}))

vi.mock('../hooks/useRevealOnScroll', () => ({ useRevealOnScroll: vi.fn() }))

const LONG_QUERY = '设计一个高光谱图像拼接算法，并系统比较光谱一致性、接缝优化与配准方法。'.repeat(8)
const RUN: RunSummary = {
  id: 'run-1',
  query: LONG_QUERY,
  status: 'done',
  created_at: '2026-09-07T03:04:05Z',
  total_tokens: 3401,
  elapsed: 1775.9,
  tags: [],
}

describe('HistoryPage result rows', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.useRunsList.mockReturnValue({
      data: [RUN],
      isLoading: false,
      isError: false,
      error: null,
    })
    mocks.useTags.mockReturnValue({ data: [] })
    mocks.useDeleteRun.mockReturnValue({ mutateAsync: vi.fn(), isPending: false })
    mocks.useBatchDeleteRuns.mockReturnValue({ mutateAsync: vi.fn(), isPending: false })
  })

  it('uses flat rows instead of nesting cards inside the results panel', () => {
    const { container } = render(
      <MemoryRouter>
        <HistoryPage />
      </MemoryRouter>,
    )

    expect(container.querySelector('.history-run-row')).not.toBeNull()
    expect(container.querySelector('.history-run-list .history-run-card')).toBeNull()
    expect(screen.getByText(LONG_QUERY)).toHaveAttribute('title', LONG_QUERY)
    expect(screen.getByText('3401 tokens')).toBeInTheDocument()
    expect(container.querySelector('time[datetime="2026-09-07T03:04:05Z"]')).not.toBeNull()
  })
})
