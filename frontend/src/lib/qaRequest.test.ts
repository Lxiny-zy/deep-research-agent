import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import { RequestTimeoutError } from '../api/transport'
import { pendingQaId, reconcileQaRequests, runQaRequest } from './qaRequest'
import { ApiError } from '../api/client'
import type { QaMessage } from '../types'

const mocks = vi.hoisted(() => ({ askQuestion: vi.fn(), getQaRequest: vi.fn() }))
vi.mock('../api/client', async () => ({ ...(await vi.importActual('../api/client')), ...mocks }))
const message: QaMessage = {
  id: 'm1',
  request_id: 'request-one',
  position: 0,
  query: 'same question',
  answer: 'new answer',
  citations: [],
  evidence: [],
  thoughts: [],
  status: 'done',
  created_at: null,
}

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  window.dispatchEvent(new Event('dr:credentials-cleared'))
})
afterEach(() => vi.useRealTimers())

it('retains an unresolved request id and creates a new one after known completion', () => {
  const first = pendingQaId('cid', message.query, { sources: [] })
  expect(pendingQaId('cid', message.query, { sources: [] })).toBe(first)
  reconcileQaRequests('cid', [{ ...message, request_id: first }])
  expect(pendingQaId('cid', message.query, { sources: [] })).not.toBe(first)
})

it('keeps revision identity separate from a new question and from another parent answer', () => {
  const plain = pendingQaId('cid', message.query, { sources: [] })
  const first = pendingQaId('cid', message.query, { sources: [], revisionMessageId: 'm1' })
  expect(first).not.toBe(plain)
  expect(pendingQaId('cid', message.query, { sources: [], revisionMessageId: 'm1' })).toBe(first)
  expect(pendingQaId('cid', message.query, { sources: [], revisionMessageId: 'm2' })).not.toBe(
    first,
  )
})

it('looks up the durable request rather than matching an old identical question', async () => {
  mocks.askQuestion.mockRejectedValueOnce(new RequestTimeoutError())
  mocks.getQaRequest.mockResolvedValue(message)
  const result = await runQaRequest(
    'cid',
    message.query,
    { sources: [] },
    'request-one',
    vi.fn(),
    vi.fn(),
  )
  expect(result.answer).toBe('new answer')
  expect(mocks.getQaRequest).toHaveBeenCalledWith('cid', 'request-one', undefined)
  expect(mocks.askQuestion).toHaveBeenCalledTimes(1)
})

it('reattaches using the same id when the original job is still running', async () => {
  vi.useFakeTimers()
  mocks.askQuestion.mockRejectedValueOnce(new RequestTimeoutError()).mockResolvedValue(message)
  mocks.getQaRequest.mockResolvedValue({ ...message, status: 'running', answer: '' })
  const waiting = runQaRequest(
    'cid',
    message.query,
    { sources: [] },
    'request-one',
    vi.fn(),
    vi.fn(),
  )
  await vi.runAllTimersAsync()
  expect(await waiting).toEqual(message)
  expect(mocks.askQuestion.mock.calls.map((call) => call[3].requestId)).toEqual([
    'request-one',
    'request-one',
  ])
})

it('does not automatically retry a terminal model failure', async () => {
  const failure = new ApiError(502, 'model failed')
  failure.qaTerminal = true
  mocks.askQuestion.mockRejectedValueOnce(failure)
  await expect(
    runQaRequest('cid', 'question', { sources: [] }, 'request-one', vi.fn(), vi.fn()),
  ).rejects.toBe(failure)
  expect(mocks.askQuestion).toHaveBeenCalledTimes(1)
  expect(mocks.getQaRequest).not.toHaveBeenCalled()
})

it('treats durable cancellation as terminal after a disconnected stream', async () => {
  const cancelled = { ...message, status: 'cancelled' as const }
  mocks.askQuestion.mockRejectedValueOnce(new RequestTimeoutError())
  mocks.getQaRequest.mockResolvedValue(cancelled)
  expect(
    await runQaRequest('cid', 'question', { sources: [] }, 'request-one', vi.fn(), vi.fn()),
  ).toEqual(cancelled)
  expect(mocks.askQuestion).toHaveBeenCalledTimes(1)
})

it('does not automatically retry an invalid saved recovery scope', async () => {
  const failure = new ApiError(409, 'qa_recovery_unavailable')
  mocks.askQuestion.mockRejectedValueOnce(failure)
  await expect(
    runQaRequest(
      'cid',
      'question',
      { sources: ['web'], resumeMessageId: 'stopped-message' },
      'new-request',
      vi.fn(),
      vi.fn(),
    ),
  ).rejects.toBe(failure)
  expect(mocks.askQuestion).toHaveBeenCalledTimes(1)
  expect(mocks.getQaRequest).not.toHaveBeenCalled()
})

it('does not discard another pending request when an older one finishes', async () => {
  pendingQaId('cid', 'first', { sources: [] }, 'first-request')
  pendingQaId('cid', 'second', { sources: [] }, 'second-request')
  mocks.askQuestion.mockResolvedValue(message)
  await runQaRequest('cid', 'first', { sources: [] }, 'first-request', vi.fn(), vi.fn())
  expect(pendingQaId('cid', 'second', { sources: [] })).toBe('second-request')
})

it('retains the request and stops recovery when the connection is aborted during lookup', async () => {
  const controller = new AbortController()
  pendingQaId('cid', 'question', { sources: [] }, 'request-one')
  mocks.askQuestion.mockRejectedValueOnce(new RequestTimeoutError())
  mocks.getQaRequest.mockImplementationOnce(() => {
    controller.abort()
    return Promise.reject(controller.signal.reason)
  })
  await expect(
    runQaRequest(
      'cid',
      'question',
      { sources: [] },
      'request-one',
      vi.fn(),
      vi.fn(),
      controller.signal,
    ),
  ).rejects.toMatchObject({ name: 'AbortError' })
  expect(mocks.askQuestion).toHaveBeenCalledTimes(1)
  expect(pendingQaId('cid', 'question', { sources: [] })).toBe('request-one')
})
