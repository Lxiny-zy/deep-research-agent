import { useEffect, useRef, type RefObject } from 'react'

export function useModalFocus(ref: RefObject<HTMLElement>, onClose: () => void) {
  const close = useRef(onClose)
  close.current = onClose
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const focusable = () =>
      [
        ...(ref.current?.querySelectorAll<HTMLElement>(
          'a[href], button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), summary, [tabindex="0"]',
        ) ?? []),
      ].filter((element) => {
        for (let parent: HTMLElement | null = element; parent; parent = parent.parentElement) {
          const style = getComputedStyle(parent)
          if (
            parent.hidden ||
            parent.inert ||
            style.display === 'none' ||
            style.visibility === 'hidden'
          )
            return false
          if (parent instanceof HTMLDetailsElement && !parent.open) {
            const summary = parent.querySelector('summary')
            if (!summary?.contains(element)) return false
          }
          if (parent === ref.current) break
        }
        return true
      })
    focusable()[0]?.focus()
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        close.current()
        return
      }
      if (event.key !== 'Tab') return
      const elements = focusable()
      const first = elements[0],
        last = elements[elements.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last?.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first?.focus()
      }
    }
    const focusin = (event: FocusEvent) => {
      if (ref.current && event.target instanceof Node && !ref.current.contains(event.target))
        focusable()[0]?.focus()
    }
    document.addEventListener('keydown', keydown)
    document.addEventListener('focusin', focusin)
    return () => {
      document.removeEventListener('keydown', keydown)
      document.removeEventListener('focusin', focusin)
      document.body.style.overflow = overflow
      previous?.focus()
    }
  }, [ref])
}
