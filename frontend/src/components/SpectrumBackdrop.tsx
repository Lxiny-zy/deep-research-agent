import { useEffect, useRef } from 'react'
import { SIGNATURES, padPeaks, signatureKey, stepToward, type Peak } from '../lib/spectrum'
import { drawSpectrum } from '../lib/spectrumRender'

/**
 * 全局光谱背景：随页面变形的高斯光谱 + 指针交互。
 * - 切换页面：峰的位置 / 宽度 / 高度 / 颜色逐帧插值到新页面的特征；
 * - 指针：附近的曲线被吸起、谱线点亮、带一圈同色光晕；
 * - 标签页隐藏时停帧；「减少动态效果」时只在变化时画一帧静态画面。
 */
export default function SpectrumBackdrop({ route, dark }: { route: string; dark: boolean }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const target = useRef(padPeaks(SIGNATURES[signatureKey(route)].peaks))
  const baseline = useRef(SIGNATURES[signatureKey(route)].baseline)
  const darkRef = useRef(dark)
  const kick = useRef<() => void>(() => {})

  useEffect(() => {
    const signature = SIGNATURES[signatureKey(route)]
    target.current = padPeaks(signature.peaks)
    baseline.current = signature.baseline
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
    const peaks: Peak[] = target.current.map((p) => ({ ...p, rgb: [...p.rgb] as Peak['rgb'] }))
    let base = baseline.current
    let width = 0
    let height = 0
    let frame = 0
    const pointer = { x: -9999, y: -9999, s: 0, tx: -9999, ty: -9999, ts: 0 }

    const render = (time: number) => {
      const rate = reduced ? 1 : 0.045
      stepToward(peaks, target.current, rate)
      base += (baseline.current - base) * rate
      pointer.x += (pointer.tx - pointer.x) * 0.12
      pointer.y += (pointer.ty - pointer.y) * 0.12
      pointer.s += (pointer.ts - pointer.s) * 0.08
      drawSpectrum(context, {
        width,
        height,
        baseline: base,
        peaks,
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
      pointer.ts = pointer.tx >= 0 ? 1 : 0
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
