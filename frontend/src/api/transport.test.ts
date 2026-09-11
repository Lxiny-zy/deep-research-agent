import { clearApiKey, createRun, deleteSearchProfile, setApiKey, streamRun } from './client'
import { RequestTimeoutError, withResponse } from './transport'

afterEach(() => {
  clearApiKey()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

it('accepts the actual 204 deletion response without trying to parse JSON', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 204 }))
  await expect(deleteSearchProfile('profile/id')).resolves.toBeUndefined()
  expect(fetchMock.mock.calls[0][0]).toBe('/api/search-profiles/profile%2Fid')
})

it('bounds both response headers and a stalled body by the same deadline', async () => {
  vi.useFakeTimers()
  for (const response of [
    new Promise<Response>(() => {}),
    Promise.resolve(new Response(new ReadableStream({}))),
  ]) {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(response)
    const failure = withResponse('/slow', {}, res => res.text(), 100).catch(error => error)
    await vi.advanceTimersByTimeAsync(100)
    expect(await failure).toBeInstanceOf(RequestTimeoutError)
  }
})

it('cancels a request immediately and preserves the caller cancellation reason', async () => {
  vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}))
  const controller = new AbortController()
  const request = withResponse('/cancel', { signal: controller.signal }, res => res.text())
  const failure = request.catch(error => error)
  const reason = new DOMException('page left', 'AbortError')
  controller.abort(reason)
  expect(await failure).toBe(reason)
})

it('replays the same body and idempotency key after lost response and page reload', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch')
    .mockRejectedValueOnce(new TypeError('connection lost'))
    .mockResolvedValueOnce(new Response(JSON.stringify({ run_id: 'original' }), { status: 202 }))
  await expect(createRun({ query: 'first assessed wording' }, undefined, 'logical topic')).rejects.toThrow()
  vi.resetModules()
  const reloaded = await import('./client')
  await expect(reloaded.createRun({ query: 'second assessed wording' }, undefined, 'logical topic'))
    .resolves.toEqual({ run_id: 'original' })
  const first = fetchMock.mock.calls[0][1]!
  const retry = fetchMock.mock.calls[1][1]!
  expect(new Headers(first.headers).get('Idempotency-Key')).toBe(new Headers(retry.headers).get('Idempotency-Key'))
  expect(retry.body).toBe(first.body)
  expect(sessionStorage.getItem('dr_pending_run')).toBeNull()
})

it('clears a definitive 422 so clarified text is submitted with a fresh key', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'clarify' }), { status: 422 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ run_id: 'new' }), { status: 202 }))
  await expect(createRun({ query: 'vague' }, undefined, 'topic')).rejects.toThrow('clarify')
  await createRun({ query: 'specific', clarified: true }, undefined, 'topic')
  const first = fetchMock.mock.calls[0][1]!
  const second = fetchMock.mock.calls[1][1]!
  expect(new Headers(first.headers).get('Idempotency-Key')).not.toBe(new Headers(second.headers).get('Idempotency-Key'))
  expect(JSON.parse(String(second.body)).query).toBe('specific')
})

it('keeps an uncertain submission on overload but clears it on identity change', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 503 }))
  await expect(createRun({ query: 'topic' })).rejects.toThrow()
  expect(sessionStorage.getItem('dr_pending_run')).not.toBeNull()
  sessionStorage.setItem('dr_workflow_draft_new', 'private draft')
  setApiKey('another-identity')
  expect(sessionStorage.getItem('dr_pending_run')).toBeNull()
  expect(sessionStorage.getItem('dr_workflow_draft_new')).toBeNull()
  expect(localStorage.getItem('dr_api_key')).toBeNull()
})

it('times out an SSE connection only after its heartbeat has been absent for 45 seconds', async () => {
  vi.useFakeTimers()
  let source!: ReadableStreamDefaultController<Uint8Array>
  const cancel = vi.fn()
  const stream = new ReadableStream<Uint8Array>({ start: controller => { source = controller }, cancel })
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(stream))
  const failure = streamRun('run', vi.fn()).catch(error => error)
  await vi.advanceTimersByTimeAsync(44_000)
  source.enqueue(new TextEncoder().encode(': heartbeat\n\n'))
  await vi.advanceTimersByTimeAsync(44_000)
  expect(cancel).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(1000)
  expect(await failure).toBeInstanceOf(RequestTimeoutError)
  expect(cancel).toHaveBeenCalledOnce()
})
