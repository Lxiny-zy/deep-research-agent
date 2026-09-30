import { useEffect, useMemo, useRef, useState } from 'react'
import { GlobalWorkerOptions, Util, getDocument } from 'pdfjs-dist'
import type { PDFDocumentProxy, PDFPageProxy } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { fetchReaderPdf } from '../api/client'
import { findQuote } from '../lib/pdfMatch'
import { AppIcon } from './AppIcon'

GlobalWorkerOptions.workerSrc = workerUrl

// 中文论文常用 CID 字体，需要 cMap；构建时由 vite 插件复制到 /assets/pdfjs/
const ASSET_BASE = import.meta.env.DEV ? '/node_modules/pdfjs-dist/' : '/assets/pdfjs/'
const ZOOM_STEPS = [0.6, 0.8, 1, 1.25, 1.5, 2]

interface PageSize {
  width: number
  height: number
}

interface Rect {
  x: number
  y: number
  width: number
  height: number
}

interface TextRun {
  str: string
  transform: number[]
  width: number
}

export interface PdfHighlight {
  quote: string
  /** 同一句引文再次点击也要重新定位，所以带一个递增序号 */
  token: number
}

function textRuns(items: unknown[]): TextRun[] {
  return items.filter(
    (item): item is TextRun => typeof item === 'object' && item !== null && 'str' in item,
  )
}

