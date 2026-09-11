// 类型化的 HTTP 客户端：统一错误处理，所有路径走 Vite proxy / 同源后端。
import type {
  ResourcePreflight,
  SearchResourceImpact,
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
  ReportDocument,
  RunDetail,
  RunSummary,
  SearchKey,
  SearchKeyInput,
  SearchProfile,
  SearchProfileInput,
  PromptPreview,
  TagCount,
  TestResult,
  WorkflowDef,
  WorkflowDefInput,
  WorkflowInfo,
} from '../types'

export const checkResourcePreflight = (workflow: string, signal?: AbortSignal) =>
  request<ResourcePreflight>(`/api/resource-preflight?workflow=${encodeURIComponent(workflow)}`, {
    signal,
  })

export const getSearchResourceImpact = () =>
  request<SearchResourceImpact>('/api/search-resources/impact')
import { normalizeReportDocument } from '../lib/reportDocument'
import { withResponse } from './transport'
import { RequestTimeoutError } from './transport'

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
let memoryApiKey: string | null = null

// Existing tab sessions stay valid; remembered credentials survive reopening the browser.
export function getApiKey(): string | null {
  for (const storage of ['sessionStorage', 'localStorage'] as const) {
    try {
      const key = window[storage].getItem(API_KEY_STORAGE)
      if (key) return key
    } catch {
      // One storage may be blocked while the other remains available.
    }
  }
  return memoryApiKey
}

export function isApiKeyRemembered(): boolean {
  return getApiKeyStorage() === 'local'
}

export function getApiKeyStorage(): 'local' | 'session' | 'memory' | 'none' {
  for (const storage of ['sessionStorage', 'localStorage'] as const) {
    try {
      if (window[storage].getItem(API_KEY_STORAGE))
        return storage === 'localStorage' ? 'local' : 'session'
    } catch {
      /* Try the remaining storage. */
    }
  }
  return memoryApiKey ? 'memory' : 'none'
}

export function setApiKey(key: string, remember = false): 'local' | 'session' | 'memory' {
  clearApiKey()
  const value = key.trim()
  if (!value) return 'memory'
  const destinations = remember
    ? (['localStorage', 'sessionStorage'] as const)
    : (['sessionStorage'] as const)
  for (const storage of destinations) {
    try {
      window[storage].setItem(API_KEY_STORAGE, value)
      return storage === 'localStorage' ? 'local' : 'session'
    } catch {
      // Retain a usable login even when browser storage is disabled.
    }
  }
  memoryApiKey = value
  return 'memory'
}

export function clearApiKey(): void {
  memoryApiKey = null
  for (const storage of ['localStorage', 'sessionStorage'] as const) {
    try { window[storage].removeItem(API_KEY_STORAGE) } catch { /* Storage is optional. */ }
  }
  clearWorkspaceState()
}

export function clearWorkspaceState(): void {
  if (typeof window !== 'undefined') window.dispatchEvent(new Event('dr:credentials-cleared'))
  pendingSubmission = null
  try {
    window.sessionStorage.removeItem('dr_pending_run')
  } catch {
    /* Storage is optional. */
  }
  for (const storage of ['localStorage', 'sessionStorage'] as const) {
    try {
      for (const name of Object.keys(window[storage])) {
        if (/^dr_.*(?:draft|thread|pending_run)/.test(name)) window[storage].removeItem(name)
      }
    } catch {
      // Storage access can be disabled by browser policy.
    }
  }
}

