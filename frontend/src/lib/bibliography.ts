import type { CitationOccurrence, Finding, ReportBibliography } from '../types'
import { stripTrailingReferences } from './evidence'

/** Do not apply a separately fetched catalog to stale or streaming report text. */
export function catalogForReport(
  markdown: string,
  urls: (string | undefined)[],
  catalog?: ReportBibliography | null,
): ReportBibliography | undefined {
  const text = (value: string) => value.replace(/\r\n/g, '\n').trim()
  if (
    !catalog ||
    (text(catalog.source_body) !== text(markdown) &&
      text(catalog.source_body) !== text(stripTrailingReferences(markdown)))
  )
    return
  if (catalog.locations.length !== urls.length) return
  const seen = new Set<number>()
  const documents = new Map(catalog.documents.map((document) => [document.index, document]))
  for (const location of catalog.locations) {
    if (
      !Number.isInteger(location.index) ||
      location.index < 1 ||
      seen.has(location.index) ||
      urls[location.index - 1] !== location.url ||
      !documents.get(location.document)?.locations.includes(location.index)
    )
      return
    seen.add(location.index)
  }
  return catalog
}

export function citationLocations(href: string): number[] {
  const match = /^#cite-(\d+(?:-\d+)*)$/.exec(href)
  if (!match) return []
  const values = [...new Set(match[1].split('-').map(Number))]
  return values.every((value) => Number.isSafeInteger(value) && value > 0) ? values : []
}

export function documentNumber(catalog: ReportBibliography | undefined, location: number): number {
  return catalog?.locations.find((item) => item.index === location)?.document ?? location
}

export function citedDocuments(catalog: ReportBibliography) {
  const used = catalog.cited_documents
  return used == null ? catalog.documents : catalog.documents.filter((entry) => used.includes(entry.index))
}

export function citationOccurrence(
  href: string,
  catalog?: ReportBibliography,
): CitationOccurrence | undefined {
  const match = /^#cite-o-([0-9a-f]{24})$/.exec(href)
  if (!match || catalog?.binding_status !== 'bound') return
  const item = catalog.occurrences?.find((occurrence) => occurrence.id === match[1])
  if (
    !item ||
    !item.locations.length ||
    !item.locations.every((index) =>
      catalog.locations.some(
        (location) => location.index === index && location.document === item.document,
      ),
    )
  )
    return
  return item
}

export function reviewedFindings(
  findings: Finding[],
  occurrence: CitationOccurrence,
  targets: (string | undefined)[],
): Finding[] {
  const ids = new Set(occurrence.evidence_ids)
  const urls = new Set(occurrence.locations.map((index) => targets[index - 1]))
  const seen = new Set<string>()
  return findings.filter((finding) => {
    if (!finding.support_id || !ids.has(finding.support_id) || !urls.has(finding.source_url))
      return false
    const key = `${finding.support_id}:${finding.verification.source_content_hash}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}
