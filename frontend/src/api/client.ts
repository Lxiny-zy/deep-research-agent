// 类型化的 HTTP 客户端：统一错误处理，所有路径走 Vite proxy / 同源后端。
import type {
  ConfigUpdate,
  ConfigView,
  CreateRunRequest,
  CreateRunResponse,
  RunDetail,
  RunSummary,
  TagCount,
} from '../types'

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

// FastAPI 校验失败（422）时 detail 是对象数组；业务错误是字符串
interface ValidationItem {
  loc?: (string | number)[]
  msg?: string
}

function formatDetail(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const parts = (detail as ValidationItem[])
      .map((d) => {
        const loc = Array.isArray(d.loc) ? d.loc.filter((x) => x !== 'body').join('.') : ''
        return loc && d.msg ? `${loc}: ${d.msg}` : (d.msg ?? '')
      })
      .filter(Boolean)
    if (parts.length > 0) return parts.join('；')
  }
  return fallback
}

// 后端启用 API_KEY 认证时，在浏览器控制台执行
// localStorage.setItem('dr_api_key', '<你的密钥>') 后刷新即可。
function apiKey(): string | null {
  try {
    return localStorage.getItem('dr_api_key')
  } catch {
    return null
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const key = apiKey()
  const res = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
      ...(key ? { 'X-API-Key': key } : {}),
    },
    ...init,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = (await res.json()) as { detail?: unknown }
      if (body.detail != null) detail = formatDetail(body.detail, res.statusText)
    } catch {
      // 错误体非 JSON，沿用 statusText
    }
    throw new ApiError(res.status, detail)
  }
  return (await res.json()) as T
}

// 用于 204 No Content（如 DELETE）：仅校验状态，不解析响应体
async function requestVoid(url: string, init?: RequestInit): Promise<void> {
  const key = apiKey()
  const res = await fetch(url, {
    headers: { ...(key ? { 'X-API-Key': key } : {}) },
    ...init,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = (await res.json()) as { detail?: unknown }
      if (body.detail != null) detail = formatDetail(body.detail, res.statusText)
    } catch {
      // 无响应体或非 JSON
    }
    throw new ApiError(res.status, detail)
  }
}

export function createRun(body: CreateRunRequest): Promise<CreateRunResponse> {
  return request<CreateRunResponse>('/api/runs', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function listRuns(
  params: {
    limit?: number
    offset?: number
    status?: string
    q?: string
    tag?: string
  } = {},
): Promise<RunSummary[]> {
  const query = new URLSearchParams()
  if (params.limit != null) query.set('limit', String(params.limit))
  if (params.offset != null) query.set('offset', String(params.offset))
  if (params.status) query.set('status', params.status)
  if (params.q) query.set('q', params.q)
  if (params.tag) query.set('tag', params.tag)
  const qs = query.toString()
  return request<RunSummary[]>(`/api/runs${qs ? `?${qs}` : ''}`)
}

export function getRun(id: string): Promise<RunDetail> {
  return request<RunDetail>(`/api/runs/${encodeURIComponent(id)}`)
}

export function deleteRun(id: string): Promise<void> {
  return requestVoid(`/api/runs/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export function batchDeleteRuns(ids: string[]): Promise<{ deleted: number; skipped: number }> {
  return request<{ deleted: number; skipped: number }>('/api/runs/batch_delete', {
    method: 'POST',
    body: JSON.stringify({ ids }),
  })
}

export function setTags(id: string, tags: string[]): Promise<RunDetail> {
  return request<RunDetail>(`/api/runs/${encodeURIComponent(id)}/tags`, {
    method: 'PUT',
    body: JSON.stringify({ tags }),
  })
}

export function listTags(): Promise<TagCount[]> {
  return request<TagCount[]>('/api/tags')
}

export function streamUrl(id: string): string {
  const base = `/api/runs/${encodeURIComponent(id)}/stream`
  const key = apiKey()
  // EventSource 无法自定义请求头，认证走查询参数
  return key ? `${base}?api_key=${encodeURIComponent(key)}` : base
}

export function getConfig(): Promise<ConfigView> {
  return request<ConfigView>('/api/config')
}

export function updateConfig(body: ConfigUpdate): Promise<ConfigView> {
  return request<ConfigView>('/api/config', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}
