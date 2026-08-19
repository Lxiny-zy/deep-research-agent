import { clearApiKey, createRun, setApiKey, streamRun } from './client'

describe('request error formatting', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders an object-shaped business detail instead of the bare status text', async () => {
    // needs_clarification 兜底 422 的 detail 是对象。不解析它的话，
    // 用户在错误框里只看到「Unprocessable Entity」，完全不知道该怎么办。
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: {
            code: 'needs_clarification',
            message: '请求信息不足，请把问题说得更具体一些',
            question: '想研究什么方向？',
            options: [],
          },
        }),
        { status: 422, statusText: 'Unprocessable Entity' },
      ),
    )

    await expect(createRun({ query: '帮我看看' })).rejects.toThrow(
      '请求信息不足，请把问题说得更具体一些：想研究什么方向？',
    )
  })

  it('keeps FastAPI validation array formatting', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({ detail: [{ loc: ['body', 'query'], msg: '不能为空' }] }),
        { status: 422, statusText: 'Unprocessable Entity' },
      ),
    )

    await expect(createRun({ query: '' })).rejects.toThrow('query: 不能为空')
  })
})

describe('streamRun', () => {
  afterEach(() => {
    clearApiKey()
    vi.restoreAllMocks()
  })

  it('sends the API key in a header and parses chunked SSE without exposing it in the URL', async () => {
    const encoder = new TextEncoder()
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: 'start' })}\n`))
        controller.enqueue(encoder.encode(`\ndata: ${JSON.stringify({ type: 'done' })}\n\n`))
        controller.close()
      },
    })
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response(body, { status: 200 }))
    const messages: string[] = []
    setApiKey('secret/@:key')

    await streamRun('run/id', (data) => messages.push(data))

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/runs/run%2Fid/stream',
      expect.objectContaining({
        headers: {
          Accept: 'text/event-stream',
          Authorization: 'Bearer secret/@:key',
        },
      }),
    )
    expect(fetchMock.mock.calls[0][0]).not.toContain('secret')
    expect(messages.map((message) => JSON.parse(message))).toEqual([
      { type: 'start' },
      { type: 'done' },
    ])
  })
})
