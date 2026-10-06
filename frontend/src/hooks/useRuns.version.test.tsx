import { act, renderHook, waitFor } from '@testing-library/react'
import {
  focusManager,
  onlineManager,
  QueryClient,
  QueryClientProvider,
} from '@tanstack/react-query'
import type { PropsWithChildren } from 'react'
import { getRunDocument } from '../api/client'
import { useRunDocument } from './useRuns'

vi.mock('../api/client', async () => ({
  ...(await vi.importActual('../api/client')),
  getRunDocument: vi.fn(),
}))
it('refreshes the current document after tab focus and reconnect while versioned cache entries stay separate', async () => {
  const fetch = vi.mocked(getRunDocument)
  fetch.mockResolvedValueOnce({ content_version: 'old' } as Awaited<
    ReturnType<typeof getRunDocument>
  >)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  const hook = renderHook(() => useRunDocument('version-run'), { wrapper })
  await waitFor(() => expect(hook.result.current.data?.content_version).toBe('old'))
  fetch.mockResolvedValue({ content_version: 'new' } as Awaited<ReturnType<typeof getRunDocument>>)
  act(() => {
    focusManager.setFocused(false)
    focusManager.setFocused(true)
  })
  await waitFor(() => expect(hook.result.current.data?.content_version).toBe('new'))
  fetch.mockResolvedValue({ content_version: 'after-reconnect' } as Awaited<
    ReturnType<typeof getRunDocument>
  >)
  act(() => {
    onlineManager.setOnline(false)
    onlineManager.setOnline(true)
  })
  await waitFor(() => expect(hook.result.current.data?.content_version).toBe('after-reconnect'))
  const history = renderHook(() => useRunDocument('version-run', { version: 'old' }), { wrapper })
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith('version-run', expect.objectContaining({ version: 'old' })),
  )
  expect(client.getQueryCache().getAll()).toHaveLength(2)
  history.unmount()
  hook.unmount()
  client.clear()
  focusManager.setFocused(undefined)
})
