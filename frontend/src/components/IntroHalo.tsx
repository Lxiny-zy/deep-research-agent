import { useEffect, useRef } from 'react'
import { drawHalo } from '../lib/haloRender'

/** 画布边长 = 光环直径 × 1.9（见 intro-halo.css）；光环细线在画布半径的这个比例处 */
const RING_RATIO = 0.972 / 1.9

/**
 * 欢迎光环外沿的动态光谱画布。
 * - `hot`：指针悬停「进入」时峰更高、光尘飘得更远；
 * - `leaving`：离场时整圈光谱向外爆发；
 * - 指针方向会在环上隆起一座峰；标签页隐藏时停帧，「减少动态效果」时只画一帧。
 */
export default function IntroHalo({
  hot,
  leaving,
  dark,
}: {
  hot: boolean
  leaving: boolean
  dark: boolean
}) {
  const ref = useRef<HTMLCanvasElement>(null)
  const state = useRef({ hot, leaving, dark })
  state.current = { hot, leaving, dark }

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    let ctx: CanvasRenderingContext2D | null = null
    try {
      ctx = canvas.getContext('2d')
    } catch {
      ctx = null
    }
    if (!ctx) return
    const context = ctx
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    let size = 0
    let frame = 0
    let energy = 0
    let burst = 0
    const pointer = { angle: 0, s: 0, ts: 0, ta: 0 }

    const render = (time: number) => {
      const s = state.current
      energy += ((s.hot ? 1 : 0) - energy) * 0.06
      burst += ((s.leaving ? 1 : 0) - burst) * 0.05
      pointer.s += (pointer.ts - pointer.s) * 0.06
      // 沿最短方向转向目标角，避免跨过 ±π 时整圈甩动
      let d = pointer.ta - pointer.angle
      d = Math.atan2(Math.sin(d), Math.cos(d))
      pointer.angle += d * 0.1
      drawHalo(context, {
        size,
        radius: (size / 2) * RING_RATIO,
        time,
        energy,
        burst,
        pointer,
        dark: s.dark,
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
    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      size = canvas.clientWidth
      canvas.width = Math.max(1, Math.floor(size * dpr))
      canvas.height = canvas.width
      context.setTransform(dpr, 0, 0, dpr, 0, 0)
      if (reduced) render(0)
    }
    const onMove = (event: PointerEvent) => {
      const rect = canvas.getBoundingClientRect()
      const dx = event.clientX - (rect.left + rect.width / 2)
      const dy = event.clientY - (rect.top + rect.height / 2)
      pointer.ta = Math.atan2(dy, dx)
      pointer.ts = 1
    }
    const onLeave = () => {
      pointer.ts = 0
    }

    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(resize)
    observer?.observe(canvas)
    resize()
    start()
    if (!reduced) {
      window.addEventListener('pointermove', onMove, { passive: true })
      document.documentElement.addEventListener('pointerleave', onLeave)
    }
    document.addEventListener('visibilitychange', start)
    return () => {
      cancelAnimationFrame(frame)
      observer?.disconnect()
      window.removeEventListener('pointermove', onMove)
      document.documentElement.removeEventListener('pointerleave', onLeave)
      document.removeEventListener('visibilitychange', start)
    }
  }, [])

  return <canvas ref={ref} className="intro-halo" aria-hidden="true" />
}
