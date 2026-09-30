/**
 * 在 PDF 文本层里定位一段引文。
 *
 * 回答里的原文摘句来自服务端的 PDF 解析，和 PDF.js 切出的文本块在空白、断行、
 * 连字符上常有出入，所以比较时忽略空白与大小写，并把行尾连字符视为断词。
 * 整句找不到时依次退到开头、结尾的片段，至少让读者落到正确的位置。
 */

export interface QuoteMatch {
  /** 从 0 开始的页序号 */
  page: number
  /** 命中的文本块下标（对应该页 getTextContent().items 中的文本项） */
  items: number[]
}

export function normalizeForMatch(text: string): string {
  return text
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[\s­]+/g, '')
}

interface IndexedPage {
  text: string
  starts: number[]
  ends: number[]
}

function indexPage(items: string[]): IndexedPage {
  let text = ''
  const starts: number[] = []
  const ends: number[] = []
  items.forEach((raw, index) => {
    let piece = normalizeForMatch(raw)
    // 行尾连字符多半是排版断词：去掉后与解析出的连续单词对齐
    if (piece.endsWith('-') && index < items.length - 1) piece = piece.slice(0, -1)
    starts.push(text.length)
    text += piece
    ends.push(text.length)
  })
  return { text, starts, ends }
}

function candidates(needle: string, minLength: number): string[] {
  const options = [
    needle,
    needle.slice(0, 120),
    needle.slice(0, 60),
    needle.slice(-60),
    needle.slice(0, 30),
    needle.slice(-30),
  ]
  return [...new Set(options)].filter((item) => item.length >= minLength)
}

export function findQuote(pages: string[][], quote: string, minLength = 8): QuoteMatch | null {
  const needle = normalizeForMatch(quote)
  if (!needle) return null
  const indexed = pages.map(indexPage)
  for (const candidate of candidates(needle, Math.min(minLength, needle.length))) {
    for (let page = 0; page < indexed.length; page += 1) {
      const { text, starts, ends } = indexed[page]
      const at = text.indexOf(candidate)
      if (at < 0) continue
      const stop = at + candidate.length
      const items: number[] = []
      starts.forEach((start, index) => {
        if (ends[index] > start && start < stop && ends[index] > at) items.push(index)
      })
      return { page, items }
    }
  }
  return null
}
