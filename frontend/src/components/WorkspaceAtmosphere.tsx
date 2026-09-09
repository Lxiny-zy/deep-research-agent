import { useEffect, useRef } from 'react'
import ResearchMotif, { type MotifKind } from './ResearchMotif'

function AmbientParticles({ paused }: { paused: boolean }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const elapsedRef = useRef(0)
  useEffect(() => {
    const canvas = ref.current
    if (!canvas || typeof window.CanvasRenderingContext2D === 'undefined') return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)')
    let width = 1
    let height = 1
    let frame = 0
    let last = 0
    let elapsed = elapsedRef.current
    // Deterministic positions prevent a distracting reshuffle when playback is toggled.
    const particles = Array.from({ length: 52 }, (_, i) => ({
      x: ((i * 73 + 17) % 101) / 101,
      y: ((i * 43 + 7) % 103) / 103,
      radius: i % 7 === 0 ? 1.7 : 0.7 + (i % 3) * 0.25,
      speed: 2 + (i % 5) * 0.65,
      phase: i * 2.4,
    }))
    const draw = () => {
      ctx.clearRect(0, 0, width, height)
      const count = width < 760 ? 22 : particles.length
      particles.slice(0, count).forEach((p, i) => {
        const x = p.x * width + Math.sin(elapsed * 0.09 + p.phase) * 12
        const y = (((p.y * height - elapsed * p.speed) % height) + height) % height
        const alpha = 0.2 + (Math.sin(elapsed * 0.23 + p.phase) + 1) * 0.12
        ctx.beginPath()
        ctx.fillStyle = i % 8 === 0 ? `rgba(227,189,137,${alpha})` : `rgba(169,230,208,${alpha})`
        ctx.arc(x, y, p.radius, 0, Math.PI * 2)
        ctx.fill()
        if (p.radius > 1.5) {
          const glow = ctx.createRadialGradient(x, y, 0, x, y, 7)
          glow.addColorStop(0, `rgba(145,216,189,${alpha * 0.32})`)
          glow.addColorStop(1, 'rgba(145,216,189,0)')
          ctx.fillStyle = glow
          ctx.fillRect(x - 7, y - 7, 14, 14)
        }
      })
    }
    const tick = (now: number) => {
      frame = 0
      if (paused || motion.matches || document.hidden) return
      if (now - last >= 32) {
        elapsed += Math.min((now - last) / 1000, 0.1)
        last = now
        draw()
      }
      frame = requestAnimationFrame(tick)
    }
    const sync = () => {
      cancelAnimationFrame(frame)
      frame = 0
      if (!paused && !motion.matches && !document.hidden) {
        last = performance.now()
        frame = requestAnimationFrame(tick)
      }
    }
    const resize = () => {
      width = Math.max(canvas.clientWidth, 1)
      height = Math.max(canvas.clientHeight, 1)
      const ratio = Math.min(window.devicePixelRatio || 1, 1.5)
      canvas.width = Math.round(width * ratio)
      canvas.height = Math.round(height * ratio)
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
      draw()
    }
    const observer = new ResizeObserver(resize)
    observer.observe(canvas)
    motion.addEventListener('change', sync)
    document.addEventListener('visibilitychange', sync)
    resize()
    sync()
    return () => {
      elapsedRef.current = elapsed
      cancelAnimationFrame(frame)
      observer.disconnect()
      motion.removeEventListener('change', sync)
      document.removeEventListener('visibilitychange', sync)
    }
  }, [paused])
  return <canvas className="ambient-particles" ref={ref} aria-hidden="true" />
}

export default function WorkspaceAtmosphere({
  kind,
  paused,
}: {
  kind: MotifKind
  paused: boolean
}) {
  return (
    <div className={`workspace-atmosphere atmosphere-${kind}`} aria-hidden="true">
      <div className="atmosphere-light" />
      <ResearchMotif kind={kind} className="ambient-motif ambient-motif-near" />
      <ResearchMotif
        kind={kind === 'orbit' ? 'archive' : 'orbit'}
        className="ambient-motif ambient-motif-far"
      />
      <AmbientParticles paused={paused} />
    </div>
  )
}
