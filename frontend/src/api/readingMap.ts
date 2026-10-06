import { requestAcceptance } from './acceptance'

export interface ReadingUnit {
  id: string
  unit_id: string
  section: string
  preview: string
  verification_status: string
  evidence_count: number
  kind: string
  peer_type?: string | null
  severity?: string | null
}
export interface ReadingAnchor {
  id: string
  evidence_id: string
  source_url: string
  document_id: string | null
  pdf_available: boolean
  page_hint: number | null
  locator: string
  source_hash: string
  start: number
  end: number
  match_kind: string
  quote: string
  quote_truncated: boolean
  quote_redacted: boolean
  quote_total_chars: number
  context_before: string
  context_after: string
  source_binding: string
}
export interface ReadingMapPage {
  document_version: string
  source_version: string
  template: string | null
  review_bound: boolean
  review_issues: string[]
  total: number
  offset: number
  limit: number
  units: ReadingUnit[]
  focus_items: {
    id: string
    label: string
    status: string
    gap_kind?: string
    location_ids: string[]
  }[]
  focus_total: number
  focus_truncated: boolean
  peer_review: {
    score?: number | null
    score_status?: string
    critical_count?: number | null
    coverage_bound: boolean
    coverage_issues: string[]
    scoring_note?: string
    coverage?: { id: string; title: string; status: string; missing_topics: string[] }[]
  } | null
}
export interface ReadingUnitDetail {
  document_version: string
  unit: ReadingUnit
  text: string
  text_offset: number
  text_total_chars: number
  text_truncated: boolean
  text_redacted: boolean
  review_bound: boolean
  anchors: ReadingAnchor[]
  anchor_offset: number
  anchor_total: number
  anchor_limit: number
  evidence_status: { evidence_id: string; status: string; candidates: number }[]
  evidence_status_truncated: boolean
  peer_item: {
    type?: string
    severity?: string
    reason?: string
    action?: string
    action_ready?: boolean
  } | null
  measurement_context: Record<string, unknown>[]
  fulltext_status?: string | null
  fulltext_passages?: ReadingFulltextPassage[]
  fulltext_total?: number
  fulltext_offset?: number
  fulltext_limit?: number
}
export type ReadingPdfTarget = Pick<
  ReadingAnchor,
  'document_id' | 'pdf_available' | 'quote' | 'quote_truncated' | 'quote_redacted'
>
export interface ReadingFulltextPassage extends ReadingPdfTarget {
  source: string
  source_hash: string
  locator: string
  start: number
  end: number
  verdict: string
  review_status: string
  page_hint?: number | null
}
export interface ReadingNavigation {
  runId: string
  documentVersion: string
  includeHsiTables: boolean
  unitId: string
  anchor: Pick<
    ReadingAnchor,
    'document_id' | 'pdf_available' | 'quote' | 'quote_truncated' | 'quote_redacted'
  >
}
function root(runId: string) {
  return `/api/runs/${encodeURIComponent(runId)}/acceptance/reading-map`
}
function query(version: string, hsi: boolean, values: Record<string, string>) {
  return new URLSearchParams({ version, include_hsi_tables: String(hsi), ...values })
}
export function getReadingMap(
  runId: string,
  version: string,
  hsi: boolean,
  offset: number,
  signal?: AbortSignal,
) {
  return requestAcceptance<ReadingMapPage>(
    `${root(runId)}?${query(version, hsi, { offset: String(offset), limit: '20' })}`,
    signal,
  )
}
export function getReadingUnit(
  runId: string,
  version: string,
  hsi: boolean,
  id: string,
  textOffset: number,
  anchorOffset: number,
  fulltextOffset: number,
  signal?: AbortSignal,
) {
  return requestAcceptance<ReadingUnitDetail>(
    `${root(runId)}/units/${encodeURIComponent(id)}?${query(version, hsi, { text_offset: String(textOffset), text_length: '1600', anchor_offset: String(anchorOffset), anchor_limit: '8', fulltext_offset: String(fulltextOffset), fulltext_limit: '8' })}`,
    signal,
  )
}
export function canLocateReadingAnchor(
  anchor: Pick<
    ReadingAnchor,
    'document_id' | 'pdf_available' | 'quote' | 'quote_truncated' | 'quote_redacted'
  >,
): boolean {
  return Boolean(
    anchor.pdf_available === true &&
    anchor.document_id &&
    anchor.quote.trim() &&
    !anchor.quote_truncated &&
    !anchor.quote_redacted,
  )
}
export function readingNavigation(value: unknown): ReadingNavigation | null {
  if (!value || typeof value !== 'object') return null
  const item = value as Partial<ReadingNavigation>
  if (
    typeof item.runId !== 'string' ||
    typeof item.unitId !== 'string' ||
    typeof item.documentVersion !== 'string' ||
    !/^[a-f0-9]{64}$/.test(item.documentVersion) ||
    typeof item.includeHsiTables !== 'boolean' ||
    !item.anchor ||
    typeof item.anchor.quote !== 'string' ||
    item.anchor.quote.length > 4800 ||
    typeof item.anchor.document_id !== 'string'
  )
    return null
  return canLocateReadingAnchor(item.anchor) ? (item as ReadingNavigation) : null
}
