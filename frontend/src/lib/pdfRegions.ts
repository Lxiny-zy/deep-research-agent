export interface PdfImageRegion {
  page: number
  figure_label: string
  bounds: [number, number, number, number]
}

export function regionFigureKey(label: string): string | null {
  const match = label.trim().match(/^(?:fig(?:ure)?\.?\s*|图\s*)([1-9]\d*)([a-z]?)$/i)
  return match ? `${Number(match[1])}${match[2].toLowerCase()}` : null
}

export function regionBounds(values: string[]): PdfImageRegion['bounds'] | null {
  if (values.length !== 4 || values.some((value) => !value.trim())) return null
  const numbers = values.map(Number)
  if (numbers.some((value) => !Number.isFinite(value) || value < 0 || value > 100)) return null
  if (numbers[0] >= numbers[2] || numbers[1] >= numbers[3]) return null
  return numbers.map((value) => value / 100) as PdfImageRegion['bounds']
}

export function imageRegions(value: unknown): PdfImageRegion[] {
  return Array.isArray(value) ? (value as PdfImageRegion[]) : []
}
