import { useEffect, useRef, useState } from 'react'
import type { ResearchEvent } from '../types'

/** Batch display snapshots without changing events or request state. */
export function useReasoningFrames(events: ResearchEvent[], live: boolean) {
  const [frame, setFrame] = useState(events)
  const latest = useRef(events)
  const timer = useRef<ReturnType<typeof setTimeout>>()
  useEffect(() => {
    latest.current = events
    if (!live || events.length === 0) {
      clearTimeout(timer.current)
      timer.current = undefined
      setFrame(events)
    } else if (timer.current === undefined) {
      // Fixed deadline, not debounce: continuous tokens cannot starve display.
      timer.current = setTimeout(() => {
        timer.current = undefined
        setFrame(latest.current)
      }, 80)
    }
  }, [events, live])
  useEffect(() => () => {
    clearTimeout(timer.current)
    timer.current = undefined
  }, [])
  return !live || events.length === 0 ? events : frame
}
