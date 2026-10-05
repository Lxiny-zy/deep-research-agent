// 类型化的 HTTP 客户端：统一错误处理，所有路径走 Vite proxy / 同源后端。
import type {
  ResourcePreflight,
  SearchResourceImpact,
  AgentCard,
  AttachmentUploadResult,
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
  ResearchProject,
  Corpus,
  LibrarySource,
  LibrarySourceStatus,
  SourceChunk,
  ImportSourceInput,
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
  TaskTemplate,
  TaskContract,
  DeliverableRegistry,
  RunTemplateInfo,
  QaConversation,
  DatasetParseResult,
  DatasetMergeRequest,
  DatasetMergeResult,
  QaMessage,
  QaActivity,
  QaSourceOption,
  RunNarrative,
  RunReader,
  RunWorkspace,
  TierSpec,
  UsageQuota,
  QualityField,
} from '../types'

export const checkResourcePreflight = (workflow: string, signal?: AbortSignal) =>
  request<ResourcePreflight>(`/api/resource-preflight?workflow=${encodeURIComponent(workflow)}`, {
    signal,
  })

export const getSearchResourceImpact = () =>
  request<SearchResourceImpact>('/api/search-resources/impact')
import { normalizeReportDocument } from '../lib/reportDocument'
import { withResponse } from './transport'
import {
  cacheReaderPdf,
  cachedReaderPdf,
  clearReaderPdfCache,
  persistentReaderPdf,
  persistReaderPdf,
  readerPdfCacheGeneration,
} from '../lib/readerPdfCache'
import { QaStreamInterruptedError, RequestTimeoutError } from './transport'

export class ApiError extends Error {
  qaTerminal = false
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
const revisionRequests = new Map<string, string>()

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
    try {
      window[storage].removeItem(API_KEY_STORAGE)
    } catch {
      /* Storage is optional. */
    }
  }
  clearWorkspaceState()
}

