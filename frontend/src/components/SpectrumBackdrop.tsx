import { useEffect, useRef } from 'react'
import { drawSpectralLines } from '../lib/spectrumRender'
import { DARK_LINES, pageLines, spectrumKey, stepLines, type LiveLine } from '../lib/spectralLines'

/** 光谱带贴近视口底边：像地平线上的一道光，不穿过阅读区。 */
const BAND = 0.965

/**
 * 全局谱线背景：一道贴着底边的光谱，本页所在的那一步在其上亮起。
 * - 切换页面：亮点沿光谱带滑到新一步的位置，颜色随之连续变化；
 * - 指针：靠近底边时，亮点附近升得更高、更亮；
 * - 标签页隐藏时停帧；「减少动态效果」时只在变化时画一帧静态画面。
 */
export default function SpectrumBackdrop({ route, dark }: { route: string; dark: boolean }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const target = useRef(pageLines(spectrumKey(route)))
  const darkRef = useRef(dark)
  const kick = useRef<() => void>(() => {})

  useEffect(() => {
    target.current = pageLines(spectrumKey(route))
    kick.current()
  }, [route])

  useEffect(() => {
    darkRef.current = dark
    kick.current()
  }, [dark])

  useEffect(() => {
    const canvas = ref.current
    if (!canvas || typeof ResizeObserver === 'undefined') return
    let ctx: CanvasRenderingContext2D | null = null
    try {
      ctx = canvas.getContext('2d')
    } catch {
      ctx = null
    }
    if (!ctx) return
    const context = ctx
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    const lines: LiveLine[] = target.current.map((line) => ({ ...line }))
    let width = 0
    let height = 0
    let frame = 0
    const pointer = { x: -9999, y: -9999, s: 0, tx: -9999, ty: -9999, ts: 0 }

    const render = (time: number) => {
      stepLines(lines, target.current, reduced ? 1 : 0.05)
      pointer.x += (pointer.tx - pointer.x) * 0.12
      pointer.y += (pointer.ty - pointer.y) * 0.12
      pointer.s += (pointer.ts - pointer.s) * 0.08
      drawSpectralLines(context, {
        width,
        height,
        band: BAND,
        lines,
        absorption: DARK_LINES,
        time,
        pointer,
        dark: darkRef.current,
      })
    }
    const loop = (time: number) => {
      render(time)
      frame = requestAnimationFrame(loop)
    }
    const start = () => {
      cancelAnimationFrame(frame)
      if (reduced) render(0)
      else if (!document.hidden) frame = requestAnimationFrame(loop)
    }
    kick.current = start

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 1.5)
      width = canvas.clientWidth
      height = canvas.clientHeight
      canvas.width = Math.max(1, Math.floor(width * dpr))
      canvas.height = Math.max(1, Math.floor(height * dpr))
      context.setTransform(dpr, 0, 0, dpr, 0, 0)
      if (reduced) render(0)
    }
    const onMove = (event: PointerEvent) => {
      const rect = canvas.getBoundingClientRect()
      pointer.tx = event.clientX - rect.left
      pointer.ty = event.clientY - rect.top
      if (pointer.s < 0.01) {
        pointer.x = pointer.tx
        pointer.y = pointer.ty
      }
      pointer.ts = 1
    }
    const onLeave = () => {
      pointer.ts = 0
    }

    const observer = new ResizeObserver(resize)
    observer.observe(canvas)
    resize()
    start()
    if (!reduced) {
      window.addEventListener('pointermove', onMove, { passive: true })
      document.documentElement.addEventListener('pointerleave', onLeave)
    }
    document.addEventListener('visibilitychange', start)
    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
      window.removeEventListener('pointermove', onMove)
      document.documentElement.removeEventListener('pointerleave', onLeave)
      document.removeEventListener('visibilitychange', start)
      kick.current = () => {}
    }
  }, [])

  return <canvas ref={ref} className="spectrum-canvas" aria-hidden="true" />
}
