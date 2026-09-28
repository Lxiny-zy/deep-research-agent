import { useCallback, useEffect, useState } from 'react'

const THEME_KEY = 'sr_theme'
type Theme = 'light' | 'dark'

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

/** 浅色 / 深色主题：默认跟随系统，用户手动切换后记住选择。 */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>(initialTheme)
  useEffect(() => {
    applyTheme(theme)
  }, [theme])
  const toggle = useCallback(() => {
    setTheme((current) => {
      const next = current === 'dark' ? 'light' : 'dark'
      try {
        localStorage.setItem(THEME_KEY, next)
      } catch {
        // 只影响「记住选择」
      }
      return next
    })
  }, [])
  return { theme, dark: theme === 'dark', toggle }
}
