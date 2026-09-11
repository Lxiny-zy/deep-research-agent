import { useEffect, useState } from 'react'

const STORAGE_KEY = 'dr_ambient_motion_paused'
const CHANGE_EVENT = 'dr:ambient-motion'

function savedPreference(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'true'
  } catch {
    return false
  }
}

/** Keep decorative motion consistent across routes, tabs and system preferences. */
export function useAmbientMotion() {
  const [userPaused, setUserPaused] = useState(savedPreference)
  const [reduced, setReduced] = useState(
    () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false,
  )
  const [hidden, setHidden] = useState(() => document.hidden)

  useEffect(() => {
    const media = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    const onMedia = () => setReduced(media?.matches ?? false)
    const onVisibility = () => setHidden(document.hidden)
    const onStorage = (event: StorageEvent) => {
      if (event.key === STORAGE_KEY || event.key === null) setUserPaused(savedPreference())
    }
    const onChange = (event: Event) => setUserPaused((event as CustomEvent<boolean>).detail)
    media?.addEventListener('change', onMedia)
    document.addEventListener('visibilitychange', onVisibility)
    window.addEventListener('storage', onStorage)
    window.addEventListener(CHANGE_EVENT, onChange)
    return () => {
      media?.removeEventListener('change', onMedia)
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('storage', onStorage)
      window.removeEventListener(CHANGE_EVENT, onChange)
    }
  }, [])

  const setPaused = (value: boolean) => {
    setUserPaused(value)
    try {
      localStorage.setItem(STORAGE_KEY, String(value))
    } catch {
      // Playback controls also work when browser storage is unavailable.
    }
    window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: value }))
  }
  const paused = reduced || userPaused
  return {
    paused,
    reduced,
    inactive: paused || hidden,
    setPaused,
    toggle: () => {
      if (!reduced) setPaused(!userPaused)
    },
  }
}
