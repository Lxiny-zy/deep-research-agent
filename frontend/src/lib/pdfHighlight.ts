import { TextLayer } from 'pdfjs-dist'
import type { PDFPageProxy, TextContent } from 'pdfjs-dist/types/src/display/api'
import type { QuoteRange } from './pdfMatch'
import './pdfHighlight.css'

export interface PdfRect {
  x: number
  y: number
  width: number
  height: number
}

/** Measure character ranges using PDF.js's actual text layout, including glyph
 * widths, ligatures, font scaling and text rotation; never paint an entire run. */
export async function quoteRects(
  page: PDFPageProxy,
  content: TextContent,
  ranges: QuoteRange[],
  signal: AbortSignal,
): Promise<PdfRect[]> {
  signal.throwIfAborted()
  const viewport = page.getViewport({ scale: 1, rotation: 0 })
  const rotation = page.getViewport({ scale: 1 }).rotation
  const container = document.createElement('div')
  container.className = 'pdf-text-measure'
  container.setAttribute('aria-hidden', 'true')
  container.style.setProperty('--total-scale-factor', String(viewport.scale * viewport.userUnit))
  document.body.append(container)
  const layer = new TextLayer({ textContentSource: content, viewport, container })
  // TextLayer normally inherits the full viewer's rounding CSS variables.
  // This isolated layer uses the exact viewport dimensions instead.
  container.style.width = `${viewport.width}px`
  container.style.height = `${viewport.height}px`
  const cancel = () => layer.cancel()
  signal.addEventListener('abort', cancel, { once: true })
  try {
    await layer.render()
    signal.throwIfAborted()
    const bounds = container.getBoundingClientRect()
    const rects: PdfRect[] = []
    for (const match of ranges) {
      const node = layer.textDivs[match.item]?.firstChild
      if (!node || node.nodeType !== Node.TEXT_NODE) throw new Error('引文文字层不完整')
      const range = document.createRange()
      range.setStart(node, match.start)
      range.setEnd(node, match.end)
      for (const box of range.getClientRects()) {
        if (!box.width || !box.height) continue
        const x = box.left - bounds.left
        const y = box.top - bounds.top
        const width = box.width
        const height = box.height
        // Measure with an unrotated page; map back to the canvas viewport.
        if (rotation === 90) {
          rects.push({ x: viewport.height - y - height, y: x, width: height, height: width })
        } else if (rotation === 180) {
          rects.push({
            x: viewport.width - x - width,
            y: viewport.height - y - height,
            width,
            height,
          })
        } else if (rotation === 270) {
          rects.push({ x: y, y: viewport.width - x - width, width: height, height: width })
        } else {
          rects.push({ x, y, width, height })
        }
      }
    }
    if (!rects.length) throw new Error('无法读取引文的文字位置')
    return rects
  } finally {
    signal.removeEventListener('abort', cancel)
    layer.cancel()
    container.remove()
  }
}
