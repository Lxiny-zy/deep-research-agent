import { useEffect, useRef, useState, type PointerEvent } from 'react'
import { createPortal } from 'react-dom'
import {
  getDocument,
  GlobalWorkerOptions,
  type PDFDocumentProxy,
  type PDFDocumentLoadingTask,
  type RenderTask,
} from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { useDialogFocus } from '../hooks/useDialogFocus'
import { regionBounds, regionFigureKey, type PdfImageRegion } from '../lib/pdfRegions'

GlobalWorkerOptions.workerSrc = workerUrl
const ASSET_BASE = import.meta.env.DEV ? '/node_modules/pdfjs-dist/' : '/assets/pdfjs/'

export default function PdfRegionPicker({
  file,
  initialRegions,
  maxRegions,
  onApply,
  onClose,
}: {
  file: File
  initialRegions: PdfImageRegion[]
  maxRegions: number
  onApply: (regions: PdfImageRegion[]) => void
  onClose: () => void
}) {
  const dialogRef = useDialogFocus(onClose)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const dragStart = useRef<[number, number] | null>(null)
  const loadingRef = useRef<PDFDocumentLoadingTask | null>(null)
  const [document, setDocument] = useState<PDFDocumentProxy | null>(null)
  const [page, setPage] = useState(1)
  const [width, setWidth] = useState(560)
  const [size, setSize] = useState({ width: 560, height: 720 })
  const [regions, setRegions] = useState(() =>
    initialRegions.map((region) => ({
      ...region,
      bounds: [...region.bounds] as PdfImageRegion['bounds'],
    })),
  )
  const [coordinates, setCoordinates] = useState(['', '', '', ''])
  const [figure, setFigure] = useState('')
  const [drawing, setDrawing] = useState(false)
  const [rendering, setRendering] = useState(false)
  const [previewError, setPreviewError] = useState(false)
  const [context, setContext] = useState('')
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const bounds = regionBounds(coordinates)

  useEffect(() => {
    let cancelled = false
    let loading: PDFDocumentLoadingTask | undefined
    setDocument(null)
    setError('')
    setPreviewError(false)
    const timer = window.setTimeout(() => {
      cancelled = true
      setError('PDF 预览超时，请重试。')
      setPreviewError(true)
      void loading?.destroy().catch(() => {})
    }, 45_000)
    void (async () => {
      const bytes = await file.arrayBuffer()
      if (cancelled) return
      loading = getDocument({
        data: new Uint8Array(bytes),
        cMapUrl: `${ASSET_BASE}cmaps/`,
        cMapPacked: true,
        standardFontDataUrl: `${ASSET_BASE}standard_fonts/`,
      })
      loadingRef.current = loading
      const pdf = await loading.promise
      if (cancelled) {
        await loading.destroy()
        return
      }
      setDocument(pdf)
      setPage(1)
    })()
      .catch(() => {
        if (!cancelled) {
          setError('无法打开这个 PDF，请检查文件后重试。')
          setPreviewError(true)
        }
      })
      .finally(() => window.clearTimeout(timer))
    return () => {
      cancelled = true
      window.clearTimeout(timer)
      void loading?.destroy().catch(() => {})
      if (loadingRef.current === loading) loadingRef.current = null
    }
  }, [file, attempt])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const measure = () => setWidth(Math.max(160, container.clientWidth))
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(container)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (!document) return
    let cancelled = false
    let task: RenderTask | undefined
    setRendering(true)
    setContext('')
    setPreviewError(false)
    const timer = window.setTimeout(() => {
      cancelled = true
      task?.cancel()
      setRendering(false)
      setPreviewError(true)
      setError('本页预览超时，请重新读取 PDF。')
      setDocument(null)
      void loadingRef.current?.destroy().catch(() => {})
    }, 45_000)
    void (async () => {
      const selected = await document.getPage(page)
      if (cancelled || !canvasRef.current) return
      const scale = Math.min(2, width / selected.getViewport({ scale: 1 }).width)
      const viewport = selected.getViewport({ scale })
      const canvas = canvasRef.current
      const ratio = Math.min(window.devicePixelRatio || 1, 2)
      canvas.width = Math.ceil(viewport.width * ratio)
      canvas.height = Math.ceil(viewport.height * ratio)
      setSize({ width: viewport.width, height: viewport.height })
      task = selected.render({
        canvas,
        canvasContext: canvas.getContext('2d')!,
        viewport,
        transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
      })
      await task.promise
      const text = await selected.getTextContent()
      if (!cancelled)
        setContext(
          text.items
            .flatMap((item) => ('str' in item ? [item.str] : []))
            .join(' ')
            .slice(0, 800),
        )
    })()
      .catch((cause) => {
        if (!cancelled && cause?.name !== 'RenderingCancelledException') {
          setError('本页预览失败，请重试或选择其他页。')
          setPreviewError(true)
        }
      })
      .finally(() => {
        window.clearTimeout(timer)
        if (!cancelled) setRendering(false)
      })
    return () => {
      cancelled = true
      window.clearTimeout(timer)
      task?.cancel()
    }
  }, [document, page, width])

  function point(event: PointerEvent<HTMLDivElement>): [number, number] {
    const rect = event.currentTarget.getBoundingClientRect()
    return [
      Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
      Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)),
    ]
  }
  function draw(event: PointerEvent<HTMLDivElement>) {
    if (!dragStart.current) return
    const end = point(event),
      start = dragStart.current
    setCoordinates(
      [
        Math.min(start[0], end[0]),
        Math.min(start[1], end[1]),
        Math.max(start[0], end[0]),
        Math.max(start[1], end[1]),
      ].map((value) => (value * 100).toFixed(2)),
    )
  }
  function addRegion() {
    setPreviewError(false)
    const label = regionFigureKey(figure)
    if (!label) return setError('请填写原文图号，例如 Fig. 1、Figure 1a 或 图1。')
    if (!bounds) return setError('请框选有效范围，或填写左、上、右、下四个百分比。')
    if (regions.length >= maxRegions) return setError('本任务最多选择 4 个区域，请先移除其他区域。')
    if (regions.some((region) => regionFigureKey(region.figure_label) === label))
      return setError('这个图号已有区域（可能在其他页），请先移除原选择。')
    setRegions((current) => [...current, { page, figure_label: figure.trim(), bounds }])
    setCoordinates(['', '', '', ''])
    setFigure('')
    setError('')
    setDrawing(false)
  }
  const boxStyle = (value: PdfImageRegion['bounds']) => ({
    left: `${value[0] * 100}%`,
    top: `${value[1] * 100}%`,
    width: `${(value[2] - value[0]) * 100}%`,
    height: `${(value[3] - value[1]) * 100}%`,
  })

  return createPortal(
    <div className="modal-backdrop pdf-region-backdrop">
      <section
        className="modal pdf-region-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="pdf-region-title"
        ref={dialogRef}
        tabIndex={-1}
      >
        <header className="row between">
          <h2 id="pdf-region-title">选择 PDF 图像区域</h2>
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            关闭
          </button>
        </header>
        <p className="hint">
          {file.name} ·
          保留页码、图号和原文上下文。区域作为图像素材，底层内容不可编辑；是否采用仍需后续核验。
        </p>
        <div className="pdf-region-layout">
          <div className="pdf-region-preview" ref={containerRef}>
            <div className="row pdf-region-toolbar">
              <label>
                页码{' '}
                <select
                  className="input"
                  aria-label="预览页码"
                  disabled={!document || rendering}
                  value={page}
                  onChange={(event) => {
                    setPage(Number(event.target.value))
                    setCoordinates(['', '', '', ''])
                    setDrawing(false)
                    setError('')
                  }}
                >
                  {Array.from({ length: document?.numPages || 1 }, (_, index) => (
                    <option key={index} value={index + 1}>
                      {index + 1}
                    </option>
                  ))}
                </select>
              </label>
              <span className="hint">共 {document?.numPages ?? '…'} 页</span>
              <button
                className="btn btn-secondary btn-sm"
                type="button"
                aria-pressed={drawing}
                disabled={!document || rendering || previewError}
                onClick={() => setDrawing((value) => !value)}
              >
                {drawing ? '取消框选' : '开始框选'}
              </button>
            </div>
            {!document && !error && <p role="status">正在读取本地 PDF…</p>}
            <div
              className={`pdf-region-surface${drawing ? ' is-drawing' : ''}`}
              style={{ width: size.width, height: size.height }}
              aria-label={`第 ${page} 页区域预览`}
              onPointerDown={(event) => {
                if (!drawing || rendering || event.button !== 0) return
                dragStart.current = point(event)
                event.currentTarget.setPointerCapture(event.pointerId)
                setCoordinates(['', '', '', ''])
              }}
              onPointerMove={draw}
              onPointerUp={(event) => {
                if (!dragStart.current) return
                draw(event)
                dragStart.current = null
                setDrawing(false)
                event.currentTarget.releasePointerCapture(event.pointerId)
              }}
              onPointerCancel={() => {
                dragStart.current = null
                setDrawing(false)
              }}
            >
              <canvas ref={canvasRef} style={{ width: size.width, height: size.height }} />
              {regions
                .filter((region) => region.page === page)
                .map((region, index) => (
                  <span
                    key={index}
                    className="pdf-region-box is-saved"
                    style={boxStyle(region.bounds)}
                  >
                    <span>{region.figure_label}</span>
                  </span>
                ))}
              {bounds && <span className="pdf-region-box" style={boxStyle(bounds)} />}
            </div>
            {rendering && (
              <p className="hint" role="status">
                正在绘制本页…
              </p>
            )}
          </div>
          <div className="pdf-region-controls stack">
            <p>先选择页码，点击“开始框选”后拖动；也可用键盘填写范围。</p>
            <label className="field-label">
              原文图号
              <input
                className="input"
                maxLength={40}
                placeholder="例如 Fig. 1 或 图1"
                value={figure}
                onChange={(event) => setFigure(event.target.value)}
              />
            </label>
            <div className="pdf-region-coordinates">
              {['左边界（%）', '上边界（%）', '右边界（%）', '下边界（%）'].map((label, index) => (
                <label key={label} className="field-label">
                  {label}
                  <input
                    className="input"
                    type="number"
                    min={0}
                    max={100}
                    step={0.01}
                    value={coordinates[index]}
                    onChange={(event) =>
                      setCoordinates((current) =>
                        current.map((value, at) => (at === index ? event.target.value : value)),
                      )
                    }
                  />
                </label>
              ))}
            </div>
            <button
              className="btn btn-secondary"
              type="button"
              disabled={!document || rendering || previewError || regions.length >= maxRegions}
              onClick={addRegion}
            >
              添加这个区域
            </button>
            {error && (
              <p className="error-text" role="alert">
                {error}
                {previewError && (
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() => setAttempt((value) => value + 1)}
                  >
                    重新读取 PDF
                  </button>
                )}
              </p>
            )}
            <h3>
              已选区域（{regions.length}/{maxRegions}）
            </h3>
            {maxRegions === 0 && <p className="hint">其他附件已占满本任务的 4 个区域。</p>}
            <ol className="pdf-region-list">
              {regions.map((region, index) => (
                <li key={index}>
                  <span>
                    第 {region.page} 页 · {region.figure_label}
                  </span>
                  <button
                    className="btn btn-ghost btn-sm"
                    type="button"
                    onClick={() => setRegions((current) => current.filter((_, at) => at !== index))}
                    aria-label={`移除区域 ${index + 1}`}
                  >
                    移除
                  </button>
                </li>
              ))}
            </ol>
            {context && (
              <details>
                <summary>本页原文摘录</summary>
                <p className="pdf-region-context">{context}</p>
              </details>
            )}
            <button
              className="btn btn-primary"
              type="button"
              disabled={regions.length > maxRegions}
              onClick={() => onApply(regions)}
            >
              使用这 {regions.length} 个区域
            </button>
          </div>
        </div>
      </section>
    </div>,
    window.document.body,
  )
}
