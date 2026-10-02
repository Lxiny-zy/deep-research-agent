import type { ReportBibliography } from '../types'
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
