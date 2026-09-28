import { useEffect } from 'react'

/** 光谱色的份数，与 styles/prism/button-hues.css 里的 [data-hue] 规则一一对应 */
export const HUE_COUNT = 7

/** 按按钮文字（或无障碍名称）取一个稳定的色号：同一个按钮在哪个页面都是同一种颜色 */
export function hueFor(label: string) {
  let hash = 0
  for (const char of label.trim()) hash = (hash * 31 + char.codePointAt(0)!) | 0
  return Math.abs(hash) % HUE_COUNT
}

function paint(root: ParentNode) {
  root.querySelectorAll<HTMLElement>('.btn:not([data-hue])').forEach((button) => {
    const label = button.getAttribute('aria-label') || button.textContent || ''
    button.dataset.hue = String(hueFor(label))
  })
}

/**
 * 给页面上的每个 `.btn` 标一个光谱色号（data-hue），悬停线条、描边、光环都用这一色。
 * 用 MutationObserver 跟进后续渲染出来的按钮，同一帧内的多次变动只处理一次。
 */
export function useButtonHues() {
  useEffect(() => {
    if (typeof MutationObserver === 'undefined') return
    let frame = 0
    const schedule = () => {
      if (frame) return
      frame = requestAnimationFrame(() => {
        frame = 0
        paint(document)
      })
    }
    paint(document)
    const observer = new MutationObserver(schedule)
    observer.observe(document.body, { childList: true, subtree: true })
    return () => {
      observer.disconnect()
      cancelAnimationFrame(frame)
    }
  }, [])
}
