import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { useConversations } from './useConversations'
import { listConversations } from '../api/client'

vi.mock('../api/client', () => ({ listConversations: vi.fn() }))

it('loads older conversations using the last timestamp and id', async () => {
  const first = Array.from({ length: 30 }, (_, i) => ({ id: `c${i}`, updated_at: '2026-01-01T00:00:00Z' }))
  vi.mocked(listConversations).mockResolvedValueOnce(first as never).mockResolvedValueOnce([{ id: 'older' }] as never)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const { result } = renderHook(() => useConversations('r'), { wrapper })
  await waitFor(() => expect(result.current.data).toHaveLength(30))
  expect(result.current.hasNextPage).toBe(true)
  await act(() => result.current.fetchNextPage())
  await waitFor(() => expect(result.current.data).toHaveLength(31))
  expect(listConversations).toHaveBeenLastCalledWith(expect.any(AbortSignal), 'r', {
    limit: 30, before: '2026-01-01T00:00:00Z|c29',
  })
  expect(result.current.hasNextPage).toBe(false)
})
