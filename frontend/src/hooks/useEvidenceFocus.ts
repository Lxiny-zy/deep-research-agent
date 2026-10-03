import { useEffect, useRef } from 'react'

/** Evidence is a reading companion: no scroll lock or focus trap. */
export function useEvidenceFocus(onClose: () => void, returnFocus?: () => HTMLElement | null) {
  const ref = useRef<HTMLElement>(null)
  const close = useRef(onClose)
  const target = useRef(returnFocus)
  close.current = onClose
  target.current = returnFocus
  useEffect(() => {
    const panel = ref.current
    const previous = document.activeElement as HTMLElement | null
    let restore = true
    const otherPanel = (event: Event) => {
      if ((event as CustomEvent).detail !== panel) {
        restore = false
        close.current()
      }
    }
    document.dispatchEvent(new CustomEvent('evidence-panel-open', { detail: panel }))
    document.addEventListener('evidence-panel-open', otherPanel)
    panel?.focus({ preventScroll: true })
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return
      event.preventDefault()
      close.current()
    }
    const outside = (event: MouseEvent) => {
      if (!(event.target instanceof Element) || panel?.contains(event.target)) return
      if (
        event.target.closest(
          '.cite-ref, .qa-inline-cite, .reference-evidence-button, .evidence-toolbar',
        )
      )
        return
      if (window.getSelection()?.isCollapsed === false) return
      restore = false
      close.current()
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('click', outside)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('click', outside)
      document.removeEventListener('evidence-panel-open', otherPanel)
      const active = document.activeElement
      const trigger = target.current?.() ?? previous
      if (restore && (panel?.contains(active) || active === document.body) && trigger?.isConnected)
        trigger.focus({ preventScroll: true })
    }
  }, [])
  return ref
}
