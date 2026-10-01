import { afterEach, expect, it, vi } from 'vitest'
import { clearApiKey, fetchReaderPdf, setApiKey } from './client'

afterEach(() => {
  clearApiKey()
  vi.restoreAllMocks()
})

it('reuses downloaded PDF bytes and never exposes the cached buffer to a worker transfer', async () => {
  const fetchMock = vi
    .spyOn(globalThis, 'fetch')
    .mockImplementation(async () => new Response('%PDF-test'))
  setApiKey('first-user')
  const first = await fetchReaderPdf('run', 'doc')
  new Uint8Array(first)[0] = 0
  const second = await fetchReaderPdf('run', 'doc')
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(new TextDecoder().decode(second)).toBe('%PDF-test')
  setApiKey('second-user')
  await fetchReaderPdf('run', 'doc')
  expect(fetchMock).toHaveBeenCalledTimes(2)
})
