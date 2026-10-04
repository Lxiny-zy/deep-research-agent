import { ApiError, askQuestion, getQaRequest } from '../api/client'
import { canRecoverQaAnswer } from './qaRecovery'
import type { QaActivity, QaMessage, QaSourceOption } from '../types'

export type QaScope = { sources: QaSourceOption[]; projectId?: string; revisionMessageId?: string }
const memory = new Map<string, { query: string; scope: string; id: string }>()
if (typeof window !== 'undefined')
  window.addEventListener('dr:credentials-cleared', () => memory.clear())

function clearPending(cid: string, requestId: string) {
  let current = memory.get(cid)
  try {
    current ??= JSON.parse(sessionStorage.getItem(`dr_pending_qa_${cid}`) ?? 'null')
  } catch {
    /* Use memory. */
  }
  if (current?.id !== requestId) return
  memory.delete(cid)
  try {
    sessionStorage.removeItem(`dr_pending_qa_${cid}`)
  } catch {
    /* Storage is optional. */
  }
}

export function pendingConversationId(ids: string[]): string | undefined {
  return ids.find((cid) => {
    try {
      return memory.has(cid) || Boolean(sessionStorage.getItem(`dr_pending_qa_${cid}`))
    } catch {
      return memory.has(cid)
    }
  })
}

export function reconcileQaRequests(cid: string, messages: QaMessage[]) {
  for (const message of messages) {
    if (message.request_id && ['done', 'fallback', 'error'].includes(message.status)) {
      clearPending(cid, message.request_id)
    }
  }
}

export function pendingQaId(cid: string, query: string, scope: QaScope, resumed?: string): string {
  const signature = JSON.stringify([
    Array.from(new Set(scope.sources)).sort(),
    scope.projectId ?? '',
    ...(scope.revisionMessageId ? [scope.revisionMessageId] : []),
  ])
  let previous = memory.get(cid)
  try {
    previous ??= JSON.parse(sessionStorage.getItem(`dr_pending_qa_${cid}`) ?? 'null')
  } catch {
    /* Use memory. */
  }
  const previousId =
    previous &&
    typeof previous.id === 'string' &&
    /^[A-Za-z0-9_-]{8,64}$/.test(previous.id) &&
    previous.query === query &&
    previous.scope === signature
      ? previous.id
      : undefined
  const id = resumed ?? previousId ?? crypto.randomUUID()
  const value = { query, scope: signature, id }
  memory.set(cid, value)
  try {
    sessionStorage.setItem(`dr_pending_qa_${cid}`, JSON.stringify(value))
  } catch {
    /* Use memory. */
  }
  return id
}

/** Reconnect to the SAME durable request; never infer completion from question text. */
export async function runQaRequest(
  cid: string,
  query: string,
  scope: QaScope,
  requestId: string,
  onDelta: (text: string) => void,
  onActivity: (activity: QaActivity) => void,
  signal?: AbortSignal,
): Promise<QaMessage> {
  let lastError: unknown
  for (let attempt = 0; attempt < 4; attempt += 1) {
    signal?.throwIfAborted()
    try {
      const message = await askQuestion(
        cid,
        query,
        signal,
        { ...scope, requestId },
        onDelta,
        onActivity,
      )
      signal?.throwIfAborted()
      clearPending(cid, requestId)
      return message
    } catch (error) {
      signal?.throwIfAborted()
      if (
        error instanceof ApiError &&
        (error.qaTerminal || (error.status >= 400 && error.status < 500))
      ) {
        clearPending(cid, requestId)
        throw error
      }
      if (!canRecoverQaAnswer(error) && !(error instanceof ApiError && error.status >= 500))
        throw error
      lastError = error
      onActivity({ type: 'status', message: '连接中断，正在查询原任务并恢复连接…' })
      try {
        const current = await getQaRequest(cid, requestId, signal)
        signal?.throwIfAborted()
        if (current.status === 'done' || current.status === 'fallback') {
          clearPending(cid, requestId)
          return current
        }
        if (current.status === 'error') {
          clearPending(cid, requestId)
          const failure = new ApiError(502, current.error || '本轮生成失败')
          failure.qaTerminal = true
          throw failure
        }
      } catch (lookupError) {
        signal?.throwIfAborted()
        if (
          lookupError instanceof ApiError &&
          (lookupError.qaTerminal || [401, 403].includes(lookupError.status))
        )
          throw lookupError
        // A lost POST may never have arrived. Reposting the same id is safe.
      }
      if (attempt < 3)
        await new Promise((resolve) => setTimeout(resolve, Math.min(4000, 500 * 2 ** attempt)))
    }
  }
  // Retain the id for explicit reconnect or page reload; do not create a new job.
  throw lastError
}
