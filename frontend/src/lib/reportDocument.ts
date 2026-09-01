import type {
  ChartBlock,
  ReportBlock,
  ReportDocument,
  ReportEvidence,
  ReportOverview,
  ReportReference,
  TableBlock,
  TableCell,
  TableColumn,
  TableRow,
} from '../types'

type RecordValue = Record<string, unknown>

function isRecord(value: unknown): value is RecordValue {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function stringValue(value: unknown, fallback = ''): string {
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return fallback
}

function numberValue(value: unknown, fallback: number | null = null): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return fallback
}

function integerValue(value: unknown, fallback: number | null = null): number | null {
  const parsed = numberValue(value)
  return parsed != null && Number.isInteger(parsed) ? parsed : fallback
}

function positiveInteger(value: unknown, fallback: number | null = null): number | null {
  const parsed = integerValue(value)
  return parsed != null && parsed > 0 ? parsed : fallback
}

function booleanValue(value: unknown, fallback = false): boolean {
  return typeof value === 'boolean' ? value : fallback
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map((item) => stringValue(item).trim()).filter(Boolean)
}

function citationList(value: unknown): number[] {
  if (!Array.isArray(value)) return []
  return value.map((item) => positiveInteger(item)).filter((item): item is number => item != null)
}

function normalizeColumn(raw: unknown, index: number): TableColumn | null {
  if (!isRecord(raw)) return null
  const key = stringValue(raw.key, `column-${index + 1}`).trim() || `column-${index + 1}`
  const align = raw.align === 'right' ? 'right' : 'left'
  return {
    key,
    label: stringValue(raw.label, key),
    unit: stringValue(raw.unit),
    align,
    numeric: booleanValue(raw.numeric),
    note_ref: positiveInteger(raw.note_ref),
  }
}

function normalizeCell(raw: unknown): TableCell {
  const cell = isRecord(raw) ? raw : {}
  return {
    value: stringValue(cell.value),
    numeric: numberValue(cell.numeric),
    citations: citationList(cell.citations),
    note_ref: positiveInteger(cell.note_ref),
    disputed: booleanValue(cell.disputed),
  }
}

function normalizeRow(raw: unknown, index: number): TableRow | null {
  if (!isRecord(raw)) return null
  const cells: Record<string, TableCell> = {}
  if (isRecord(raw.cells)) {
    for (const [key, value] of Object.entries(raw.cells)) cells[key] = normalizeCell(value)
  }
  return {
    label: stringValue(raw.label, stringValue(raw.name, `Row ${index + 1}`)),
    citation: positiveInteger(raw.citation),
    cells,
  }
}

function normalizeTable(raw: RecordValue, index: number): TableBlock {
  const columns = Array.isArray(raw.columns)
    ? raw.columns
        .map((column, columnIndex) => normalizeColumn(column, columnIndex))
        .filter((column): column is TableColumn => column != null)
    : []
  const rows = Array.isArray(raw.rows)
    ? raw.rows
        .map((row, rowIndex) => normalizeRow(row, rowIndex))
        .filter((row): row is TableRow => row != null)
    : []
  return {
    kind: 'table',
    id: stringValue(raw.id, `table-${index + 1}`),
    title: stringValue(raw.title),
    columns,
    rows,
    notes: stringList(raw.notes),
    caption: stringValue(raw.caption),
  }
}

const CHART_FORMS = new Set<ChartBlock['form']>(['bar', 'dot', 'grouped_bar', 'scatter', 'line'])

function normalizeChart(raw: RecordValue, index: number): ChartBlock {
  const form = stringValue(raw.form)
  return {
    kind: 'chart',
    id: stringValue(raw.id, `chart-${index + 1}`),
    title: stringValue(raw.title),
    form: CHART_FORMS.has(form as ChartBlock['form']) ? (form as ChartBlock['form']) : 'bar',
    source_table: stringValue(raw.source_table),
    value_columns: stringList(raw.value_columns),
    x_column: stringValue(raw.x_column),
    emphasis: stringValue(raw.emphasis),
    y_label: stringValue(raw.y_label),
    caption: stringValue(raw.caption),
  }
}

function normalizeBlock(raw: unknown, index: number): ReportBlock | null {
  if (!isRecord(raw)) return null
  const kind = stringValue(raw.kind).toLowerCase()
  if (kind === 'table') return normalizeTable(raw, index)
  if (kind === 'chart') return normalizeChart(raw, index)
  if (kind === 'prose' || typeof raw.markdown === 'string') {
    return { kind: 'prose', markdown: stringValue(raw.markdown) }
  }
  return null
}

function normalizeReferences(value: unknown): ReportReference[] {
  if (!Array.isArray(value)) return []
  return value
    .map((raw, index) => {
      if (typeof raw === 'string') {
        const url = raw.trim()
        return url ? { index: index + 1, url, reference: url } : null
      }
      if (!isRecord(raw)) return null
      const url = stringValue(raw.url, stringValue(raw.href)).trim()
      if (!url) return null
      return {
        index: positiveInteger(raw.index, index + 1) ?? index + 1,
        url,
        reference: stringValue(raw.reference, stringValue(raw.title, url)),
      }
    })
    .filter((reference): reference is ReportReference => reference != null)
}

