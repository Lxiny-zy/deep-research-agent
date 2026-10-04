import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, renderHook, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { getNarrative } from '../api/client'
import { useNarrative } from '../hooks/useWorkbench'
import type { RunNarrative as Narrative } from '../types'
import RunNarrative from './RunNarrative'

vi.mock('../api/client', async () => ({
  ...(await vi.importActual('../api/client')),
  getNarrative: vi.fn(),
}))

const review: Narrative = {
  headline: '待复核：报告或必需交付尚未通过验收',
  sections: [
    {
      key: 'finish',
      title: '待复核',
      status: 'needs_review',
      lines: ['运行已结束，报告或必需交付待复核'],
      first_seq: 2,
      last_seq: 2,
      elapsed: 4,
    },
  ],
  counters: {},
  last_seq: 2,
}

beforeEach(() => vi.mocked(getNarrative).mockReset())

it('renders the review-required narrative as a warning without an active spinner or completed label', () => {
  const { container } = render(<RunNarrative narrative={review} />)
  expect(screen.getByRole('heading')).toHaveTextContent('待复核')
  expect(container.querySelector('.narrative-step')).toHaveClass('is-needs_review')
  expect(container.querySelector('.is-active, .spin, .is-done')).toBeNull()
  expect(container).not.toHaveTextContent('已完成')
})

it('fetches a final narrative when the run becomes terminal instead of freezing an active phase', async () => {
  vi.mocked(getNarrative)
    .mockResolvedValueOnce({
      ...review,
      headline: '进行中：正在撰写正文',
      sections: [{ ...review.sections[0], key: 'write', title: '撰写交付物', status: 'active' }],
    })
    .mockResolvedValue(review)
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  const hook = renderHook(({ live }) => useNarrative('review-run', live), {
    wrapper,
    initialProps: { live: true },
  })
  try {
    await waitFor(() => expect(hook.result.current.data?.headline).toContain('进行中'))
    hook.rerender({ live: false })
    await waitFor(() => expect(hook.result.current.data?.headline).toContain('待复核'))
    expect(hook.result.current.data?.sections.every((section) => section.status !== 'active')).toBe(
      true,
    )
  } finally {
    hook.unmount()
    client.clear()
  }
})

it('does not let a slow live narrative swallow or overwrite the final request', async () => {
  let finishLive!: (value: Narrative) => void
  vi.mocked(getNarrative)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishLive = resolve
        }),
    )
    .mockResolvedValue(review)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  const hook = renderHook(({ live }) => useNarrative('review-run', live), {
    wrapper,
    initialProps: { live: true },
  })
  try {
    await waitFor(() => expect(getNarrative).toHaveBeenCalledTimes(1))
    hook.rerender({ live: false })
    await waitFor(() => expect(hook.result.current.data?.headline).toContain('待复核'))
    await act(async () => finishLive({ ...review, headline: '过时的进行中叙事' }))
    expect(hook.result.current.data?.headline).toContain('待复核')
    expect(hook.result.current.data?.headline).not.toContain('进行中')
  } finally {
    finishLive(review)
    hook.unmount()
    client.clear()
  }
})
