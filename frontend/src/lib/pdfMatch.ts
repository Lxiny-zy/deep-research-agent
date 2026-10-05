/** Match complete quotations and retain offsets into the original PDF text items. */
export interface QuoteRange {
  /** Zero-based page/item indices; UTF-16 offsets for DOM Range. */
  page: number
  item: number
  start: number
  end: number
}

export interface QuoteMatch {
  page: number
  ranges: QuoteRange[]
}

interface MappedText {
  text: string
  starts: number[]
  ends: number[]
}

function normalizeMapped(raw: string): MappedText {
  const chars: string[] = []
  const starts: number[] = []
  const ends: number[] = []
  // Keep combining marks with their base character. Ligature expansions still
  // point to the original glyph; all offsets use the DOM's UTF-16 convention.
  for (const match of raw.matchAll(/\P{M}\p{M}*|\p{M}+/gu)) {
    const value = match[0].normalize('NFKC').toLowerCase()
    for (const character of value) {
      if (/[\s\u00ad]/u.test(character)) continue
      const normalized = character
        .replace(/[‘’]/g, "'")
        .replace(/[“”]/g, '"')
        .replace(/−/g, '-')
      for (let unit = 0; unit < normalized.length; unit += 1) {
        chars.push(normalized[unit])
        starts.push(match.index)
        ends.push(match.index + match[0].length)
      }
    }
  }
  const text = chars.join('')
  const omitted = new Set<number>()
  // Backend scientific superscripts use ×10^6, while PDF.js supplies 10 and
  // the exponent as separate items. Preserve arbitrary mathematical exponents.
  for (const match of text.matchAll(
    /([+-]?(?:\d+(?:\.\d*)?|\.\d+)[×x*·]10)\^(?:\{[+-]?\d+\}|[+-]?\d+)/g,
  )) {
    for (let i = match.index + match[1].length; i < match.index + match[0].length; i += 1) {
      if ('^{}'.includes(text[i])) omitted.add(i)
    }
  }
  return {
    text: chars.filter((_, i) => !omitted.has(i)).join(''),
    starts: starts.filter((_, i) => !omitted.has(i)),
    ends: ends.filter((_, i) => !omitted.has(i)),
  }
}

export function normalizeForMatch(text: string): string {
  return normalizeMapped(text).text
}

interface IndexedItem extends MappedText {
  page: number
  item: number
  at: number
}

function indexDocument(pages: string[][]) {
  const items: IndexedItem[] = []
  const discretionaryHyphens = new Set<number>()
  let text = ''
  const flattened = pages.flatMap((page, pageIndex) =>
    page.map((raw, item) => ({ raw, page: pageIndex, item })),
  )
  flattened.forEach(({ raw, page, item }, index) => {
    const mapped = normalizeMapped(raw)
    // Only a hyphen at a text-item boundary can be skipped. At each boundary
    // the quote decides whether it is a compound word or a broken word.
    if (/\p{L}[-‐]$/u.test(mapped.text)) {
      let next = index + 1
      while (next < flattened.length && !flattened[next].raw.trim()) next += 1
      if (next < flattened.length && /^\p{L}/u.test(flattened[next].raw.trimStart())) {
        discretionaryHyphens.add(text.length + mapped.text.length - 1)
      }
    }
    items.push({ ...mapped, page, item, at: text.length })
    text += mapped.text
  })
  return { text, items, discretionaryHyphens }
}

function matchEnd(text: string, needle: string, at: number, hyphens: Set<number>): number | null {
  let index = 0
  while (index < needle.length && at < text.length) {
    if (text[at] === needle[index]) {
      index += 1
      at += 1
    } else if (hyphens.has(at)) {
      at += 1
    } else {
      return null
    }
  }
  return index === needle.length ? at : null
}

/** Require the entire quote; reject ambiguous occurrences instead of guessing. */
export function findQuote(pages: string[][], quote: string): QuoteMatch | null {
  const needles = new Set([
    normalizeForMatch(quote),
    normalizeForMatch(quote.replace(/(\p{L})[-‐][ \t]*\r?\n\s*(?=\p{L})/gu, '$1')),
  ])
  needles.delete('')
  if (!needles.size) return null
  const matches = new Map<string, QuoteMatch>()
  const indexed = indexDocument(pages)
  for (const needle of needles) {
    let at = indexed.text.indexOf(needle[0])
    while (at >= 0) {
      const stop = matchEnd(indexed.text, needle, at, indexed.discretionaryHyphens)
      if (stop === null) {
        at = indexed.text.indexOf(needle[0], at + 1)
        continue
      }
      const ranges = indexed.items.flatMap((item): QuoteRange[] => {
        const from = Math.max(0, at - item.at)
        const to = Math.min(item.text.length, stop - item.at)
        if (from >= to) return []
        return [
          {
            page: item.page,
            item: item.item,
            start: item.starts[from],
            end: item.ends[to - 1],
          },
        ]
      })
      if (ranges.length) {
        matches.set(JSON.stringify(ranges), { page: ranges[0].page, ranges })
        if (matches.size > 1) return null
      }
      at = indexed.text.indexOf(needle[0], at + 1)
    }
  }
  return matches.values().next().value ?? null
}
