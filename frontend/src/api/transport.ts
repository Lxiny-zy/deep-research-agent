/** A deadline covers both response headers and body consumption. SSE uses its own idle timer. */
export class RequestTimeoutError extends Error {
  constructor() {
    super('请求超时，请检查连接后重试。')
    this.name = 'RequestTimeoutError'
  }
}

export async function withResponse<T>(
  url: string,
  init: RequestInit | undefined,
  consume: (response: Response) => Promise<T>,
  timeoutMs = 30_000,
): Promise<T> {
  const controller = new AbortController()
  const cancel = () => controller.abort(init?.signal?.reason)
  let timedOut = false
  let rejectAbort: () => void = () => {}
  const aborted = new Promise<never>((_, reject) => {
    rejectAbort = () => reject(controller.signal.reason)
    controller.signal.addEventListener('abort', rejectAbort, { once: true })
  })
  if (init?.signal?.aborted) cancel()
  init?.signal?.addEventListener('abort', cancel, { once: true })
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)
  try {
    return await Promise.race([
      (async () => {
        controller.signal.throwIfAborted()
        return consume(await fetch(url, { ...init, signal: controller.signal }))
      })(),
      aborted,
    ])
  } catch (error) {
    if (timedOut) throw new RequestTimeoutError()
    throw error
  } finally {
    clearTimeout(timer)
    init?.signal?.removeEventListener('abort', cancel)
    controller.signal.removeEventListener('abort', rejectAbort)
  }
}

export function verifyAccessKey(key: string | null, signal?: AbortSignal): Promise<void> {
  return withResponse(
    '/api/config',
    {
      headers: key ? { Authorization: `Bearer ${key}` } : {},
      signal,
    },
    async (response) => {
      if (!response.ok) {
        const error = new Error(
          response.status === 401
            ? '访问密钥无效，请检查后重试。'
            : `无法加载服务配置（HTTP ${response.status}）`,
        )
        Object.assign(error, { status: response.status })
        throw error
      }
      await response.text()
    },
  )
}
