import { requestWorkspaceJson } from './acceptance'

export interface OperationsOverviewData {
  as_of: string
  since: string
  scope: 'mine' | 'workspace'
  date_basis: string
  backend: string
  max_records_per_kind: number
  coverage: {
    research_records: number
    qa_records: number
    truncated: boolean
    unknown_date_records: number
    unknown_research_first_results: number
  }
  scenarios: {
    scenario: string
    total: number
    statuses: Record<string, number>
    origins: Record<string, number>
    initial_done: number
  }[]
  daily: { date: string; statuses: Record<string, number> }[]
  needs_review_reasons: Record<string, number>
  research_duration: { observations: number; p50_seconds: number | null; p95_seconds: number | null }
  model_calls: {
    recorded_attempts: number
    statuses: Record<string, number>
    retries: number
    usage: Record<string, { known_total: number | null; reported_calls: number; unknown_calls: number }>
    duration_observations: number
    p95_seconds: number | null
    cost: null
    cost_basis: string
  }
  rendering: {
    records: number
    statuses: Record<string, number>
    retried: number
    stalled: number
    oldest_pending_seconds: number | null
  } | null
  limitations: string[]
}

export function getOperations(days: number, scope: 'mine' | 'workspace', signal?: AbortSignal) {
  return requestWorkspaceJson<OperationsOverviewData>(`/api/operations?days=${days}&scope=${scope}`, signal)
}
