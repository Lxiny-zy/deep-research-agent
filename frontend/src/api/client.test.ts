import { clearApiKey, setApiKey, streamRun } from './client'

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
          'X-API-Key': 'secret/@:key',
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
