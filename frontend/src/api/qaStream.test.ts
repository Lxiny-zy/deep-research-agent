import { afterEach, describe, expect, it, vi } from 'vitest'
import { askQuestion } from './client'
import { QaStreamInterruptedError, RequestTimeoutError } from './transport'

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('QA stream deadline', () => {
  it('forwards reasoning and revision resets separately from answer text', async () => {
    const encoder = new TextEncoder()
    const frames = [
      'event: reasoning\ndata: {"call_id":"one","reasoning_delta":"模型返回的内容"}\n\n',
      'event: delta\ndata: {"delta":"第一版"}\n\n',
      'event: reset\ndata: {"message":"正在修订"}\n\n',
      'event: delta\ndata: {"delta":"第二版"}\n\n',
      'event: complete\ndata: {"answer":"第二版"}\n\n',
    ]
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        new ReadableStream({
          start(controller) {
            frames.forEach((frame) => controller.enqueue(encoder.encode(frame)))
            controller.close()
          },
        }),
      ),
    )
    const observed: string[] = []
    await askQuestion(
      'c',
      'q',
      undefined,
      undefined,
      (text) => observed.push(text),
      (event) => observed.push(event.type),
    )
    expect(observed).toEqual(['reasoning', '第一版', 'reset', '第二版'])
  })

  it('classifies a premature EOF as recoverable and flushes received text', async () => {
    const encoder = new TextEncoder()
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(encoder.encode('event: delta\ndata: {"delta":"部分正文"}\n\n'))
            controller.close()
          },
        }),
      ),
    )
    const onDelta = vi.fn()
    await expect(askQuestion('c1', '问题', undefined, undefined, onDelta)).rejects.toBeInstanceOf(
      QaStreamInterruptedError,
    )
    expect(onDelta).toHaveBeenCalledWith('部分正文')
  })

  it('delivers draft chunks before completion and returns the validated replacement', async () => {
    const encoder = new TextEncoder()
    let stream!: ReadableStreamDefaultController<Uint8Array>
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        new ReadableStream({
          start(controller) {
            stream = controller
          },
        }),
      ),
    )
    const onDelta = vi.fn()
    let completed = false
    const result = askQuestion('c1', '问题', undefined, { sources: [] }, onDelta).then(
      (message) => {
        completed = true
        return message
      },
    )
    stream.enqueue(encoder.encode('event: delta\ndata: {"delta":"待核验的正文"}\n\n'))
    await vi.waitFor(() => expect(onDelta).toHaveBeenCalledWith('待核验的正文'))
    expect(completed).toBe(false)
    const final = { id: 'm1', answer: '核验后的答案', citations: [] }
    stream.enqueue(encoder.encode(`event: complete\ndata: ${JSON.stringify(final)}\n\n`))
    await expect(result).resolves.toEqual(final)
    expect(onDelta).toHaveBeenCalledTimes(1)
  })

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
