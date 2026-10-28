import { useEffect, useRef } from 'react'
import { drawIntro, introThreads } from '../lib/introRender'

/**
 * 欢迎画面的谱线画布。
 * - 谱线依次显影：先是光谱带，再是一条条谱线；
 * - `hot`：指针悬停「进入」时，下段谱线收紧、汇聚点变亮；
 * - `leaving`：离场时光谱带向上下铺满画面；
 * - 标签页隐藏时停帧，「减少动态效果」时直接画出完成态的一帧。
 */
export default function IntroSpectrum({
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
    const motion = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    let reduced = motion?.matches ?? false
    const threads = introThreads()
    let width = 0
    let height = 0
    let frame = 0
    let born = -1
    let energy = 0
    let burst = 0
    const pointer = { x: -9999, y: -9999, s: 0, tx: -9999, ty: -9999, ts: 0 }

    const render = (time: number) => {
      const s = state.current
      if (born < 0) born = time
      energy += ((s.hot ? 1 : 0) - energy) * 0.07
      burst += ((s.leaving ? 1 : 0) - burst) * 0.06
      pointer.x += (pointer.tx - pointer.x) * 0.1
      pointer.y += (pointer.ty - pointer.y) * 0.1
      pointer.s += (pointer.ts - pointer.s) * 0.06
      drawIntro(context, {
        width,
        height,
        time,
        elapsed: reduced ? 1e6 : time - born,
        energy: reduced ? 0 : energy,
        burst: reduced ? 0 : burst,
        pointer,
        dark: s.dark,
        threads,
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
      width = canvas.clientWidth
      height = canvas.clientHeight
      canvas.width = Math.max(1, Math.floor(width * dpr))
      canvas.height = Math.max(1, Math.floor(height * dpr))
      context.setTransform(dpr, 0, 0, dpr, 0, 0)
      if (reduced) render(0)
    }
    const onMove = (event: PointerEvent) => {
      if (reduced || event.pointerType === 'touch') return
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
    const onMotion = () => {
      reduced = motion?.matches ?? false
      start()
    }

    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(resize)
    observer?.observe(canvas)
    resize()
    start()
    window.addEventListener('pointermove', onMove, { passive: true })
    document.documentElement.addEventListener('pointerleave', onLeave)
    motion?.addEventListener?.('change', onMotion)
    document.addEventListener('visibilitychange', start)
    return () => {
      cancelAnimationFrame(frame)
      observer?.disconnect()
      window.removeEventListener('pointermove', onMove)
      document.documentElement.removeEventListener('pointerleave', onLeave)
      document.removeEventListener('visibilitychange', start)
      motion?.removeEventListener?.('change', onMotion)
    }
  }, [])

  return <canvas ref={ref} className="intro-spectrum" aria-hidden="true" />
}
