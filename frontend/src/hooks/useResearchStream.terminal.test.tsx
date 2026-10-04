import { renderHook, waitFor } from '@testing-library/react'
import { streamRun } from '../api/client'
import { useResearchStream } from './useResearchStream'

vi.mock('../api/client', () => ({ streamRun: vi.fn() }))

it('closes a review-required stream and ignores buffered success without reconnecting', async () => {
  let connection: AbortSignal | undefined
  vi.mocked(streamRun).mockImplementation(async (_id, onMessage, signal) => {
    connection = signal
    onMessage(
      JSON.stringify({ stage: 'DELIVERY', type: 'info', message: '正在生成 PDF', elapsed: 1 }),
    )
    onMessage(
      JSON.stringify({
        stage: 'ORCHESTRATOR',
        type: 'needs_review',
        message: 'PDF 待复核',
        elapsed: 2,
        data: {
          status: 'needs_review',
          total_tokens: 40,
          elapsed: 2,
          sources: 1,
          completion: { status: 'needs_review', issues: ['PDF 生成失败'] },
        },
      }),
    )
    onMessage(
      JSON.stringify({ stage: 'ORCHESTRATOR', type: 'done', message: 'stale success', elapsed: 3 }),
    )
  })
  const { result } = renderHook(() => useResearchStream('review-run'))
  await waitFor(() => expect(result.current.status).toBe('needs_review'))
  expect(connection?.aborted).toBe(true)
  expect(streamRun).toHaveBeenCalledTimes(1)
  expect(result.current.events.map((event) => event.message)).toEqual([
    '正在生成 PDF',
    'PDF 待复核',
  ])
  expect(result.current.stats?.total_tokens).toBe(40)
})
