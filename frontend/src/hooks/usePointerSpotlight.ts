import { useEffect } from 'react'

/**
 * 指针聚光：把鼠标在卡片内的位置写成 CSS 变量（--mx / --my），
 * 由样式层画出跟随指针的光斑与边框高光。
 *
 * 用一个 document 级委托监听，而不是给每张卡片挂事件：
 * 卡片数量随页面变化，委托不需要重新绑定；rAF 合并同一帧内的多次移动。
 */
const SELECTOR =
  '.panel, .task-card, .home-card, .home-config, .catalog-card, .builtin-card, .qa-starter, .live-metric, .library-project, .history-run-row, .welcome-feature, .spot'

export function usePointerSpotlight() {
  useEffect(() => {
    if (window.matchMedia?.('(pointer: coarse)').matches) return
    let pending: PointerEvent | null = null
    let frame = 0

    const apply = () => {
      frame = 0
      const event = pending
      pending = null
      if (!event) return
      const target = (event.target as Element | null)?.closest?.(SELECTOR) as HTMLElement | null
      if (!target) return
      const rect = target.getBoundingClientRect()
      target.style.setProperty('--mx', `${event.clientX - rect.left}px`)
      target.style.setProperty('--my', `${event.clientY - rect.top}px`)
    }

    const onMove = (event: PointerEvent) => {
      pending = event
      if (!frame) frame = requestAnimationFrame(apply)
    }

    document.addEventListener('pointermove', onMove, { passive: true })
    return () => {
      document.removeEventListener('pointermove', onMove)
      if (frame) cancelAnimationFrame(frame)
    }
  }, [])
}
