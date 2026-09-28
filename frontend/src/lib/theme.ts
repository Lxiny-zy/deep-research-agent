import { useCallback, useEffect, useRef, useState } from 'react'
import { flushSync } from 'react-dom'

const THEME_KEY = 'sr_theme'
export type Theme = 'light' | 'dark'

function initialTheme(): Theme {
  try {
    const stored = localStorage.getItem(THEME_KEY)
    if (stored === 'light' || stored === 'dark') return stored
  } catch {
    // 存储不可用：跟随系统
  }
  return typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-color-scheme: dark)').matches
    ? 'dark'
    : 'light'
}

export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme
}

function persist(theme: Theme) {
  try {
    localStorage.setItem(THEME_KEY, theme)
  } catch {
    // 只影响「记住选择」
  }
}

type ViewTransitionDocument = Document & {
  startViewTransition?: (update: () => void) => { ready: Promise<void> }
}

/**
 * 切换主题的过渡画面：
 * - 支持 View Transitions 的浏览器：新主题从切换按钮处以圆形向外揭开；
 * - 不支持时：整页颜色交叉淡入（html.theme-fading 打开颜色过渡）；
 * - 「减少动态效果」时直接切换。
 */
export function transitionTheme(
  next: Theme,
  commit: () => void,
  origin?: { x: number; y: number },
) {
  const root = document.documentElement
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
  const doc = document as ViewTransitionDocument
  if (reduced) {
    commit()
    return
  }
  if (!doc.startViewTransition) {
    root.classList.add('theme-fading')
    commit()
    window.setTimeout(() => root.classList.remove('theme-fading'), 600)
    return
  }
  const x = origin?.x ?? window.innerWidth - 80
  const y = origin?.y ?? 30
  const radius = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y))
  root.dataset.themeTo = next
  const transition = doc.startViewTransition(commit)
  transition.ready
    .then(() => {
      root.animate(
        { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
        {
          duration: 820,
          easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)',
          pseudoElement: '::view-transition-new(root)',
        },
      )
    })
    .catch(() => undefined)
    .finally(() => window.setTimeout(() => delete root.dataset.themeTo, 900))
}

/** 浅色 / 深色主题：默认跟随系统，用户手动切换后记住选择。 */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>(initialTheme)
  const current = useRef(theme)
  current.current = theme
  useEffect(() => {
    applyTheme(theme)
  }, [theme])
  /** origin：切换按钮中心的视口坐标，过渡从这里扩散。 */
  const toggle = useCallback((origin?: { x: number; y: number }) => {
    const next: Theme = current.current === 'dark' ? 'light' : 'dark'
    persist(next)
    transitionTheme(
      next,
      () => {
        applyTheme(next)
        flushSync(() => setTheme(next))
      },
      origin,
    )
  }, [])
  return { theme, dark: theme === 'dark', toggle }
}
