import { useCallback, useEffect, useRef, useState } from 'react'
import type { RenderOperationReceipt, RenderOperationRequest } from '../api/client'
import { clearRender, executeRender, readRender, renderStorageKey } from '../lib/renderOperation'

export function useRenderOperation(
  runId: string | undefined,
  channel: string,
  onComplete: (receipt: RenderOperationReceipt, request: RenderOperationRequest) => Promise<void>,
) {
  const [busy, setBusy] = useState(false)
  const [receipt, setReceipt] = useState<RenderOperationReceipt>()
  const [error, setError] = useState<string | null>(null)
  const callback = useRef(onComplete)
  callback.current = onComplete
  const active = useRef<AbortController>()
  const perform = useCallback(
    async (request?: Omit<RenderOperationRequest, 'request_id'>) => {
      if (!runId || active.current) return
      const controller = new AbortController()
      active.current = controller
      const complete = callback.current
      try {
        const key = await renderStorageKey(runId, channel)
        controller.signal.throwIfAborted()
        const saved = readRender(key)
        if (
          request &&
          saved?.receipt &&
          ['failed', 'cancelled', 'error'].includes(saved.receipt.status)
        ) {
          clearRender(key)
        }
        const body = request ?? saved?.request
        if (!body) return
        setBusy(true)
        setError(null)
        const result = await executeRender(runId, key, body, controller.signal, setReceipt)
        if (controller.signal.aborted) return
        await complete(
          result,
          readRender(key)?.request ?? { ...body, request_id: result.request_id },
        )
        clearRender(key)
      } catch (cause) {
        if (!controller.signal.aborted)
          setError(
            cause instanceof Error ? cause.message : '渲染连接中断；操作回执已保留，可重新连接。',
          )
      } finally {
        if (active.current === controller) {
          active.current = undefined
          setBusy(false)
        }
      }
    },
    [runId, channel],
  )
  useEffect(() => {
    let disposed = false
    setBusy(false)
    setReceipt(undefined)
    setError(null)
    if (runId)
      void renderStorageKey(runId, channel)
        .then((key) => {
          if (!disposed && readRender(key)) void perform()
        })
        .catch((cause: unknown) => {
          if (!disposed) setError(cause instanceof Error ? cause.message : '无法恢复渲染操作。')
        })
    const reconnect = () => void perform()
    const credentialsCleared = () => {
      active.current?.abort()
      active.current = undefined
      setBusy(false)
      setReceipt(undefined)
    }
    window.addEventListener('online', reconnect)
    window.addEventListener('dr:credentials-cleared', credentialsCleared)
    return () => {
      disposed = true
      active.current?.abort()
      active.current = undefined
      window.removeEventListener('online', reconnect)
      window.removeEventListener('dr:credentials-cleared', credentialsCleared)
    }
  }, [perform, runId, channel])
  return { busy, receipt, error, start: perform, reconnect: () => perform() }
}