// 收到 401 时广播：App 监听后弹出密钥登录。仅浏览器环境派发。
function signalUnauthorized(rejectedKey: string | null): void {
  // A late response from a previous login must not discard a newly verified key.
  if (typeof window !== 'undefined' && rejectedKey === getApiKey()) {
    window.dispatchEvent(new Event('dr:unauthorized'))
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const key = getApiKey()
  const headers = new Headers(init?.headers)
  if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  if (key && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${key}`)
  return withResponse(
    url,
    {
      ...init,
      headers,
    },
    async (res) => {
      if (!res.ok) {
        if (res.status === 401) signalUnauthorized(key)
        let detail = res.statusText || `请求失败（HTTP ${res.status}）`
        try {
          const body = (await res.json()) as { detail?: unknown }
          if (body.detail != null) detail = formatDetail(body.detail, detail)
        } catch {
          // 错误体非 JSON，沿用 statusText
        }
        throw new ApiError(res.status, detail)
      }
      if (res.status === 204) return undefined as T
      return (await res.json()) as T
    },
  )
}

// 用于 204 No Content（如 DELETE）：仅校验状态，不解析响应体
async function requestVoid(url: string, init?: RequestInit): Promise<void> {
  await request<void>(url, init)
}

interface PendingSubmission {
  body: string
  key: string
  identity?: string
}
let pendingSubmission: PendingSubmission | null = null

function submissionFor(body: CreateRunRequest, identity?: string): PendingSubmission {
  const serialized = JSON.stringify(body)
  const logicalIdentity = identity ?? serialized
  try {
    const saved = JSON.parse(
      window.sessionStorage.getItem('dr_pending_run') ?? 'null',
    ) as PendingSubmission | null
    if (
      (saved?.identity ?? saved?.body) === logicalIdentity &&
      typeof saved?.key === 'string' && typeof saved?.body === 'string'
    )
      return saved
  } catch {
    /* Private browsing can disable storage. */
  }
  if (pendingSubmission?.identity === logicalIdentity) return pendingSubmission
  const key =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`
  pendingSubmission = { body: serialized, key, identity: logicalIdentity }
  try {
    window.sessionStorage.setItem('dr_pending_run', JSON.stringify(pendingSubmission))
  } catch {
    /* Retain in memory. */
  }
  return pendingSubmission
}

export async function createRun(
  body: CreateRunRequest,
  signal?: AbortSignal,
  logicalIdentity?: string,
): Promise<CreateRunResponse> {
  const submission = submissionFor(body, logicalIdentity)
  try {
    const result = await request<CreateRunResponse>('/api/runs', {
      method: 'POST',
      headers: { 'Idempotency-Key': submission.key },
      body: submission.body,
      signal,
    })
    forgetSubmission(submission)
    return result
  } catch (error) {
    // A rejected request can be edited. An uncertain outcome must reuse its original body/key.
    if (error instanceof ApiError && [400, 401, 403, 404, 409, 413, 422].includes(error.status))
      forgetSubmission(submission)
    throw error
  }
}

function forgetSubmission(submission: PendingSubmission): void {
  if (pendingSubmission?.key === submission.key) pendingSubmission = null
  try {
    const saved = JSON.parse(
      window.sessionStorage.getItem('dr_pending_run') ?? 'null',
    ) as PendingSubmission | null
    if (saved?.key === submission.key) window.sessionStorage.removeItem('dr_pending_run')
  } catch {
    /* Storage is optional. */
  }
}

/** 建 run 之前判断信息够不够。不够时返回追问与候选项，且**不会创建任何 run**。 */
export function assessIntent(body: AssessRequest, signal?: AbortSignal): Promise<AssessResponse> {
  return request<AssessResponse>('/api/intent/assess', {
    method: 'POST',
    body: JSON.stringify(body),
    signal,
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
  signal?: AbortSignal,
): Promise<RunSummary[]> {
  const query = new URLSearchParams()
  if (params.limit != null) query.set('limit', String(params.limit))
  if (params.offset != null) query.set('offset', String(params.offset))
  if (params.status) query.set('status', params.status)
  if (params.q) query.set('q', params.q)
  if (params.tag) query.set('tag', params.tag)
  const qs = query.toString()
  return request<RunSummary[]>(`/api/runs${qs ? `?${qs}` : ''}`, { signal })
}

export function getRun(id: string, signal?: AbortSignal): Promise<RunDetail> {
  return request<RunDetail>(`/api/runs/${encodeURIComponent(id)}`, { signal })
}

export interface GetRunDocumentOptions {
  includeHsiTables?: boolean
  signal?: AbortSignal
}

/** Fetch the server-owned structured report for a persisted run. */
export function getRunDocument(
  id: string,
  options: GetRunDocumentOptions = {},
): Promise<ReportDocument> {
  const query = new URLSearchParams()
  if (options.includeHsiTables) query.set('include_hsi_tables', 'true')
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return request<unknown>(`/api/runs/${encodeURIComponent(id)}/document${suffix}`, {
    signal: options.signal,
  }).then((payload) => {
    const document = normalizeReportDocument(payload)
    if (!document) throw new Error('Invalid structured report response')
    return document
  })
}

export type RunDocumentFormat = 'md' | 'csv' | 'xlsx' | 'pdf'

export interface RunDocumentExportOptions {
  includeHsiTables?: boolean
  tableId?: string
  signal?: AbortSignal
}

export interface RunDocumentDownload {
  blob: Blob
  filename: string
}

function downloadFilename(header: string | null, fallback: string): string {
  if (!header) return fallback
  const encoded = header.match(/filename\*=(?:UTF-8'')?([^;]+)/i)?.[1]
  const quoted = header.match(/filename="([^"]+)"/i)?.[1]
  const raw = encoded ?? quoted ?? header.match(/filename=([^;]+)/i)?.[1]
  if (!raw) return fallback
  let value = raw.trim().replace(/^"|"$/g, '')
  if (encoded) {
    try {
      value = decodeURIComponent(value)
    } catch {
      // Keep the original header value when a server sends malformed escapes.
    }
  }
  const safe = value
    .replace(/[\\/:*?"<>|\r\n]+/g, '_')
    .replace(/^\.+/, '')
    .trim()
  return safe || fallback
}

/** Download a server-rendered report export without exposing the API key in a URL. */
export async function downloadRunDocument(
  id: string,
  format: RunDocumentFormat,
  options: RunDocumentExportOptions = {},
): Promise<RunDocumentDownload> {
  const query = new URLSearchParams()
  if (options.includeHsiTables) query.set('include_hsi_tables', 'true')
  // 只有单表导出（CSV/XLSX）需要选表。md/pdf 渲染的是整份文档，给它们带
  // table_id 会让 URL 声明一个该端点并不遵守的约束。用白名单而不是排除法，
  // 这样将来新增格式默认不带，而不是默认带上。
  if (options.tableId && (format === 'csv' || format === 'xlsx')) {
    query.set('table_id', options.tableId)
  }
  const suffix = query.toString() ? `?${query.toString()}` : ''
  const encodedId = encodeURIComponent(id)
  const fallback = `research-${id.replace(/[^A-Za-z0-9._-]/g, '_') || 'run'}.${format}`
  const key = getApiKey()
  return withResponse(
    `/api/runs/${encodedId}/document.${format}${suffix}`,
    {
      headers: {
        Accept: 'application/octet-stream',
        ...(key ? { Authorization: `Bearer ${key}` } : {}),
      },
      signal: options.signal,
    },
    async (res) => {
      if (!res.ok) {
        if (res.status === 401) signalUnauthorized(key)
        let detail = res.statusText
        try {
          const body = (await res.json()) as { detail?: unknown }
          if (body.detail != null) detail = formatDetail(body.detail, res.statusText)
        } catch {
          // Error responses are allowed to be non-JSON; retain statusText.
        }
        throw new ApiError(res.status, detail)
      }
      return {
        blob: await res.blob(),
        filename: downloadFilename(res.headers.get('Content-Disposition'), fallback),
      }
    },
    120_000,
  )
}

export function deleteRun(id: string): Promise<void> {
  return requestVoid(`/api/runs/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export function cancelRun(id: string): Promise<CancelRunResponse> {
  return request<CancelRunResponse>(`/api/runs/${encodeURIComponent(id)}/cancel`, {
    method: 'POST',
  })
}

export function resumeRun(id: string): Promise<CreateRunResponse> {
  return request<CreateRunResponse>(`/api/runs/${encodeURIComponent(id)}/resume`, {
    method: 'POST',
  })
}

export function batchDeleteRuns(
  ids: string[],
): Promise<{ deleted: number; skipped: number; deleted_ids?: string[] }> {
  return request<{ deleted: number; skipped: number; deleted_ids?: string[] }>(
    '/api/runs/batch_delete',
    {
      method: 'POST',
      body: JSON.stringify({ ids }),
    },
  )
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
  signal?.throwIfAborted()
  const key = getApiKey()
  const controller = new AbortController()
  const cancel = () => controller.abort(signal?.reason)
  let rejectAbort: () => void = () => {}
  const aborted = new Promise<never>((_, reject) => {
    rejectAbort = () => reject(controller.signal.reason)
    controller.signal.addEventListener('abort', rejectAbort, { once: true })
  })
  if (signal?.aborted) cancel()
  signal?.addEventListener('abort', cancel, { once: true })
  let idleTimer: ReturnType<typeof setTimeout>
  const heartbeat = () => {
    clearTimeout(idleTimer)
    idleTimer = setTimeout(() => controller.abort(new RequestTimeoutError()), 45_000)
  }
  heartbeat()
  try {
    controller.signal.throwIfAborted()
    const res = await Promise.race([fetch(`/api/runs/${encodeURIComponent(id)}/stream`, {
      headers: {
        Accept: 'text/event-stream',
        ...(key ? { Authorization: `Bearer ${key}` } : {}),
        ...(lastEventId ? { 'Last-Event-ID': lastEventId } : {}),
      },
      signal: controller.signal,
    }), aborted])
    if (!res.ok) {
      if (res.status === 401) signalUnauthorized(key)
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

    try {
      while (true) {
        const { done, value } = await Promise.race([reader.read(), aborted])
        if (done) break
        heartbeat()
        buffer += decoder.decode(value, { stream: true })
        if (buffer.length > 16_000_000) throw new ApiError(0, '事件内容过大，请重新连接')
        dispatchCompleteEvents()
      }
      buffer += decoder.decode()
      dispatchCompleteEvents()
    } finally {
      await reader.cancel().catch(() => {})
      reader.releaseLock()
    }
  } catch (error) {
    if (!signal?.aborted && controller.signal.aborted) throw new RequestTimeoutError()
    throw error
  } finally {
    clearTimeout(idleTimer!)
    signal?.removeEventListener('abort', cancel)
    controller.signal.removeEventListener('abort', rejectAbort)
  }
}

export function getConfig(signal?: AbortSignal): Promise<ConfigView> {
  return request<ConfigView>('/api/config', { signal })
}

export function getCapabilities(
  signal?: AbortSignal,
): Promise<{ exports: Record<RunDocumentFormat, boolean> }> {
  return request('/api/capabilities', { signal })
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

export function listSearchProfiles(): Promise<SearchProfile[]> {
  return request<SearchProfile[]>('/api/search-profiles')
}

export function saveSearchProfile(body: SearchProfileInput, id?: string): Promise<SearchProfile> {
  return request<SearchProfile>(
    id ? `/api/search-profiles/${encodeURIComponent(id)}` : '/api/search-profiles',
    {
      method: id ? 'PUT' : 'POST',
      body: JSON.stringify(body),
    },
  )
}

export function deleteSearchProfile(id: string): Promise<void> {
  return requestVoid(`/api/search-profiles/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export function testSearchProfile(id: string): Promise<TestResult> {
  return request<TestResult>(`/api/search-profiles/${encodeURIComponent(id)}/test`, {
    method: 'POST',
  })
}

export function previewRolePrompt(
  behavior: Behavior,
  system_prompt: string,
  prompt_mode: 'append' | 'replace',
): Promise<PromptPreview> {
  return request<PromptPreview>('/api/agents/prompt-preview', {
    method: 'POST',
    body: JSON.stringify({ behavior, system_prompt, prompt_mode }),
  })
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
