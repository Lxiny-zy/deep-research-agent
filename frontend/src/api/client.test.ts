import {
  clearApiKey,
  createRun,
  downloadRunDocument,
  getRunDocument,
  getApiKey,
  isApiKeyRemembered,
  resumeRun,
  setApiKey,
  streamRun,
} from './client'

describe('API key persistence', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    clearApiKey()
  })

  it('remembers a verified key after the tab session is discarded', () => {
    setApiKey('  remembered-key  ')
    sessionStorage.clear()
    expect(getApiKey()).toBe('remembered-key')
    expect(isApiKeyRemembered()).toBe(true)
  })

  it('removes a remembered key when switching to a session-only login', () => {
    setApiKey('old-key')
    setApiKey('temporary-key', false)
    expect(isApiKeyRemembered()).toBe(false)
    expect(getApiKey()).toBe('temporary-key')
    sessionStorage.clear()
    expect(getApiKey()).toBeNull()
  })

  it('continues to read existing tab-scoped credentials', () => {
    sessionStorage.setItem('dr_api_key', 'legacy-key')
    expect(getApiKey()).toBe('legacy-key')
    expect(isApiKeyRemembered()).toBe(false)
  })

  it('clears both storage locations on logout or rejection', () => {
    localStorage.setItem('dr_api_key', 'persistent-key')
    sessionStorage.setItem('dr_api_key', 'tab-key')
    clearApiKey()
    expect(getApiKey()).toBeNull()
    expect(localStorage.getItem('dr_api_key')).toBeNull()
    expect(sessionStorage.getItem('dr_api_key')).toBeNull()
  })

  it('uses the session when persistent storage is blocked', () => {
    const setItem = Storage.prototype.setItem
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (
      this: Storage,
      name,
      value,
    ) {
      if (this === localStorage) throw new DOMException('Blocked', 'SecurityError')
      setItem.call(this, name, value)
    })
    expect(setApiKey('fallback-key')).toBe('session')
    expect(getApiKey()).toBe('fallback-key')
  })

  it('supports a current-page login when all browser storage is blocked', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('Blocked')
    })
    expect(setApiKey('memory-key')).toBe('memory')
    expect(getApiKey()).toBe('memory-key')
    clearApiKey()
    expect(getApiKey()).toBeNull()
  })

  it('reads remembered credentials even when session storage is unavailable', () => {
    setApiKey('saved-key')
    const getItem = Storage.prototype.getItem
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (this: Storage, name) {
      if (this === sessionStorage) throw new Error('Blocked')
      return getItem.call(this, name)
    })
    expect(getApiKey()).toBe('saved-key')
  })

  it('does not invalidate a new login when an old request returns 401 late', async () => {
    let finish!: (response: Response) => void
    vi.spyOn(globalThis, 'fetch').mockReturnValue(
      new Promise((resolve) => {
        finish = resolve
      }),
    )
    const dispatch = vi.spyOn(window, 'dispatchEvent')
    setApiKey('old-key')
    const pending = createRun({ query: 'research question' })
    setApiKey('new-key')
    finish(new Response(null, { status: 401 }))
    await expect(pending).rejects.toThrow()
    expect(dispatch).not.toHaveBeenCalled()
    expect(getApiKey()).toBe('new-key')
  })
})

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
      new Response(JSON.stringify({ detail: [{ loc: ['body', 'query'], msg: '不能为空' }] }), {
        status: 422,
        statusText: 'Unprocessable Entity',
      }),
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

describe('getRunDocument', () => {
  afterEach(() => vi.restoreAllMocks())

  it('requests the structured document with the HSI table opt-in', async () => {
    const document = {
      schema_version: 1,
      query: 'q',
      blocks: [],
      references: [],
      evidence: [],
      overview: {
        records: 0,
        verbatim_matched: 0,
        semantically_supported: 0,
        corroborated: 0,
        conflicted: 0,
        blocked_sources: null,
      },
      disclaimer: '',
    }
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response(JSON.stringify(document), { status: 200 }))

    await expect(getRunDocument('run/id', { includeHsiTables: true })).resolves.toEqual(document)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/runs/run%2Fid/document?include_hsi_tables=true',
      expect.objectContaining({ headers: { 'Content-Type': 'application/json' } }),
    )
  })

  it('omits the query when HSI tables are not requested', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ blocks: [] }), { status: 200 }),
    )

    await getRunDocument('run-1')
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe('/api/runs/run-1/document')
  })

  it('normalizes a legacy report returned during a rolling upgrade', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          query: 'legacy query',
          markdown: 'legacy markdown',
          citations: ['https://example.test/paper'],
        }),
        { status: 200 },
      ),
    )

    await expect(getRunDocument('run-legacy')).resolves.toMatchObject({
      query: 'legacy query',
      blocks: [{ kind: 'prose', markdown: 'legacy markdown' }],
      references: [
        {
          index: 1,
          url: 'https://example.test/paper',
        },
      ],
    })
  })

  it('rejects an unrelated successful payload for the page compatibility fallback', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    )

    await expect(getRunDocument('run-invalid')).rejects.toThrow(
      'Invalid structured report response',
    )
  })
})

describe('run document downloads', () => {
  afterEach(() => {
    clearApiKey()
    vi.restoreAllMocks()
  })

  it('sends auth and export options, then honors a safe server filename', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('a,b\n1,2\n', {
        status: 200,
        headers: {
          'Content-Disposition': 'attachment; filename="research-run.csv"',
        },
      }),
    )
    setApiKey('secret')

    const result = await downloadRunDocument('run/id', 'csv', {
      includeHsiTables: true,
      tableId: 'table/one',
    })

    expect(result.filename).toBe('research-run.csv')
    expect(result.blob.size).toBeGreaterThan(0)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/runs/run%2Fid/document.csv?include_hsi_tables=true&table_id=table%2Fone',
      expect.objectContaining({
        headers: { Accept: 'application/octet-stream', Authorization: 'Bearer secret' },
      }),
    )
  })

  it('formats export errors using the API detail payload', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'install the pdf extra' }), {
        status: 501,
        statusText: 'Not Implemented',
      }),
    )

    await expect(downloadRunDocument('run-1', 'pdf')).rejects.toThrow('install the pdf extra')
  })

  it('keeps table selection out of the server PDF endpoint', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response(new Uint8Array([37, 80, 68, 70]), { status: 200 }))

    await downloadRunDocument('run-1', 'pdf', { tableId: 'table-1' })

    expect(fetchMock.mock.calls[0][0]).toBe('/api/runs/run-1/document.pdf')
  })
})

describe('resumeRun', () => {
  afterEach(() => vi.restoreAllMocks())

  it('posts to the recover endpoint with the normal API key contract', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response(JSON.stringify({ run_id: 'run-1' }), { status: 202 }))

    await expect(resumeRun('run/id')).resolves.toEqual({ run_id: 'run-1' })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/runs/run%2Fid/resume',
      expect.objectContaining({ method: 'POST' }),
    )
  })
})
