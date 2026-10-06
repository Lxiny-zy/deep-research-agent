import { getApiKey } from './client'
import { withResponse } from './transport'

export type AcceptanceConclusion = 'pending' | 'pass' | 'fail' | 'mixed' | 'uncertain'
export type IssueCategory =
  | 'correctness'
  | 'coverage'
  | 'citation'
  | 'format'
  | 'layout'
  | 'interaction'
  | 'context'
  | 'performance'
  | 'other'
export type FirstAttemptStatus = 'unknown' | 'done' | 'needs_review' | 'error' | 'cancelled'
export interface AcceptanceIssueInput {
  location_id?: string
  requirement_ids: string[]
  source_ids: string[]
  evidence_selections: { evidence_id: string; excerpt: string }[]
  excerpt: string
  category: IssueCategory
  observation: string
  conclusion: Exclude<AcceptanceConclusion, 'mixed'>
  next_work: string[]
}
export interface AcceptanceInput {
  request_id: string
  document_version: string
  delivery_version?: string
  include_hsi_tables: boolean
  phase: 'initial' | 'recovery'
  first_attempt_status: FirstAttemptStatus
  parent_record_id?: string
  conclusion: AcceptanceConclusion
  issues: AcceptanceIssueInput[]
  note: string
}
export interface AcceptanceContext {
  document_version: string
  observed_status: string
  template: string
  coverage_review_bound: boolean
  prose_review_bound: boolean
  coverage_issues: string[]
  prose_review_issues: string[]
  requirements: { id: string; label: string; status: string; review_bound: boolean }[]
  locations: {
    id: string
    kind: string
    preview: string
    verification_status: string
    pointer: { start_line?: number; table_id?: string; row?: number; column?: string }
  }[]
  evidence: { id: string; citation?: number; source_ids: string[]; semantic_status: string }[]
  sources: { id: string; locator?: string; section?: string; reading_status?: string }[]
}
export interface AcceptanceSlice {
  document_version: string
  excerpt: string
  excerpt_redacted?: boolean
  offset: number
  total_characters: number
}
export interface AcceptanceSummary {
  id: string
  created_at: string
  document_version: string
  phase: 'initial' | 'recovery'
  parent_record_id?: string
  initial_status: string
  observed_status: string
  conclusion: AcceptanceConclusion
}
export interface AcceptanceRecord extends AcceptanceSummary {
  first_attempt_status: FirstAttemptStatus
  recovery_status?: string
  note: string
  issues: {
    category: IssueCategory
    observation: string
    conclusion: AcceptanceConclusion
    excerpt?: string
    evidence?: { excerpt?: string }[]
  }[]
}

export class AcceptanceError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
  ) {
    super(message)
  }
}

async function responseError(response: Response, key: string | null): Promise<never> {
  if (response.status === 401 && key === getApiKey())
    window.dispatchEvent(new Event('dr:unauthorized'))
  const payload = await response.json().catch(() => ({}))
  const detail = payload.detail
  throw new AcceptanceError(
    response.status,
    typeof detail === 'string' ? detail : detail?.message || '无法读取或保存验收记录，请重试。',
    detail?.code,
  )
}

function request<T>(url: string, signal?: AbortSignal, body?: AcceptanceInput): Promise<T> {
  const key = getApiKey()
  return withResponse(
    url,
    {
      signal,
      method: body ? 'POST' : 'GET',
      headers: {
        ...(key ? { Authorization: `Bearer ${key}` } : {}),
        ...(body ? { 'Content-Type': 'application/json' } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    },
    async (response) => {
      if (!response.ok) return responseError(response, key)
      return response.json() as Promise<T>
    },
  )
}
export { request as requestAcceptance, request as requestWorkspaceJson }
const root = (runId: string) => `/api/runs/${encodeURIComponent(runId)}/acceptance`
const versionQuery = (version: string, hsi: boolean) =>
  new URLSearchParams({ version, include_hsi_tables: String(hsi) })
export const getAcceptanceContext = (
  runId: string,
  version: string,
  hsi: boolean,
  signal?: AbortSignal,
) => request<AcceptanceContext>(`${root(runId)}/context?${versionQuery(version, hsi)}`, signal)
export const getAcceptanceSlice = (
  runId: string,
  version: string,
  hsi: boolean,
  kind: 'locations' | 'evidence',
  id: string,
  offset: number,
  signal?: AbortSignal,
) =>
  request<AcceptanceSlice>(
    `${root(runId)}/${kind}/${encodeURIComponent(id)}?${versionQuery(version, hsi)}&offset=${offset}&length=600`,
    signal,
  )
export const listAcceptanceRecords = (runId: string, after = '', signal?: AbortSignal) =>
  request<{ items: AcceptanceSummary[]; next_cursor: string | null }>(
    `${root(runId)}/records?${new URLSearchParams({ after, limit: '50' })}`,
    signal,
  )
export const getAcceptanceRecord = (runId: string, id: string, signal?: AbortSignal) =>
  request<AcceptanceRecord>(`${root(runId)}/records/${encodeURIComponent(id)}`, signal)
export const createAcceptanceRecord = (runId: string, body: AcceptanceInput) =>
  request<AcceptanceRecord>(`${root(runId)}/records`, undefined, body)
export async function getAcceptancePackage(runId: string, record: AcceptanceSummary) {
  const key = getApiKey()
  return withResponse(
    `${root(runId)}/records/${encodeURIComponent(record.id)}/package`,
    { headers: key ? { Authorization: `Bearer ${key}` } : {} },
    async (response) => {
      if (!response.ok) return responseError(response, key)
      const bytes = await response.arrayBuffer()
      const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), (n) =>
        n.toString(16).padStart(2, '0'),
      ).join('')
      if (
        response.headers.get('X-Content-Version') !== record.document_version ||
        response.headers.get('X-Content-SHA256') !== hash
      )
        throw new Error('问题包的版本或完整性校验失败，请重试。')
      return new Blob([bytes], { type: 'application/json' })
    },
  )
}