export function clearWorkspaceState(): void {
  revisionRequests.clear()
  void clearReaderPdfCache()
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
        if (/^dr_.*(?:draft|thread|pending_run|pending_qa)/.test(name))
          window[storage].removeItem(name)
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

async function request<T>(url: string, init?: RequestInit, timeoutMs = 30_000): Promise<T> {
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
    timeoutMs,
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
      typeof saved?.key === 'string' &&
      typeof saved?.body === 'string'
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

export function listProjects(signal?: AbortSignal): Promise<ResearchProject[]> {
  return request<ResearchProject[]>('/api/projects', { signal })
}

export function createProject(body: {
  name: string
  description?: string
}): Promise<ResearchProject> {
  return request<ResearchProject>('/api/projects', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function listCorpora(projectId: string, signal?: AbortSignal): Promise<Corpus[]> {
  return request<Corpus[]>(`/api/projects/${encodeURIComponent(projectId)}/corpora`, { signal })
}

export function createCorpus(
  projectId: string,
  body: { name: string; description?: string },
): Promise<Corpus> {
  return request<Corpus>(`/api/projects/${encodeURIComponent(projectId)}/corpora`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function listLibrarySources(
  projectId: string,
  corpusId?: string,
  signal?: AbortSignal,
): Promise<LibrarySource[]> {
  const query = corpusId ? `?corpus_id=${encodeURIComponent(corpusId)}` : ''
  return request<LibrarySource[]>(
    `/api/projects/${encodeURIComponent(projectId)}/sources${query}`,
    { signal },
  )
}

export function importLibrarySource(
  projectId: string,
  body: ImportSourceInput,
): Promise<LibrarySource> {
  return request<LibrarySource>(
    `/api/projects/${encodeURIComponent(projectId)}/sources/import`,
    { method: 'POST', body: JSON.stringify(body) },
    120_000,
  )
}

export function listSourceChunks(
  projectId: string,
  sourceId: string,
  signal?: AbortSignal,
): Promise<SourceChunk[]> {
  return request<SourceChunk[]>(
    `/api/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceId)}/chunks`,
    { signal },
  )
}

export function setLibrarySourceStatus(
  projectId: string,
  sourceId: string,
  status: LibrarySourceStatus,
): Promise<LibrarySource> {
  return request<LibrarySource>(
    `/api/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceId)}`,
    { method: 'PATCH', body: JSON.stringify({ status }) },
  )
}

export function deleteLibrarySource(projectId: string, sourceId: string): Promise<void> {
  return requestVoid(
    `/api/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceId)}`,
    { method: 'DELETE' },
  )
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

export type RunDocumentFormat =
  | 'md'
  | 'csv'
  | 'xlsx'
  | 'pdf'
  | 'tex'
  | 'bib'
  | 'bundle'
  | 'paper_pdf'
export type ReportExportProfile = 'academic' | 'technical' | 'executive' | 'appendix'
export type LatexTemplateName = 'ctexart' | 'ctexrep' | 'ieeetran' | 'acmart'

export interface RunDocumentExportOptions {
  includeHsiTables?: boolean
  tableId?: string
  profile?: ReportExportProfile
  template?: LatexTemplateName
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
  if (options.profile && (format === 'tex' || format === 'paper_pdf')) {
    query.set('profile', options.profile)
  }
  if (options.profile && format === 'bundle') query.set('profile', options.profile)
  if (options.template && (format === 'tex' || format === 'paper_pdf' || format === 'bundle')) {
    query.set('template', options.template)
  }
  // 只有单表导出（CSV/XLSX）需要选表。md/pdf 渲染的是整份文档，给它们带
  // table_id 会让 URL 声明一个该端点并不遵守的约束。用白名单而不是排除法，
  // 这样将来新增格式默认不带，而不是默认带上。
  if (options.tableId && (format === 'csv' || format === 'xlsx')) {
    query.set('table_id', options.tableId)
  }
  const suffix = query.toString() ? `?${query.toString()}` : ''
  const encodedId = encodeURIComponent(id)
  const endpointByFormat: Record<RunDocumentFormat, string> = {
    md: 'md',
    csv: 'csv',
    xlsx: 'xlsx',
    pdf: 'pdf',
    tex: 'tex',
    bib: 'bib',
    bundle: 'bundle.zip',
    paper_pdf: 'paper.pdf',
  }
  const endpoint = endpointByFormat[format]
  // 与服务端 Content-Disposition 的 ASCII 文件名保持一致，头缺失时也不会得到 .bundle
  const suffixByFormat: Partial<Record<RunDocumentFormat, string>> = {
    bundle: '-bundle.zip',
    paper_pdf: '-paper.pdf',
  }
  const fileSuffix = suffixByFormat[format] ?? `.${format}`
  const fallback = `research-${id.replace(/[^A-Za-z0-9._-]/g, '_') || 'run'}${fileSuffix}`
  const key = getApiKey()
  return withResponse(
    `/api/runs/${encodedId}/document.${endpoint}${suffix}`,
    {
      headers: {
        Accept: 'application/octet-stream',
        'X-Render-Retry': crypto.randomUUID(),
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
    // 论文版 PDF 由服务端 XeLaTeX 编译，上限 120s；客户端再留出排队与传输余量，
    // 否则服务端还在编译时前端先报超时，用户重试又触发一次新的编译。
    format === 'paper_pdf' || format === 'bundle' ? 180_000 : 120_000,
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
    const res = await Promise.race([
      fetch(`/api/runs/${encodeURIComponent(id)}/stream`, {
        headers: {
          Accept: 'text/event-stream',
          ...(key ? { Authorization: `Bearer ${key}` } : {}),
          ...(lastEventId ? { 'Last-Event-ID': lastEventId } : {}),
        },
        signal: controller.signal,
      }),
      aborted,
    ])
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
): Promise<{ exports: Partial<Record<RunDocumentFormat, boolean>> }> {
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

// ---- 科研工作台 ------------------------------------------------------------

export function listTemplates(signal?: AbortSignal): Promise<TaskTemplate[]> {
  return request<TaskTemplate[]>('/api/templates', { signal })
}

export function previewContract(
  template: string,
  query: string,
  signal?: AbortSignal,
  strategy?: string | null,
): Promise<TaskContract> {
  return request<TaskContract>('/api/templates/contract', {
    method: 'POST',
    body: JSON.stringify({ template, query, strategy: strategy ?? null }),
    signal,
  })
}

export function getDeliverables(
  id: string,
  signal?: AbortSignal,
  retryToken?: string,
): Promise<DeliverableRegistry> {
  return request<DeliverableRegistry>(`/api/runs/${encodeURIComponent(id)}/deliverables`, {
    signal,
    ...(retryToken ? { headers: { 'X-Render-Retry': retryToken } } : {}),
  })
}

export function reviseRunContent(runId: string, sourceVersion: string): Promise<CreateRunResponse> {
  const key = `${runId}/${sourceVersion}`
  const storageKey = `dr_pending_run_revision:${runId}`
  let requestId = revisionRequests.get(key)
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey) ?? 'null')
    if (
      saved?.version === sourceVersion &&
      typeof saved?.id === 'string' &&
      /^[A-Za-z0-9_-]{8,64}$/.test(saved.id)
    ) {
      requestId = saved.id
    }
  } catch {
    /* A tab can still retry using its in-memory identity. */
  }
  requestId ??= crypto.randomUUID()
  revisionRequests.set(key, requestId)
  try {
    sessionStorage.setItem(storageKey, JSON.stringify({ version: sourceVersion, id: requestId }))
  } catch {
    /* Storage is optional. */
  }
  return request<CreateRunResponse>(
    `/api/runs/${encodeURIComponent(runId)}/revise`,
    {
      method: 'POST',
      body: JSON.stringify({ source_version: sourceVersion, request_id: requestId }),
    },
    120_000,
  )
}

export function retryDeliverable(
  id: string,
  version: string,
  format: string,
  requestId: string,
): Promise<DeliverableRegistry> {
  return request<DeliverableRegistry>(
    `/api/runs/${encodeURIComponent(id)}/deliverables/retry`,
    {
      method: 'POST',
      body: JSON.stringify({ version, format, request_id: requestId }),
    },
    300_000,
  )
}

export function getRunTemplate(id: string, signal?: AbortSignal): Promise<RunTemplateInfo> {
  return request<RunTemplateInfo>(`/api/runs/${encodeURIComponent(id)}/template`, { signal })
}

/** 取一份交付物的字节（带鉴权头，不把密钥放进 URL）。 */
export async function fetchDeliverable(
  id: string,
  name: string,
  signal?: AbortSignal,
  version?: string,
): Promise<RunDocumentDownload> {
  const key = getApiKey()
  const path = name.split('/').map(encodeURIComponent).join('/')
  return withResponse(
    `/api/runs/${encodeURIComponent(id)}/deliverables/${path}${version ? `?version=${encodeURIComponent(version)}` : ''}`,
    {
      headers: {
        Accept: 'application/octet-stream',
        ...(key ? { Authorization: `Bearer ${key}` } : {}),
      },
      signal,
    },
    async (res) => {
      if (!res.ok) {
        if (res.status === 401) signalUnauthorized(key)
        let detail = res.statusText
        try {
          const body = (await res.json()) as { detail?: unknown }
          if (body.detail != null) detail = formatDetail(body.detail, res.statusText)
        } catch {
          // Error responses are allowed to be non-JSON.
        }
        throw new ApiError(res.status, detail)
      }
      return {
        blob: await res.blob(),
        filename: downloadFilename(
          res.headers.get('Content-Disposition'),
          name.split('/').pop() ?? name,
        ),
      }
    },
    120_000,
  )
}

// ---- 学术问答 --------------------------------------------------------------

/** 不传 runId 时只列普通问答；传入时只列绑定到该精读任务的会话。 */
export function listConversations(signal?: AbortSignal, runId?: string): Promise<QaConversation[]> {
  const query = runId ? `?${new URLSearchParams({ run_id: runId }).toString()}` : ''
  return request<QaConversation[]>(`/api/qa/conversations${query}`, { signal })
}

export function createConversation(title = '', runId?: string): Promise<QaConversation> {
  return request<QaConversation>('/api/qa/conversations', {
    method: 'POST',
    body: JSON.stringify(runId ? { title, run_id: runId } : { title }),
  })
}

export function getConversation(id: string, signal?: AbortSignal): Promise<QaConversation> {
  return request<QaConversation>(`/api/qa/conversations/${encodeURIComponent(id)}`, { signal })
}

export function getQaRequest(
  id: string,
  requestId: string,
  signal?: AbortSignal,
): Promise<QaMessage> {
  return request<QaMessage>(
    `/api/qa/conversations/${encodeURIComponent(id)}/requests/${encodeURIComponent(requestId)}`,
    { signal },
  )
}

export function deleteConversation(id: string): Promise<void> {
  return requestVoid(`/api/qa/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

/** 每轮显式选择额外参考来源；精读会话始终保留本论文。 */
export function askQuestion(
  id: string,
  query: string,
  signal?: AbortSignal,
  scope?: {
    sources: QaSourceOption[]
    projectId?: string
    requestId?: string
    revisionMessageId?: string
  },
  onDelta?: (delta: string) => void,
  onActivity?: (activity: QaActivity) => void,
): Promise<QaMessage> {
  const body = scope
    ? {
        query,
        sources: scope.sources,
        ...(scope.projectId ? { project_id: scope.projectId } : {}),
        ...(scope.requestId ? { request_id: scope.requestId } : {}),
        ...(scope.revisionMessageId ? { revision_message_id: scope.revisionMessageId } : {}),
      }
    : { query }
  return askQuestionStream(id, body, signal, onDelta, onActivity)
}

async function askQuestionStream(
  id: string,
  body: {
    query: string
    sources?: QaSourceOption[]
    project_id?: string
    request_id?: string
    revision_message_id?: string
  },
  signal?: AbortSignal,
  onDelta?: (delta: string) => void,
  onActivity?: (activity: QaActivity) => void,
): Promise<QaMessage> {
  const key = getApiKey()
  const controller = new AbortController()
  const cancel = () => controller.abort(signal?.reason)
  let timedOut = false
  let deltaTimer: ReturnType<typeof setTimeout> | undefined
  let deltaParts: string[] = []
  let deltaSize = 0
  const flushDeltas = () => {
    if (deltaTimer !== undefined) clearTimeout(deltaTimer)
    deltaTimer = undefined
    const text = deltaParts.join('')
    deltaParts = []
    deltaSize = 0
    if (text && !signal?.aborted) onDelta?.(text)
  }
  let idleTimer: ReturnType<typeof setTimeout> | undefined
  const refreshIdleTimer = () => {
    if (idleTimer !== undefined) clearTimeout(idleTimer)
    idleTimer = setTimeout(() => {
      timedOut = true
      controller.abort()
    }, 45_000)
  }
  if (signal?.aborted) cancel()
  signal?.addEventListener('abort', cancel, { once: true })
  // Include connection setup and response headers in the idle deadline.
  refreshIdleTimer()
  try {
    const response = await fetch(
      body.revision_message_id
        ? `/api/qa/conversations/${encodeURIComponent(id)}/messages/${encodeURIComponent(body.revision_message_id)}/revise/stream`
        : `/api/qa/conversations/${encodeURIComponent(id)}/messages/stream`,
      {
        method: 'POST',
        headers: {
          Accept: 'text/event-stream',
          'Content-Type': 'application/json',
          ...(key ? { Authorization: `Bearer ${key}` } : {}),
        },
        body: JSON.stringify(body.revision_message_id ? { request_id: body.request_id } : body),
        signal: controller.signal,
      },
    )
    if (!response.ok) {
      if (response.status === 401) signalUnauthorized(key)
      let detail = response.statusText || `请求失败（HTTP ${response.status}）`
      try {
        const payload = (await response.json()) as { detail?: unknown }
        if (payload.detail != null) detail = formatDetail(payload.detail, detail)
      } catch {
        // Keep status text when the error body is not JSON.
      }
      throw new ApiError(response.status, detail)
    }
    if (!response.body) throw new ApiError(0, '问答流没有响应内容')

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    refreshIdleTimer()
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        refreshIdleTimer()
        buffer += decoder.decode(value, { stream: true })
        let boundary = buffer.match(/\r?\n\r?\n/)
        while (boundary?.index != null) {
          const block = buffer.slice(0, boundary.index)
          buffer = buffer.slice(boundary.index + boundary[0].length)
          const event = block
            .split(/\r?\n/)
            .find((line) => line.startsWith('event:'))
            ?.slice(6)
            .trim()
          const data = block
            .split(/\r?\n/)
            .filter((line) => line.startsWith('data:'))
            .map((line) => line.slice(5).replace(/^ /, ''))
            .join('\n')
          if (event === 'delta' && data) {
            const payload = JSON.parse(data) as { delta?: unknown }
            if (typeof payload.delta === 'string') {
              deltaParts.push(payload.delta)
              deltaSize += payload.delta.length
              if (deltaSize >= 2048) flushDeltas()
              else deltaTimer ??= setTimeout(flushDeltas, 60)
            }
          }
          if (event === 'complete' && data) {
            flushDeltas()
            return JSON.parse(data) as QaMessage
          }
          if (event && ['reasoning', 'usage', 'status', 'cache', 'reset'].includes(event) && data) {
            const payload = JSON.parse(data) as Record<string, unknown>
            if (payload && typeof payload === 'object') {
              if (event === 'reset') flushDeltas()
              onActivity?.({ ...payload, type: event as QaActivity['type'] })
            }
          }
          if (event === 'error' && data) {
            const payload = JSON.parse(data) as { status?: number; detail?: unknown }
            const failure = new ApiError(
              payload.status ?? 502,
              formatDetail(payload.detail, '问答失败'),
            )
            failure.qaTerminal = true
            throw failure
          }
          boundary = buffer.match(/\r?\n\r?\n/)
        }
      }
    } finally {
      await reader.cancel().catch(() => {})
      reader.releaseLock()
    }
    throw new QaStreamInterruptedError()
  } catch (error) {
    if (timedOut) throw new RequestTimeoutError()
    if (error instanceof TypeError && !signal?.aborted) throw new QaStreamInterruptedError()
    throw error
  } finally {
    flushDeltas()
    if (idleTimer !== undefined) clearTimeout(idleTimer)
    signal?.removeEventListener('abort', cancel)
  }
}

export function getReader(runId: string, signal?: AbortSignal): Promise<RunReader> {
  return request<RunReader>(`/api/runs/${encodeURIComponent(runId)}/reader`, { signal })
}

/** 取回原版 PDF 的字节；由前端的 PDF.js 渲染，不交给浏览器内置阅读器。 */
export async function fetchReaderPdf(
  runId: string,
  documentId: string,
  signal?: AbortSignal,
  onProgress?: (loaded: number, total: number | null) => void,
): Promise<ArrayBuffer> {
  const key = getApiKey()
  const cacheKey = JSON.stringify([key, runId, documentId])
  const epoch = readerPdfCacheGeneration()
  signal?.throwIfAborted()
  const cached = cachedReaderPdf(cacheKey) ?? (await persistentReaderPdf(cacheKey, epoch))
  signal?.throwIfAborted()
  if (epoch !== readerPdfCacheGeneration() || key !== getApiKey()) {
    throw new DOMException('登录状态已改变', 'AbortError')
  }
  if (cached) {
    signal?.throwIfAborted()
    return cached
  }
  const bytes = await withResponse(
    `/api/runs/${encodeURIComponent(runId)}/reader/${encodeURIComponent(documentId)}/pdf`,
    { headers: key ? { Authorization: `Bearer ${key}` } : {}, signal },
    async (res) => {
      if (!res.ok) {
        if (res.status === 401) signalUnauthorized(key)
        throw new ApiError(res.status, res.status === 404 ? '原版 PDF 暂不可用' : res.statusText)
      }
      if (!res.body || !onProgress) return res.arrayBuffer()
      const length = Number(res.headers.get('Content-Length'))
      const total = Number.isFinite(length) && length > 0 ? length : null
      const reader = res.body.getReader()
      const chunks: Uint8Array[] = []
      let loaded = 0
      try {
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          chunks.push(value)
          loaded += value.byteLength
          onProgress(loaded, total)
        }
      } finally {
        await reader.cancel().catch(() => {})
        reader.releaseLock()
      }
      const result = new Uint8Array(loaded)
      let offset = 0
      for (const chunk of chunks) {
        result.set(chunk, offset)
        offset += chunk.byteLength
      }
      return result.buffer
    },
    120_000,
  )
  signal?.throwIfAborted()
  if (epoch !== readerPdfCacheGeneration() || key !== getApiKey()) {
    throw new DOMException('登录状态已改变', 'AbortError')
  }
  cacheReaderPdf(cacheKey, bytes)
  await persistReaderPdf(cacheKey, bytes, epoch)
  signal?.throwIfAborted()
  if (epoch !== readerPdfCacheGeneration()) throw new DOMException('登录状态已改变', 'AbortError')
  return bytes
}

export function getNarrative(id: string, signal?: AbortSignal): Promise<RunNarrative> {
  return request<RunNarrative>(`/api/runs/${encodeURIComponent(id)}/narrative`, { signal })
}

export function getWorkspace(id: string, signal?: AbortSignal): Promise<RunWorkspace> {
  return request<RunWorkspace>(`/api/runs/${encodeURIComponent(id)}/workspace`, { signal })
}

/** 读取工作区中一个已登记的产物（文本），返回内容与是否被截断。 */
export async function readWorkspaceFile(
  id: string,
  path: string,
  signal?: AbortSignal,
): Promise<{ text: string; truncated: boolean }> {
  const key = getApiKey()
  const query = new URLSearchParams({ path })
  return withResponse(
    `/api/runs/${encodeURIComponent(id)}/workspace/file?${query.toString()}`,
    { headers: key ? { Authorization: `Bearer ${key}` } : {}, signal },
    async (res) => {
      if (!res.ok) {
        if (res.status === 401) signalUnauthorized(key)
        throw new ApiError(res.status, res.statusText || `HTTP ${res.status}`)
      }
      return { text: await res.text(), truncated: res.headers.get('X-Truncated') === '1' }
    },
  )
}

export function listTiers(signal?: AbortSignal): Promise<TierSpec[]> {
  return request<TierSpec[]>('/api/tiers', { signal })
}

export function getUsage(signal?: AbortSignal): Promise<UsageQuota> {
  return request<UsageQuota>('/api/usage', { signal })
}

export function getQualitySchema(signal?: AbortSignal): Promise<QualityField[]> {
  return request<QualityField[]>('/api/config/quality-schema', { signal })
}

/** 解析待分析的表格文件（CSV / TSV / XLSX）：返回每张工作表的 CSV 与概况，不落盘。 */
export function parseDatasetFile(
  body: { filename: string; data_base64: string },
  signal?: AbortSignal,
): Promise<DatasetParseResult> {
  return request<DatasetParseResult>('/api/datasets', {
    method: 'POST',
    body: JSON.stringify(body),
    signal,
  })
}

export function mergeDatasetTables(
  body: DatasetMergeRequest, signal?: AbortSignal,
): Promise<DatasetMergeResult> {
  return request<DatasetMergeResult>('/api/datasets/merge', {
    method: 'POST', body: JSON.stringify(body), signal,
  })
}

/** 上传文件原始字节；保留旧 Base64 调用方兼容入口。PDF 原文件供精读页显示。 */
export function uploadAttachment(
  body: File | { filename: string; mime_type: string; data_base64: string },
  signal?: AbortSignal,
): Promise<AttachmentUploadResult> {
  if (body instanceof File) {
    return request<AttachmentUploadResult>(
      `/api/attachments/file?filename=${encodeURIComponent(body.name)}`,
      {
        method: 'POST',
        body,
        headers: { 'Content-Type': body.type || 'application/octet-stream' },
        signal,
      },
      120_000,
    )
  }
  return request<AttachmentUploadResult>(
    '/api/attachments',
    { method: 'POST', body: JSON.stringify(body), signal },
    120_000,
  )
}
