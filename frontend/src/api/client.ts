// 类型化的 HTTP 客户端：统一错误处理，所有路径走 Vite proxy / 同源后端。
import type {
  AgentCard,
  AgentCardInput,
  AssessRequest,
  AssessResponse,
  Behavior,
  CancelRunResponse,
  ConfigUpdate,
  ConfigView,
  CreateRunRequest,
  CreateRunResponse,
  ModelProfile,
  ModelProfileInput,
  ModelProbeInput,
  ModelDiscoveryResult,
  RoleInfo,
  RunDetail,
  RunSummary,
  SearchKey,
  SearchKeyInput,
  TagCount,
  TestResult,
  WorkflowDef,
  WorkflowDefInput,
  WorkflowInfo,
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
  // 业务错误的对象形态（如 needs_clarification）：不认对象的话，
  // 用户只会看到一句「Unprocessable Entity」，完全不知道该怎么办。
  if (detail && typeof detail === 'object') {
    const obj = detail as { message?: unknown; question?: unknown }
    const message = typeof obj.message === 'string' ? obj.message : ''
    const question = typeof obj.question === 'string' ? obj.question : ''
    const combined = [message, question].filter(Boolean).join('：')
    if (combined) return combined
  }
  return fallback
}

const API_KEY_STORAGE = 'dr_api_key'

// API keys are tab-scoped in sessionStorage and cleared when the tab closes.
export function getApiKey(): string | null {
  try {
    return sessionStorage.getItem(API_KEY_STORAGE)
  } catch {
    return null
  }
}

export function setApiKey(key: string): void {
  try {
    sessionStorage.setItem(API_KEY_STORAGE, key)
  } catch {
    // sessionStorage 不可用（隐私模式等）：忽略
  }
}

export function clearApiKey(): void {
  try {
    sessionStorage.removeItem(API_KEY_STORAGE)
  } catch {
    // 忽略
  }
}