function normalizeEvidence(raw: unknown, fallbackCitation: number): ReportEvidence | null {
  if (!isRecord(raw)) return null
  const semanticConfidence = numberValue(raw.semantic_confidence, 0) ?? 0
  const independentSourceCount = integerValue(raw.independent_source_count, 0) ?? 0
  return {
    citation: positiveInteger(raw.citation, fallbackCitation) ?? fallbackCitation,
    claim_id: stringValue(raw.claim_id),
    statement: stringValue(raw.statement),
    quote: stringValue(raw.quote, stringValue(raw.evidence_quote)),
    context: stringValue(raw.context, stringValue(raw.evidence_context)),
    source_url: stringValue(raw.source_url),
    reference: stringValue(raw.reference),
    source_section: stringValue(raw.source_section),
    content_hash: stringValue(raw.content_hash, stringValue(raw.source_content_hash)),
    verbatim_verified: booleanValue(raw.verbatim_verified, raw.status === 'verified'),
    verification_reason: stringValue(raw.verification_reason, stringValue(raw.reason)),
    semantic_status: stringValue(raw.semantic_status, 'not_checked'),
    semantic_confidence: Math.max(0, Math.min(1, semanticConfidence)),
    semantic_reason: stringValue(raw.semantic_reason),
    consistency_status: stringValue(raw.consistency_status, 'not_checked'),
    contradicts_claim_ids: stringList(raw.contradicts_claim_ids),
    contradiction_reason: stringValue(raw.contradiction_reason),
    corroboration_status: stringValue(raw.corroboration_status, 'not_checked'),
    independent_source_count: Math.max(0, independentSourceCount),
    corroboration_reason: stringValue(raw.corroboration_reason),
    quantity_label: stringValue(raw.quantity_label),
    conditions_label: stringValue(raw.conditions_label),
    quantity_status: stringValue(raw.quantity_status, 'not_applicable'),
    quantity_reason: stringValue(raw.quantity_reason),
  }
}

function normalizeOverview(value: unknown, evidence: ReportEvidence[]): ReportOverview {
  const raw = isRecord(value) ? value : {}
  const count = (key: string, fallback: number) =>
    Math.max(0, integerValue(raw[key], fallback) ?? fallback)
  return {
    records: count('records', evidence.length),
    verbatim_matched: count(
      'verbatim_matched',
      evidence.filter((record) => record.verbatim_verified).length,
    ),
    semantically_supported: count(
      'semantically_supported',
      evidence.filter(
        (record) => record.verbatim_verified && record.semantic_status === 'supported',
      ).length,
    ),
    corroborated: count(
      'corroborated',
      evidence.filter((record) => record.corroboration_status === 'corroborated').length,
    ),
    conflicted: count(
      'conflicted',
      evidence.filter((record) => record.consistency_status === 'conflicted').length,
    ),
    blocked_sources: integerValue(raw.blocked_sources),
  }
}

function hasDocumentShape(raw: RecordValue): boolean {
  return [
    'schema_version',
    'query',
    'blocks',
    'references',
    'evidence',
    'overview',
    'disclaimer',
    'markdown',
    'citations',
    'report',
  ].some((key) => key in raw)
}

/**
 * Normalize the structured-report wire response at the API boundary.
 *
 * The endpoint was added after the legacy Report contract, and deployed
 * clients can therefore see either shape during a rolling upgrade. Keeping
 * this adapter here means report components only handle one stable shape.
 */
export function normalizeReportDocument(payload: unknown): ReportDocument | null {
  if (!isRecord(payload) || !hasDocumentShape(payload)) return null

  const nestedReport = isRecord(payload.report) ? payload.report : undefined
  const rawBlocks = Array.isArray(payload.blocks)
    ? payload.blocks
    : typeof payload.markdown === 'string'
      ? [{ kind: 'prose', markdown: payload.markdown }]
      : nestedReport && typeof nestedReport.markdown === 'string'
        ? [{ kind: 'prose', markdown: nestedReport.markdown }]
        : []
  const blocks = rawBlocks
    .map((block, index) => normalizeBlock(block, index))
    .filter((block): block is ReportBlock => block != null)
  const rawEvidence = Array.isArray(payload.evidence) ? payload.evidence : []
  const evidence = rawEvidence
    .map((record, index) => normalizeEvidence(record, index + 1))
    .filter((record): record is ReportEvidence => record != null)
  const rawReferences = payload.references ?? payload.citations ?? nestedReport?.citations
  const references = normalizeReferences(rawReferences)
  const query = stringValue(payload.query, stringValue(nestedReport?.query))
  const schemaVersion = positiveInteger(payload.schema_version, 1) ?? 1
  return {
    schema_version: schemaVersion,
    query,
    blocks,
    references,
    evidence,
    overview: normalizeOverview(payload.overview, evidence),
    disclaimer: stringValue(payload.disclaimer),
  }
}
