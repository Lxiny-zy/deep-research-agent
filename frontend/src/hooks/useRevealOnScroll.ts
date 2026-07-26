import { useEffect, type RefObject } from 'react'

/**
 * 滚动驱动 reveal：观察容器内所有 [data-reveal] 元素，进入视口时打上
 * `is-revealed`，由 experience.css 中的过渡完成 translateY + opacity 入场。
 *
 * 克制原则：只做一次性的进入动效，不做持续视差；
 * - prefers-reduced-motion 或缺少 IntersectionObserver（旧浏览器 / jsdom）时
 *   直接标记为已显示，内容零延迟可见。
 * - 若目标元素是异步渲染的（如等待接口返回后才出现的面板），把触发其
 *   渲染的数据放进 deps，让 hook 重新扫描；已 reveal 的元素保留 class 不回退。
 */
export function useRevealOnScroll(ref: RefObject<HTMLElement | null>, deps: readonly unknown[] = []) {
  useEffect(() => {
    const root = ref.current
    if (!root) return
    const items = Array.from(root.querySelectorAll<HTMLElement>('[data-reveal]'))
    if (items.length === 0) return

    const reduceMotion =
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduceMotion || typeof IntersectionObserver === 'undefined') {
      items.forEach((item) => item.classList.add('is-revealed'))
      return
    }

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue
          entry.target.classList.add('is-revealed')
          observer.unobserve(entry.target)
        }
      },
      { rootMargin: '0px 0px -8% 0px', threshold: 0.06 },
    )
    items.forEach((item) => observer.observe(item))
    return () => observer.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ref, ...deps])
}
