import {
  createRenderOperation,
  getApiKey,
  getRenderOperation,
  type RenderOperationReceipt,
  type RenderOperationRequest,
} from '../api/client'

export interface PendingRender {
  request: RenderOperationRequest
  receipt?: RenderOperationReceipt
}
const memory = new Map<string, PendingRender>()

export async function renderStorageKey(runId: string, channel: string) {
  const digest = await crypto.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(getApiKey() ?? 'anonymous'),
  )
  const identity = Array.from(new Uint8Array(digest), (n) => n.toString(16).padStart(2, '0')).join(
    '',
  )
  return `dr_render:${identity}:${encodeURIComponent(runId)}:${channel}`
}

export function readRender(key: string): PendingRender | undefined {
  try {
    const value = JSON.parse(localStorage.getItem(key) ?? 'null')
    if (
      value?.request &&
      typeof value.request.request_id === 'string' &&
      ['bundle', 'retry', 'export'].includes(value.request.kind)
    )
      return value
  } catch {
    /* Memory remains usable when browser storage is unavailable. */
  }
  return memory.get(key)
}

function saveRender(key: string, value: PendingRender) {
  memory.set(key, value)
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* Optional storage. */
  }
}

export function clearRender(key: string) {
  memory.delete(key)
  try {
    localStorage.removeItem(key)
  } catch {
    /* Optional storage. */
  }
}

export async function executeRender(
  runId: string,
  key: string,
  request: Omit<RenderOperationRequest, 'request_id'>,
  signal?: AbortSignal,
  onReceipt?: (receipt: RenderOperationReceipt) => void,
) {
  const saved = readRender(key)
  const pending = saved ?? { request: { ...request, request_id: crypto.randomUUID() } }
  if (
    saved &&
    JSON.stringify({ ...saved.request, request_id: undefined }) !==
      JSON.stringify({ ...request, request_id: undefined })
  )
    throw new Error('上一次渲染操作尚未确认，请先恢复该操作。')
  saveRender(key, pending)
  let receipt: RenderOperationReceipt
  try {
    receipt = pending.receipt
      ? await getRenderOperation(
          runId,
          pending.receipt.operation_id || pending.receipt.id,
          signal,
          pending.request.request_id,
        )
      : await createRenderOperation(runId, pending.request, signal)
  } catch (cause) {
    // A rejected new submission did not enqueue work. Transport failures retain identity.
    if (
      !pending.receipt &&
      cause instanceof Error &&
      'status' in cause &&
      [400, 409, 422].includes(Number(cause.status))
    )
      clearRender(key)
    throw cause
  }
  for (;;) {
    if (
      receipt.run_id !== runId ||
      receipt.request_id !== pending.request.request_id ||
      receipt.kind !== pending.request.kind
    )
      throw new Error('渲染回执与当前请求不匹配。')
    saveRender(key, { ...pending, receipt })
    onReceipt?.(receipt)
    if (['done', 'completed', 'succeeded'].includes(receipt.status)) return receipt
    if (['failed', 'cancelled', 'error'].includes(receipt.status)) {
      throw new Error(
        typeof receipt.error === 'string' ? receipt.error : '渲染未完成，可重新提交操作。',
      )
    }
    if (!['pending', 'queued', 'running'].includes(receipt.status))
      throw new Error(`未知渲染状态：${receipt.status}。已保留回执。`)
    await new Promise<void>((resolve, reject) => {
      const abort = () => {
        clearTimeout(timer)
        reject(new DOMException('Aborted', 'AbortError'))
      }
      const timer = setTimeout(() => {
        signal?.removeEventListener('abort', abort)
        resolve()
      }, 1000)
      if (signal?.aborted) abort()
      else signal?.addEventListener('abort', abort, { once: true })
    })
    receipt = await getRenderOperation(
      runId,
      receipt.operation_id || receipt.id,
      signal,
      pending.request.request_id,
    )
  }
}
