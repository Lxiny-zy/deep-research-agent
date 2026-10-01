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
export const PDF_PARSE_TIMEOUT_MS = 45_000

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
  const [actualSize, setActualSize] = useState<PageSize | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

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
    setError(null)
    let task: { cancel: () => void; promise: Promise<void> } | null = null
    const timer = window.setTimeout(() => {
      cancelled = true
      task?.cancel()
      setError('这一页渲染超时，请重试')
    }, PDF_PARSE_TIMEOUT_MS)
    void doc
      .getPage(number)
      .then(async (page: PDFPageProxy) => {
        if (cancelled) return
        const original = page.getViewport({ scale: 1 })
        setActualSize({ width: original.width, height: original.height })
        const ratio = window.devicePixelRatio || 1
        const viewport = page.getViewport({ scale })
        target.width = Math.floor(viewport.width * ratio)
        target.height = Math.floor(viewport.height * ratio)
        const context = target.getContext('2d')
        if (!context) throw new Error('浏览器无法创建 PDF 画布')
        const rendering = page.render({
          canvas: target,
          canvasContext: context,
          viewport,
          transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
        })
        task = rendering
        await rendering.promise
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : '这一页渲染失败')
      })
      .finally(() => window.clearTimeout(timer))
    return () => {
      cancelled = true
      window.clearTimeout(timer)
      task?.cancel()
    }
  }, [doc, number, scale, visible, attempt])

  return (
    <div
      ref={(element) => {
        holder.current = element
        pageRef(element)
      }}
      className="pdf-page"
      style={{
        width: (actualSize ?? size).width * scale,
        height: (actualSize ?? size).height * scale,
      }}
      aria-label={`第 ${number} 页`}
    >
      <canvas ref={canvas} style={{ width: '100%', height: '100%' }} aria-hidden="true" />
      {error && (
        <div className="pdf-page-error" role="alert">
          <p>{error}</p>
          <button className="btn btn-secondary" onClick={() => setAttempt((value) => value + 1)}>
            重试这一页
          </button>
        </div>
      )}
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
  const [attempt, setAttempt] = useState(0)
  const [phase, setPhase] = useState<'download' | 'parse'>('download')
  const [download, setDownload] = useState({ loaded: 0, total: null as number | null })

  useEffect(() => {
    const controller = new AbortController()
    let task: ReturnType<typeof getDocument> | null = null
    let parseTimer: ReturnType<typeof setTimeout> | undefined
    setDoc(null)
    setSizes([])
    setError(null)
    setMarks(null)
    setMissed(false)
    setPhase('download')
    setDownload({ loaded: 0, total: null })
    texts.current = new Map()
    fetchReaderPdf(runId, documentId, controller.signal, (loaded, total) => {
      if (!controller.signal.aborted) setDownload({ loaded, total })
    })
      .then((buffer) => {
        controller.signal.throwIfAborted()
        setPhase('parse')
        parseTimer = setTimeout(() => {
          setError('PDF 解析超时，文件已下载，请重试加载')
          controller.abort()
          void task?.destroy().catch(() => {})
        }, PDF_PARSE_TIMEOUT_MS)
        task = getDocument({
          // 6.x 已移除 eval 编译路径；不传 wasmUrl，CSP 也未放开 wasm，图像解码走 JS 回退
          data: new Uint8Array(buffer),
          cMapUrl: `${ASSET_BASE}cmaps/`,
          cMapPacked: true,
          standardFontDataUrl: `${ASSET_BASE}standard_fonts/`,
          useWasm: false,
        })
        return task.promise
      })
      .then(async (pdf) => {
        // Show the first page immediately; remaining pages load in PdfPage
        // when approaching the viewport and then use their own dimensions.
        const viewport = (await pdf.getPage(1)).getViewport({ scale: 1 })
        const list = Array.from({ length: pdf.numPages }, () => ({
          width: viewport.width,
          height: viewport.height,
        }))
        if (controller.signal.aborted) return
        clearTimeout(parseTimer)
        setSizes(list)
        setDoc(pdf)
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return
        clearTimeout(parseTimer)
        setError(reason instanceof Error ? reason.message : '原版 PDF 加载失败')
      })
    return () => {
      controller.abort()
      clearTimeout(parseTimer)
      void task?.destroy().catch(() => {})
    }
  }, [runId, documentId, attempt])

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
            <button className="btn btn-secondary" onClick={() => setAttempt((value) => value + 1)}>
              重新加载
            </button>
          </div>
        ) : !doc ? (
          <p className="pdf-loading" role="status">
            <AppIcon name="loader" size={15} className="spin" aria-hidden="true" />
            {phase === 'parse' ? '正在解析原版 PDF…' : '正在下载原版 PDF…'}
            {phase === 'download' && download.loaded > 0 && (
              <span>
                {(download.loaded / 1048576).toFixed(1)} MB
                {download.total ? ` / ${(download.total / 1048576).toFixed(1)} MB` : ''}
              </span>
            )}
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
