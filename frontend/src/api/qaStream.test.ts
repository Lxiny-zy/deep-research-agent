import { afterEach, describe, expect, it, vi } from 'vitest'
import { askQuestion } from './client'
import { RequestTimeoutError } from './transport'

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('QA stream deadline', () => {
  it('times out while waiting for response headers so answer recovery can start', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => reject(init.signal?.reason), { once: true })
        }),
    )
    const result = askQuestion('c1', '实验条件是什么？').catch((error: unknown) => error)
    await vi.advanceTimersByTimeAsync(45_000)
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true)
    expect(await result).toBeInstanceOf(RequestTimeoutError)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('keeps a long answer alive when the server sends heartbeat comments', async () => {
    vi.useFakeTimers()
    const encoder = new TextEncoder()
    let controller!: ReadableStreamDefaultController<Uint8Array>
    const body = new ReadableStream<Uint8Array>({
      start(stream) {
        controller = stream
      },
    })
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(body))
    const result = askQuestion('c1', '实验条件是什么？')
    await vi.advanceTimersByTimeAsync(30_000)
    controller.enqueue(encoder.encode(': keep-alive\n\n'))
    await vi.advanceTimersByTimeAsync(30_000)
    controller.enqueue(encoder.encode(': keep-alive\n\n'))
    await vi.advanceTimersByTimeAsync(30_000)
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(false)
    const message = { id: 'm1', query: '实验条件是什么？', answer: '已经完成' }
    controller.enqueue(encoder.encode(`event: complete\ndata: ${JSON.stringify(message)}\n\n`))
    await expect(result).resolves.toEqual(message)
    expect(vi.getTimerCount()).toBe(0)
  })
})