// 收到 401 时广播：App 监听后弹出密钥登录。仅浏览器环境派发。
function signalUnauthorized(): void {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event('dr:unauthorized'))
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const key = getApiKey()
  const res = await fetch(url, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(key ? { Authorization: `Bearer ${key}` } : {}),
      ...(init?.headers ?? {}),
    },
  })
  if (!res.ok) {
    if (res.status === 401) signalUnauthorized()
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
  const key = getApiKey()
  const res = await fetch(url, {
    ...init,
    headers: {
      ...(key ? { Authorization: `Bearer ${key}` } : {}),
      ...(init?.headers ?? {}),
    },
  })
  if (!res.ok) {
    if (res.status === 401) signalUnauthorized()
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
  const idempotencyKey =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`
  return request<CreateRunResponse>('/api/runs', {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify(body),
  })
}

/** 建 run 之前判断信息够不够。不够时返回追问与候选项，且**不会创建任何 run**。 */
export function assessIntent(body: AssessRequest): Promise<AssessResponse> {
  return request<AssessResponse>('/api/intent/assess', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function listWorkflows(): Promise<WorkflowInfo[]> {
  return request<WorkflowInfo[]>('/api/workflows')
}

// ── 自定义工作流（构建器）──────────────────────────────────────────────
export function listRoles(): Promise<RoleInfo[]> {
  return request<RoleInfo[]>('/api/roles')
}

export function listCustomWorkflows(): Promise<WorkflowDef[]> {
  return request<WorkflowDef[]>('/api/workflows/custom')
}

export function createCustomWorkflow(body: WorkflowDefInput): Promise<WorkflowDef> {
  return request<WorkflowDef>('/api/workflows/custom', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function updateCustomWorkflow(id: string, body: WorkflowDefInput): Promise<WorkflowDef> {
  return request<WorkflowDef>(`/api/workflows/custom/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function deleteCustomWorkflow(id: string): Promise<void> {
  return requestVoid(`/api/workflows/custom/${encodeURIComponent(id)}`, { method: 'DELETE' })
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

export function cancelRun(id: string): Promise<CancelRunResponse> {
  return request<CancelRunResponse>(`/api/runs/${encodeURIComponent(id)}/cancel`, {
    method: 'POST',
  })
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

export async function streamRun(
  id: string,
  onMessage: (data: string, eventId?: string) => void,
  signal?: AbortSignal,
  lastEventId?: string,
): Promise<void> {
  const key = getApiKey()
  const res = await fetch(`/api/runs/${encodeURIComponent(id)}/stream`, {
    headers: {
      Accept: 'text/event-stream',
      ...(key ? { Authorization: `Bearer ${key}` } : {}),
      ...(lastEventId ? { 'Last-Event-ID': lastEventId } : {}),
    },
    signal,
  })
  if (!res.ok) {
    if (res.status === 401) signalUnauthorized()
    throw new ApiError(res.status, res.statusText)
  }
  if (!res.body) throw new ApiError(0, 'SSE response has no body')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const dispatchCompleteEvents = () => {
    let boundary = buffer.match(/\r?\n\r?\n/)
    while (boundary?.index != null) {
      const block = buffer.slice(0, boundary.index)
      buffer = buffer.slice(boundary.index + boundary[0].length)
      const id = block
        .split(/\r?\n/)
        .find((line) => line.startsWith('id:'))
        ?.slice(3)
        .trim()
      const data = block
        .split(/\r?\n/)
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).replace(/^ /, ''))
        .join('\n')
      if (data) onMessage(data, id || undefined)
      boundary = buffer.match(/\r?\n\r?\n/)
    }
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    dispatchCompleteEvents()
  }
  buffer += decoder.decode()
  dispatchCompleteEvents()
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

// ── 角色广场 catalog ──────────────────────────────────────────────────
export function listBehaviors(): Promise<Behavior[]> {
  return request<Behavior[]>('/api/behaviors')
}

export function listModels(): Promise<ModelProfile[]> {
  return request<ModelProfile[]>('/api/models')
}

export function createModel(body: ModelProfileInput): Promise<ModelProfile> {
  return request<ModelProfile>('/api/models', { method: 'POST', body: JSON.stringify(body) })
}

export function updateModel(id: string, body: ModelProfileInput): Promise<ModelProfile> {
  return request<ModelProfile>(`/api/models/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function deleteModel(id: string): Promise<void> {
  return requestVoid(`/api/models/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export function testModel(id: string): Promise<TestResult> {
  return request<TestResult>(`/api/models/${encodeURIComponent(id)}/test`, { method: 'POST' })
}

export function testModelConfig(body: ModelProbeInput): Promise<TestResult> {
  return request<TestResult>('/api/models/test-config', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function discoverModels(body: ModelProbeInput): Promise<ModelDiscoveryResult> {
  return request<ModelDiscoveryResult>('/api/models/discover', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function listAgents(): Promise<AgentCard[]> {
  return request<AgentCard[]>('/api/agents')
}

export function createAgent(body: AgentCardInput): Promise<AgentCard> {
  return request<AgentCard>('/api/agents', { method: 'POST', body: JSON.stringify(body) })
}

export function updateAgent(id: string, body: AgentCardInput): Promise<AgentCard> {
  return request<AgentCard>(`/api/agents/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function deleteAgent(id: string): Promise<void> {
  return requestVoid(`/api/agents/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export function listSearchKeys(): Promise<SearchKey[]> {
  return request<SearchKey[]>('/api/search-keys')
}

export function createSearchKey(body: SearchKeyInput): Promise<SearchKey> {
  return request<SearchKey>('/api/search-keys', { method: 'POST', body: JSON.stringify(body) })
}

export function updateSearchKey(id: string, body: SearchKeyInput): Promise<SearchKey> {
  return request<SearchKey>(`/api/search-keys/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function deleteSearchKey(id: string): Promise<void> {
  return requestVoid(`/api/search-keys/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export function testSearchKey(id: string): Promise<TestResult> {
  return request<TestResult>(`/api/search-keys/${encodeURIComponent(id)}/test`, { method: 'POST' })
}
