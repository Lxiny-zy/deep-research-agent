import { useEffect, useRef } from 'react'
import { drawIntro, introGraph } from '../lib/introRender'

/**
 * 欢迎画面的扉页插图画布（文献引用网络，见 lib/introRender）。
 * - 节点依次出现、引用边逐条生长，已核验的节点最后汇向「进入」；
 * - `hot`：指针悬停「进入」时，汇聚线加深；
 * - `leaving`：离场时整张网络在约 0.45 秒内收拢进「进入」（随后由样式层从这一点打开圆孔）；
 * - 标签页隐藏时停帧，「减少动态效果」时直接画出完成态的一帧。
 */
export default function IntroPlate({
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
    const graph = introGraph()
    let width = 0
    let height = 0
    let frame = 0
    let born = -1
    let energy = 0
    let leaveAt = -1

    const render = (time: number) => {
      const s = state.current
      if (born < 0) born = time
      energy += ((s.hot ? 1 : 0) - energy) * 0.07
      if (s.leaving && leaveAt < 0) leaveAt = time
      if (!s.leaving) leaveAt = -1
      const t = leaveAt < 0 ? 0 : Math.min(1, (time - leaveAt) / 450)
      const gather = t * t * (3 - 2 * t)
      drawIntro(context, {
        width,
        height,
        elapsed: reduced ? 1e6 : time - born,
        energy: reduced ? 0 : energy,
        gather: reduced ? 0 : gather,
        dark: s.dark,
        graph,
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
    const onMotion = () => {
      reduced = motion?.matches ?? false
      start()
    }

    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(resize)
    observer?.observe(canvas)
    resize()
    start()
    motion?.addEventListener?.('change', onMotion)
    document.addEventListener('visibilitychange', start)
    return () => {
      cancelAnimationFrame(frame)
      observer?.disconnect()
      document.removeEventListener('visibilitychange', start)
      motion?.removeEventListener?.('change', onMotion)
    }
  }, [])

  return <canvas ref={ref} className="intro-plate" aria-hidden="true" />
}
