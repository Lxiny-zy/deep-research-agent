import { afterEach, expect, it, vi } from 'vitest'
import { clearApiKey, fetchReaderPdf, setApiKey } from './client'
import {
  clearReaderPdfCache,
  clearReaderPdfMemory,
  persistentReaderPdf,
  persistReaderPdf,
  readerPdfCacheGeneration,
} from '../lib/readerPdfCache'

afterEach(async () => {
  clearApiKey()
  await clearReaderPdfCache()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

function fakeDisk() {
  const entries = new Map<string, Response>()
  const urlOf = (key: RequestInfo | URL) =>
    typeof key === 'string' ? key : key instanceof URL ? key.href : key.url
  const cache = {
    match: vi.fn(async (key: RequestInfo | URL) => entries.get(urlOf(key))?.clone()),
    put: vi.fn(async (key: RequestInfo | URL, response: Response) => {
      entries.set(urlOf(key), response.clone())
    }),
    delete: vi.fn(async (key: RequestInfo | URL) => entries.delete(urlOf(key))),
    keys: vi.fn(async () => Array.from(entries.keys(), (url) => new Request(url))),
  }
  vi.stubGlobal('caches', {
    open: vi.fn(async () => cache),
    delete: vi.fn(async () => {
      entries.clear()
      return true
    }),
  })
  return { entries, cache }
}

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

it('restores PDF bytes after memory loss, hashes private keys, and expires persistent data', async () => {
  const { entries } = fakeDisk()
  const fetchMock = vi
    .spyOn(globalThis, 'fetch')
    .mockImplementation(async () => new Response('%PDF-persistent'))
  setApiKey('private-api-key')
  await fetchReaderPdf('private-run', 'doc')
  expect(entries.size).toBe(1)
  const stored = [...entries][0]
  expect(stored[0]).not.toMatch(/private-api-key|private-run/)
  expect(stored[1].headers.has('Authorization')).toBe(false)
  clearReaderPdfMemory()
  const reused = await fetchReaderPdf('private-run', 'doc')
  expect(new TextDecoder().decode(reused)).toBe('%PDF-persistent')
  expect(fetchMock).toHaveBeenCalledTimes(1)
  clearReaderPdfMemory()
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 25 * 60 * 60 * 1000)
  await fetchReaderPdf('private-run', 'doc')
  expect(fetchMock).toHaveBeenCalledTimes(2)
})

it('treats blocked storage as an optional cache and still loads the PDF', async () => {
  fakeDisk()
  vi.mocked(caches.open).mockRejectedValue(new DOMException('blocked', 'SecurityError'))
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('%PDF-fallback'))
  setApiKey('user')
  expect(new TextDecoder().decode(await fetchReaderPdf('run', 'doc'))).toBe('%PDF-fallback')
})

it('clears persistent private data on identity changes and rejects a late old download', async () => {
  const { entries } = fakeDisk()
  const fetchMock = vi
    .spyOn(globalThis, 'fetch')
    .mockImplementation(async () => new Response('%PDF-first'))
  setApiKey('first-user')
  await fetchReaderPdf('run', 'doc')
  let finish!: (response: Response) => void
  fetchMock.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve
      }),
  )
  const old = fetchReaderPdf('run', 'late-doc')
  const rejected = expect(old).rejects.toMatchObject({ name: 'AbortError' })
  await vi.waitFor(() => expect(finish).toBeDefined())
  setApiKey('second-user')
  finish(new Response('%PDF-old'))
  await rejected
  await clearReaderPdfCache()
  expect(entries.size).toBe(0)
  await fetchReaderPdf('run', 'doc')
  expect(fetchMock).toHaveBeenCalledTimes(3)
})

it('serializes an in-flight disk write before logout deletion', async () => {
  const { entries, cache } = fakeDisk()
  let finish!: () => void
  const put = cache.put.getMockImplementation()!
  cache.put.mockImplementationOnce(async (key, response) => {
    await new Promise<void>((resolve) => {
      finish = resolve
    })
    await put(key, response)
  })
  const write = persistReaderPdf(
    'secret',
    new TextEncoder().encode('%PDF-race').buffer,
    readerPdfCacheGeneration(),
  )
  await vi.waitFor(() => expect(finish).toBeDefined())
  const cleared = clearReaderPdfCache()
  finish()
  await Promise.all([write, cleared])
  expect(entries.size).toBe(0)
  expect(await persistentReaderPdf('secret', readerPdfCacheGeneration())).toBeNull()
})

it('evicts old disk entries to respect capacity and isolates document identities', async () => {
  const { entries } = fakeDisk()
  const epoch = readerPdfCacheGeneration()
  await persistReaderPdf('alice/run/doc', new TextEncoder().encode('%PDF-old').buffer, epoch)
  const [url] = [...entries.keys()]
  // Seed the metadata of a large existing PDF without allocating 96 MiB in this test.
  entries.set(
    url,
    new Response('%PDF-old', {
      headers: {
        'Content-Length': String(96 * 1024 * 1024),
        'X-Pdf-Expires': String(Date.now() + 10000),
      },
    }),
  )
  expect(await persistentReaderPdf('bob/run/doc', epoch)).toBeNull()
  const bytes = new TextEncoder().encode('%PDF-new').buffer
  const write = persistReaderPdf('alice/run/other-doc', bytes, epoch)
  structuredClone(bytes, { transfer: [bytes] })
  await write
  expect(entries.has(url)).toBe(false)
  expect(entries.size).toBe(1)
  expect(new TextDecoder().decode((await persistentReaderPdf('alice/run/other-doc', epoch))!)).toBe(
    '%PDF-new',
  )
})