function PdfPage({
  doc,
  number,
  size,
  scale,
  rects,
  pageRef,
}: {
  doc: PDFDocumentProxy
  number: number
  size: PageSize
  scale: number
  rects: Rect[]
  pageRef: (element: HTMLDivElement | null) => void
}) {
  const holder = useRef<HTMLDivElement | null>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [visible, setVisible] = useState(typeof IntersectionObserver === 'undefined')

  useEffect(() => {
    const element = holder.current
    if (!element || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver(
      (entries) => setVisible(entries.some((entry) => entry.isIntersecting)),
      { rootMargin: '800px 0px' },
    )
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const target = canvas.current
    if (!target) return
    if (!visible) {
      // 远离视口的页释放位图，长论文也不会占满内存
      target.width = 0
      target.height = 0
      return
    }
    let cancelled = false
    let task: { cancel: () => void; promise: Promise<void> } | null = null
    void doc.getPage(number).then((page: PDFPageProxy) => {
      if (cancelled) return
      const ratio = window.devicePixelRatio || 1
      const viewport = page.getViewport({ scale })
      target.width = Math.floor(viewport.width * ratio)
      target.height = Math.floor(viewport.height * ratio)
      const context = target.getContext('2d')
      if (!context) return
      const rendering = page.render({
        canvas: target,
        canvasContext: context,
        viewport,
        transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
      })
      task = rendering
      rendering.promise.catch(() => {
        // 缩放或翻页时取消渲染属于正常情况
      })
    })
    return () => {
      cancelled = true
      task?.cancel()
    }
  }, [doc, number, scale, visible])

  return (
    <div
      ref={(element) => {
        holder.current = element
        pageRef(element)
      }}
      className="pdf-page"
      style={{ width: size.width * scale, height: size.height * scale }}
      aria-label={`第 ${number} 页`}
    >
      <canvas ref={canvas} style={{ width: '100%', height: '100%' }} aria-hidden="true" />
      {rects.map((rect, index) => (
        <span
          key={index}
          className="pdf-highlight"
          style={{
            left: rect.x * scale,
            top: rect.y * scale,
            width: rect.width * scale,
            height: rect.height * scale,
          }}
        />
      ))}
    </div>
  )
}

export default function PdfViewer({
  runId,
  documentId,
  highlight,
}: {
  runId: string
  documentId: string
  highlight?: PdfHighlight | null
}) {
  const scroller = useRef<HTMLDivElement>(null)
  const pageRefs = useRef<(HTMLDivElement | null)[]>([])
  const texts = useRef(new Map<number, TextRun[]>())
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null)
  const [sizes, setSizes] = useState<PageSize[]>([])
  const [error, setError] = useState<string | null>(null)
  const [zoom, setZoom] = useState<number | 'fit'>('fit')
  const [width, setWidth] = useState(0)
  const [marks, setMarks] = useState<{ page: number; rects: Rect[] } | null>(null)
  const [missed, setMissed] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    let task: ReturnType<typeof getDocument> | null = null
    setDoc(null)
    setSizes([])
    setError(null)
    setMarks(null)
    setMissed(false)
    texts.current = new Map()
    fetchReaderPdf(runId, documentId, controller.signal)
      .then((buffer) => {
        task = getDocument({
          // 6.x 已移除 eval 编译路径；不传 wasmUrl，CSP 也未放开 wasm，图像解码走 JS 回退
          data: new Uint8Array(buffer),
          cMapUrl: `${ASSET_BASE}cmaps/`,
          cMapPacked: true,
          standardFontDataUrl: `${ASSET_BASE}standard_fonts/`,
        })
        return task.promise
      })
      .then(async (pdf) => {
        const list: PageSize[] = []
        for (let number = 1; number <= pdf.numPages; number += 1) {
          const viewport = (await pdf.getPage(number)).getViewport({ scale: 1 })
          list.push({ width: viewport.width, height: viewport.height })
        }
        if (controller.signal.aborted) return
        setSizes(list)
        setDoc(pdf)
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return
        setError(reason instanceof Error ? reason.message : '原版 PDF 加载失败')
      })
    return () => {
      controller.abort()
      void task?.destroy()
    }
  }, [runId, documentId])

  useEffect(() => {
    const element = scroller.current
    if (!element) return
    const measure = () => setWidth(Math.max(element.clientWidth - 32, 0))
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  const fitScale = sizes[0] && width ? Math.min(width / sizes[0].width, 2.5) : 1
  const scale = zoom === 'fit' ? fitScale : zoom
  const scaleRef = useRef(scale)
  scaleRef.current = scale

  const quote = highlight?.quote
  const token = highlight?.token
  useEffect(() => {
    if (!doc || !quote) return
    let cancelled = false
    void (async () => {
      const pages: string[][] = []
      for (let number = 1; number <= doc.numPages; number += 1) {
        if (!texts.current.has(number)) {
          const content = await (await doc.getPage(number)).getTextContent()
          texts.current.set(number, textRuns(content.items))
        }
        pages.push((texts.current.get(number) ?? []).map((run) => run.str))
      }
      const match = findQuote(pages, quote)
      if (cancelled) return
      if (!match) {
        setMarks(null)
        setMissed(true)
        return
      }
      const viewport = (await doc.getPage(match.page + 1)).getViewport({ scale: 1 })
      const runs = texts.current.get(match.page + 1) ?? []
      const rects = match.items.map((index) => {
        const run = runs[index]
        const box = Util.transform(viewport.transform, run.transform)
        const height = Math.hypot(box[2], box[3])
        return { x: box[4], y: box[5] - height, width: run.width, height: height * 1.15 }
      })
      if (cancelled) return
      setMissed(false)
      setMarks({ page: match.page, rects })
      const page = pageRefs.current[match.page]
      scroller.current?.scrollTo?.({
        top: (page?.offsetTop ?? 0) + (rects[0]?.y ?? 0) * scaleRef.current - 96,
        behavior: 'smooth',
      })
    })()
    return () => {
      cancelled = true
    }
  }, [doc, quote, token])

  const pages = useMemo(() => sizes.map((size, index) => ({ size, number: index + 1 })), [sizes])
  const zoomIn = () => setZoom(ZOOM_STEPS.find((step) => step > scale + 0.01) ?? scale)
  const zoomOut = () =>
    setZoom([...ZOOM_STEPS].reverse().find((step) => step < scale - 0.01) ?? scale)

  return (
    <div className="pdf-viewer">
      <div className="pdf-toolbar" role="toolbar" aria-label="原文工具栏">
        <span className="pdf-toolbar-count">{doc ? `共 ${doc.numPages} 页` : '原版 PDF'}</span>
        {missed && (
          <span className="pdf-toolbar-note" role="status">
            原文中没有找到这段话，可能跨页或排版不同
          </span>
        )}
        <div className="pdf-toolbar-zoom">
          <button
            type="button"
            className="btn btn-ghost btn-sm icon-button"
            onClick={zoomOut}
            aria-label="缩小"
            title="缩小"
            disabled={!doc}
          >
            <AppIcon name="zoom-out" size={15} aria-hidden="true" />
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => setZoom('fit')}
            title="适应宽度"
            disabled={!doc}
          >
            {zoom === 'fit' ? '适应宽度' : `${Math.round(scale * 100)}%`}
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm icon-button"
            onClick={zoomIn}
            aria-label="放大"
            title="放大"
            disabled={!doc}
          >
            <AppIcon name="zoom-in" size={15} aria-hidden="true" />
          </button>
        </div>
      </div>
      <div ref={scroller} className="pdf-scroller">
        {error ? (
          <div className="alert error" role="alert">
            <AppIcon name="circle-x" size={14} aria-hidden="true" />
            {error}
          </div>
        ) : !doc ? (
          <p className="pdf-loading" role="status">
            <AppIcon name="loader" size={15} className="spin" aria-hidden="true" />
            正在载入原版 PDF…
          </p>
        ) : (
          pages.map(({ size, number }) => (
            <PdfPage
              key={number}
              doc={doc}
              number={number}
              size={size}
              scale={scale}
              rects={marks?.page === number - 1 ? marks.rects : []}
              pageRef={(element) => {
                pageRefs.current[number - 1] = element
              }}
            />
          ))
        )}
      </div>
    </div>
  )
}
